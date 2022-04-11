# Copyright (C) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (C) 2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Common utilities used in testing"""

import glob
import itertools
import kubernetes.config.kube_config
import logging
import os
import random
import select
import string
import socket
import subprocess
import threading
import tempfile
import time

import fixtures
import kazoo.client
import testtools

from nodepool import builder
from nodepool import launcher
from nodepool import webapp
from nodepool import zk
from nodepool.cmd.config_validator import ConfigValidator
from nodepool.nodeutils import iterate_timeout

TRUE_VALUES = ('true', '1', 'yes')
SECOND = 1
ONE_MINUTE = 60 * SECOND


class LoggingPopen(subprocess.Popen):
    pass


class ZookeeperServerFixture(fixtures.Fixture):
    def _setUp(self):
        zk_host = os.environ.get('NODEPOOL_ZK_HOST', 'localhost')
        if ':' in zk_host:
            host, port = zk_host.split(':')
        else:
            host = zk_host
            port = None

        self.zookeeper_host = host

        if not port:
            self.zookeeper_port = 2281
        else:
            self.zookeeper_port = int(port)

        zk_ca = os.environ.get('NODEPOOL_ZK_CA', None)
        if not zk_ca:
            zk_ca = os.path.join(os.path.dirname(__file__),
                                 '../../tools/ca/certs/cacert.pem')
        self.zookeeper_ca = zk_ca
        zk_cert = os.environ.get('NODEPOOL_ZK_CERT', None)
        if not zk_cert:
            zk_cert = os.path.join(os.path.dirname(__file__),
                                   '../../tools/ca/certs/client.pem')
        self.zookeeper_cert = zk_cert
        zk_key = os.environ.get('NODEPOOL_ZK_KEY', None)
        if not zk_key:
            zk_key = os.path.join(os.path.dirname(__file__),
                                  '../../tools/ca/keys/clientkey.pem')
        self.zookeeper_key = zk_key


class ChrootedKazooFixture(fixtures.Fixture):
    def __init__(self, zookeeper_host, zookeeper_port, zookeeper_ca,
                 zookeeper_cert, zookeeper_key):
        super(ChrootedKazooFixture, self).__init__()
        self.zk_args = dict(
            hosts='%s:%s' % (zookeeper_host, zookeeper_port),
            use_ssl=True,
            ca=zookeeper_ca,
            certfile=zookeeper_cert,
            keyfile=zookeeper_key)

    def _setUp(self):
        # Make sure the test chroot paths do not conflict
        random_bits = ''.join(random.choice(string.ascii_lowercase +
                                            string.ascii_uppercase)
                              for x in range(8))

        rand_test_path = '%s_%s' % (random_bits, os.getpid())
        self.zookeeper_chroot = "/nodepool_test/%s" % rand_test_path

        # Ensure the chroot path exists and clean up any pre-existing znodes.
        _tmp_client = kazoo.client.KazooClient(**self.zk_args)
        _tmp_client.start()

        if _tmp_client.exists(self.zookeeper_chroot):
            _tmp_client.delete(self.zookeeper_chroot, recursive=True)

        _tmp_client.ensure_path(self.zookeeper_chroot)
        _tmp_client.stop()
        _tmp_client.close()

        self.addCleanup(self._cleanup)

    def _cleanup(self):
        '''Remove the chroot path.'''
        # Need a non-chroot'ed client to remove the chroot path
        _tmp_client = kazoo.client.KazooClient(**self.zk_args)
        _tmp_client.start()
        _tmp_client.delete(self.zookeeper_chroot, recursive=True)
        _tmp_client.stop()
        _tmp_client.close()


class StatsdFixture(fixtures.Fixture):
    def _setUp(self):
        self.running = True
        self.thread = threading.Thread(target=self.run)
        self.thread.daemon = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.port = self.sock.getsockname()[1]
        self.wake_read, self.wake_write = os.pipe()
        self.stats = []
        self.thread.start()
        self.addCleanup(self._cleanup)

    def run(self):
        while self.running:
            poll = select.poll()
            poll.register(self.sock, select.POLLIN)
            poll.register(self.wake_read, select.POLLIN)
            ret = poll.poll()
            for (fd, event) in ret:
                if fd == self.sock.fileno():
                    data = self.sock.recvfrom(1024)
                    if not data:
                        return
                    self.stats.append(data[0])
                if fd == self.wake_read:
                    return

    def _cleanup(self):
        self.running = False
        os.write(self.wake_write, b'1\n')
        self.thread.join()


class BaseTestCase(testtools.TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()
        test_timeout = os.environ.get('OS_TEST_TIMEOUT', 60)
        try:
            test_timeout = int(test_timeout)
        except ValueError:
            # If timeout value is invalid, fail hard.
            print("OS_TEST_TIMEOUT set to invalid value"
                  " defaulting to no timeout")
            test_timeout = 0
        if test_timeout > 0:
            self.useFixture(fixtures.Timeout(test_timeout, gentle=True))
            self.useFixture(fixtures.Timeout(test_timeout + 20, gentle=False))

        if os.environ.get('OS_STDOUT_CAPTURE') in TRUE_VALUES:
            stdout = self.useFixture(fixtures.StringStream('stdout')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stdout', stdout))
        if os.environ.get('OS_STDERR_CAPTURE') in TRUE_VALUES:
            stderr = self.useFixture(fixtures.StringStream('stderr')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stderr', stderr))
        if os.environ.get('OS_LOG_CAPTURE') in TRUE_VALUES:
            fs = '%(asctime)s %(levelname)s [%(name)s] %(message)s'
            self.useFixture(fixtures.FakeLogger(level=logging.DEBUG,
                                                format=fs))
        else:
            logging.basicConfig(level=logging.DEBUG)
        l = logging.getLogger('kazoo')
        l.setLevel(logging.INFO)
        l.propagate = False
        l = logging.getLogger('stevedore')
        l.setLevel(logging.INFO)
        l.propagate = False
        l = logging.getLogger('botocore')
        l.setLevel(logging.INFO)
        l.propagate = False
        l = logging.getLogger('boto3')
        l.setLevel(logging.INFO)
        l.propagate = False
        self.useFixture(fixtures.NestedTempfile())

        self.subprocesses = []

        def LoggingPopenFactory(*args, **kw):
            p = LoggingPopen(*args, **kw)
            self.subprocesses.append(p)
            return p

        self.statsd = StatsdFixture()
        self.useFixture(self.statsd)

        # note, use 127.0.0.1 rather than localhost to avoid getting ipv6
        # see: https://github.com/jsocol/pystatsd/issues/61
        os.environ['STATSD_HOST'] = '127.0.0.1'
        os.environ['STATSD_PORT'] = str(self.statsd.port)

        self.useFixture(fixtures.MonkeyPatch('subprocess.Popen',
                                             LoggingPopenFactory))
        self.setUpFakes()

        self.addCleanup(self._cleanup)

    def _cleanup(self):
        # This is a hack to cleanup kubernetes temp files during test runs.
        # The kube_config maintains a global dict of temporary files. During
        # running the tests those can get deleted during the cleanup phase of
        # the tests without kube_config knowing about this so forcefully tell
        # kube_config to clean this up.
        kubernetes.config.kube_config._cleanup_temp_files()

    def setUpFakes(self):
        clouds_path = os.path.join(os.path.dirname(__file__),
                                   'fixtures', 'clouds.yaml')
        self.useFixture(fixtures.MonkeyPatch(
            'openstack.config.loader.CONFIG_FILES', [clouds_path]))

    def wait_for_threads(self):
        # Wait until all transient threads (node launches, deletions,
        # etc.) are all complete.  Whitelist any long-running threads.
        whitelist = ['MainThread',
                     'NodePool',
                     'NodePool Builder',
                     'fake-provider',
                     'fake-provider1',
                     'fake-provider2',
                     'fake-provider3',
                     'CleanupWorker',
                     'DeletedNodeWorker',
                     'ServerListWatcher',
                     'StatsWorker',
                     'pydevd.CommandThread',
                     'pydevd.Reader',
                     'pydevd.Writer',
                     ]

        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Transient threads to finish",
                                 interval=0.1):
            done = True
            for t in threading.enumerate():
                if t.name.startswith("Thread-"):
                    # Kazoo
                    continue
                if t.name.startswith("worker "):
                    # paste web server
                    continue
                if t.name.startswith("UploadWorker"):
                    continue
                if t.name.startswith("BuildWorker"):
                    continue
                if t.name.startswith("CleanupWorker"):
                    continue
                if t.name.startswith("PoolWorker"):
                    continue
                if t.name not in whitelist:
                    done = False
            if done:
                return

    def assertReportedStat(self, key, value=None, kind=None):
        """Check statsd output

        Check statsd return values.  A ``value`` should specify a
        ``kind``, however a ``kind`` may be specified without a
        ``value`` for a generic match.  Leave both empy to just check
        for key presence.

        :arg str key: The statsd key
        :arg str value: The expected value of the metric ``key``
        :arg str kind: The expected type of the metric ``key``  For example

          - ``c`` counter
          - ``g`` gauge
          - ``ms`` timing
          - ``s`` set
        """

        if value:
            self.assertNotEqual(kind, None)

        for _ in iterate_timeout(5 * SECOND, Exception,
                                 "Find statsd event",
                                 interval=0.1):
            # Note our fake statsd just queues up results in a queue.
            # We just keep going through them until we find one that
            # matches, or fail out.  If statsd pipelines are used,
            # large single packets are sent with stats separated by
            # newlines; thus we first flatten the stats out into
            # single entries.
            stats = itertools.chain.from_iterable(
                [s.decode('utf-8').split('\n') for s in self.statsd.stats])
            for stat in stats:
                k, v = stat.split(':')
                if key == k:
                    if kind is None:
                        # key with no qualifiers is found
                        return True

                    s_value, s_kind = v.split('|')

                    # if no kind match, look for other keys
                    if kind != s_kind:
                        continue

                    if value:
                        # special-case value|ms because statsd can turn
                        # timing results into float of indeterminate
                        # length, hence foiling string matching.
                        if kind == 'ms':
                            if float(value) == float(s_value):
                                return True
                        if value == s_value:
                            return True
                        # otherwise keep looking for other matches
                        continue

                    # this key matches
                    return True

        raise Exception("Key %s not found in reported stats" % key)


class BuilderFixture(fixtures.Fixture):
    log = logging.getLogger("tests.BuilderFixture")

    def __init__(self, configfile, cleanup_interval, securefile=None,
                 num_uploaders=1):
        super(BuilderFixture, self).__init__()
        self.configfile = configfile
        self.securefile = securefile
        self.cleanup_interval = cleanup_interval
        self.builder = None
        self.num_uploaders = num_uploaders

    def setUp(self):
        super(BuilderFixture, self).setUp()
        self.builder = builder.NodePoolBuilder(
            self.configfile, secure_path=self.securefile,
            num_uploaders=self.num_uploaders)
        self.builder.cleanup_interval = self.cleanup_interval
        self.builder.build_interval = .1
        self.builder.upload_interval = .1
        self.builder.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # The NodePoolBuilder.stop() method does not intentionally stop the
        # upload workers for reasons documented in that method. But we can
        # safely do so in tests.
        for worker in self.builder._upload_workers:
            worker.shutdown()
            worker.join()
            self.log.debug("Stopped worker %s", worker.name)
        self.builder.stop()


class DBTestCase(BaseTestCase):
    def setUp(self):
        super(DBTestCase, self).setUp()
        self.log = logging.getLogger("tests")
        self.setupZK()

    def setup_config(self, filename, images_dir=None, **kw):
        if images_dir is None:
            images_dir = fixtures.TempDir()
            self.useFixture(images_dir)
        build_log_dir = fixtures.TempDir()
        self.useFixture(build_log_dir)

        format_dict = dict(
            images_dir=images_dir.path,
            build_log_dir=build_log_dir.path,
            zookeeper_host=self.zookeeper_host,
            zookeeper_port=self.zookeeper_port,
            zookeeper_chroot=self.zookeeper_chroot,
            zookeeper_ca=self.zookeeper_ca,
            zookeeper_cert=self.zookeeper_cert,
            zookeeper_key=self.zookeeper_key
        )
        format_dict.update(kw)

        if filename.startswith('/'):
            path = filename
        else:
            configfile = os.path.join(os.path.dirname(__file__),
                                      'fixtures', filename)
            (fd, path) = tempfile.mkstemp()
            with open(configfile, 'rb') as conf_fd:
                config = conf_fd.read().decode('utf8')
                data = config.format(**format_dict)
                os.write(fd, data.encode('utf8'))
            os.close(fd)
        self._config_images_dir = images_dir
        self._config_build_log_dir = build_log_dir
        validator = ConfigValidator(path)
        ret = validator.validate()
        if ret != 0:
            raise ValueError("Config file %s could not be validated" % path)
        return path

    def replace_config(self, configfile, filename):
        self.log.debug("Replacing config with %s", filename)
        new_configfile = self.setup_config(filename, self._config_images_dir)
        os.rename(new_configfile, configfile)

    def setup_secure(self, filename):
        # replace entries in secure.conf
        configfile = os.path.join(os.path.dirname(__file__),
                                  'fixtures', filename)
        (fd, path) = tempfile.mkstemp()
        with open(configfile, 'rb') as conf_fd:
            config = conf_fd.read().decode('utf8')
            data = config.format(
                zookeeper_host=self.zookeeper_host,
                zookeeper_port=self.zookeeper_port,
                zookeeper_chroot=self.zookeeper_chroot,
                zookeeper_ca=self.zookeeper_ca,
                zookeeper_cert=self.zookeeper_cert,
                zookeeper_key=self.zookeeper_key)
            os.write(fd, data.encode('utf8'))
        os.close(fd)
        return path

    def wait_for_config(self, pool):
        for x in range(300):
            if pool.config is not None:
                return
            time.sleep(0.1)

    def waitForImage(self, provider_name, image_name, ignore_list=None):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Image upload to be ready",
                                 interval=1):
            self.wait_for_threads()
            image = self.zk.getMostRecentImageUpload(image_name, provider_name)
            if image:
                if ignore_list and image not in ignore_list:
                    break
                elif not ignore_list:
                    break
        self.wait_for_threads()
        return image

    def waitForUploadRecordDeletion(self, provider_name, image_name,
                                    build_id, upload_id):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Image upload record deletion",
                                 interval=1):
            self.wait_for_threads()
            uploads = self.zk.getUploads(image_name, build_id, provider_name)
            if not uploads or upload_id not in [u.id for u in uploads]:
                break
        self.wait_for_threads()

    def waitForImageDeletion(self, provider_name, image_name, match=None):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Image upload deletion",
                                 interval=1):
            self.wait_for_threads()
            image = self.zk.getMostRecentImageUpload(image_name, provider_name)
            if not image or (match and image != match):
                break
        self.wait_for_threads()

    def waitForBuild(self, image_name, build_id, states=None):
        if states is None:
            states = (zk.READY,)

        base = "-".join([image_name, build_id])

        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Image build record to reach state",
                                 interval=1):
            self.wait_for_threads()
            build = self.zk.getBuild(image_name, build_id)
            if build and build.state in states:
                break

        # We should only expect a dib manifest with a successful build.
        while build.state == zk.READY:
            self.wait_for_threads()
            files = builder.DibImageFile.from_image_id(
                self._config_images_dir.path, base)
            if files:
                break
            time.sleep(1)

        self.wait_for_threads()
        return build

    def waitForBuildDeletion(self, image_name, build_id):
        base = "-".join([image_name, build_id])
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "DIB build files deletion",
                                 interval=1):
            self.wait_for_threads()
            files = builder.DibImageFile.from_image_id(
                self._config_images_dir.path, base)
            if not files:
                break

        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "DIB build file deletion leaks",
                                 interval=1):
            self.wait_for_threads()
            # Now, check the disk to ensure we didn't leak any files.
            matches = glob.glob('%s/%s.*' % (self._config_images_dir.path,
                                             base))
            if not matches:
                break

        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Image build record deletion",
                                 interval=1):
            self.wait_for_threads()
            build = self.zk.getBuild(image_name, build_id)
            if not build:
                break

        self.wait_for_threads()

    def waitForNodeDeletion(self, node):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Node record deletion",
                                 interval=1):
            exists = False
            for n in self.zk.nodeIterator():
                if node.id == n.id:
                    exists = True
                    break
            if not exists:
                break

    def waitForInstanceDeletion(self, manager, instance_id):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Cloud instance deletion",
                                 interval=1):
            servers = manager.listNodes()
            if not (instance_id in [s.id for s in servers]):
                break

    def waitForNodeRequestLockDeletion(self, request_id):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Node request lock deletion",
                                 interval=1):
            exists = False
            for lock_id in self.zk.getNodeRequestLockIDs():
                if request_id == lock_id:
                    exists = True
                    break
            if not exists:
                break

    def waitForNodes(self, label, count=1):
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Ready nodes",
                                 interval=1):
            self.wait_for_threads()
            ready_nodes = self.zk.getReadyNodesOfTypes([label])
            if label in ready_nodes and len(ready_nodes[label]) == count:
                break
        self.wait_for_threads()
        return ready_nodes[label]

    def waitForNodeRequest(self, req, states=None):
        '''
        Wait for a node request to transition to a final state.
        '''
        if states is None:
            states = (zk.FULFILLED, zk.FAILED)
        for _ in iterate_timeout(ONE_MINUTE, Exception,
                                 "Node request state transition",
                                 interval=1):
            req = self.zk.getNodeRequest(req.id)
            if req.state in states:
                break

        return req

    def useNodepool(self, *args, **kwargs):
        secure_conf = kwargs.pop('secure_conf', None)
        args = (secure_conf,) + args
        pool = launcher.NodePool(*args, **kwargs)
        pool.cleanup_interval = .5
        pool.delete_interval = .5
        self.addCleanup(pool.stop)
        return pool

    def useWebApp(self, *args, **kwargs):
        app = webapp.WebApp(*args, **kwargs)
        self.addCleanup(app.stop)
        return app

    def useBuilder(self, configfile, securefile=None, cleanup_interval=.5,
                   num_uploaders=1):
        builder_fixture = self.useFixture(
            BuilderFixture(configfile, cleanup_interval, securefile,
                           num_uploaders)
        )
        return builder_fixture.builder

    def setupZK(self):
        f = ZookeeperServerFixture()
        self.useFixture(f)
        self.zookeeper_host = f.zookeeper_host
        self.zookeeper_port = f.zookeeper_port
        self.zookeeper_ca = f.zookeeper_ca
        self.zookeeper_cert = f.zookeeper_cert
        self.zookeeper_key = f.zookeeper_key

        kz_fxtr = self.useFixture(ChrootedKazooFixture(
            self.zookeeper_host,
            self.zookeeper_port,
            self.zookeeper_ca,
            self.zookeeper_cert,
            self.zookeeper_key,
        ))
        self.zookeeper_chroot = kz_fxtr.zookeeper_chroot
        self.zk = zk.ZooKeeper(enable_cache=False)
        host = zk.ZooKeeperConnectionConfig(
            self.zookeeper_host, self.zookeeper_port, self.zookeeper_chroot,
        )
        self.zk.connect([host],
                        tls_ca=self.zookeeper_ca,
                        tls_cert=self.zookeeper_cert,
                        tls_key=self.zookeeper_key)
        self.addCleanup(self.zk.disconnect)

    def printZKTree(self, node):
        def join(a, b):
            if a.endswith('/'):
                return a + b
            return a + '/' + b

        data, stat = self.zk.client.get(node)
        self.log.debug("Node: %s" % (node,))
        if data:
            self.log.debug(data)

        for child in self.zk.client.get_children(node):
            self.printZKTree(join(node, child))

    def getZKTree(self, path, ret=None):
        """Return the contents of a ZK tree as a dictionary"""
        if ret is None:
            ret = {}
        for key in self.zk.client.get_children(path):
            subpath = os.path.join(path, key)
            ret[subpath] = self.zk.client.get(
                os.path.join(path, key))[0]
            self.getZKTree(subpath, ret)
        return ret


class IntegrationTestCase(DBTestCase):
    def setUpFakes(self):
        pass
