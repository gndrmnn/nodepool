# Copyright (C) 2011-2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import functools
import ipaddress
import math
import os
import time
import yaml

from nodepool.driver import ConfigValue
from nodepool.driver import Drivers

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader


class ZooKeeperConnectionConfig(object):
    '''
    Represents the connection parameters for a ZooKeeper server.
    '''

    def __eq__(self, other):
        if isinstance(other, ZooKeeperConnectionConfig):
            if other.__dict__ == self.__dict__:
                return True
        return False

    def __init__(self, host, port=2181, chroot=None):
        '''Initialize the ZooKeeperConnectionConfig object.

        :param str host: The hostname of the ZooKeeper server.
        :param int port: The port on which ZooKeeper is listening.
            Optional, default: 2181.
        :param str chroot: A chroot for this connection.  All
            ZooKeeper nodes will be underneath this root path.
            Optional, default: None.

        (one per server) defining the ZooKeeper cluster servers. Only
        the 'host' attribute is required.'.

        '''
        self.host = host
        self.port = port
        self.chroot = chroot or ''

    def __repr__(self):
        return "host=%s port=%s chroot=%s" % \
            (self.host, self.port, self.chroot)


def buildZooKeeperHosts(host_list):
    '''
    Build the ZK cluster host list for client connections.

    :param list host_list: A list of
        :py:class:`~nodepool.zk.ZooKeeperConnectionConfig` objects (one
        per server) defining the ZooKeeper cluster servers.
    '''
    if not isinstance(host_list, list):
        raise Exception("'host_list' must be a list")
    hosts = []
    for host_def in host_list:
        h = host_def.host
        # If this looks like a ipv6 literal address, make sure it's
        # quoted in []'s
        try:
            addr = ipaddress.ip_address(host_def.host)
            if addr.version == 6:
                h = '[%s]' % addr
        except ValueError:
            pass
        host = '%s:%s%s' % (h, host_def.port, host_def.chroot)
        hosts.append(host)
    return ",".join(hosts)


class Config(ConfigValue):
    '''
    Class representing the nodepool configuration.

    This class implements methods to read each of the top-level configuration
    items found in the YAML config file, and set attributes accordingly.
    '''
    def __init__(self):
        self.diskimages = {}
        self.labels = {}
        self.providers = {}
        self.provider_managers = {}
        self.zookeeper_servers = {}
        self.zookeeper_timeout = 10.0
        self.zookeeper_tls_cert = None
        self.zookeeper_tls_key = None
        self.zookeeper_tls_ca = None
        self.elements_dir = None
        self.images_dir = None
        self.build_log_dir = None
        self.build_log_retention = None
        self.max_hold_age = None
        self.webapp = None
        self.tenant_resource_limits = {}
        # Last modified timestamps of loaded config files
        self.config_mtimes = {}

    def __eq__(self, other):
        if isinstance(other, Config):
            return (self.diskimages == other.diskimages and
                    self.labels == other.labels and
                    self.providers == other.providers and
                    self.provider_managers == other.provider_managers and
                    self.zookeeper_servers == other.zookeeper_servers and
                    self.zookeeper_timeout == other.zookeeper_timeout and
                    self.elements_dir == other.elements_dir and
                    self.images_dir == other.images_dir and
                    self.build_log_dir == other.build_log_dir and
                    self.build_log_retention == other.build_log_retention and
                    self.max_hold_age == other.max_hold_age and
                    self.webapp == other.webapp and
                    self.tenant_resource_limits == other.tenant_resource_limits
                    )
        return False

    def setConfigPathMtime(self, path, mtime):
        self.config_mtimes[path] = mtime

    def setElementsDir(self, value):
        self.elements_dir = value

    def setImagesDir(self, value):
        self.images_dir = value

    def setBuildLog(self, directory, retention):
        if retention is None:
            retention = 7
        self.build_log_dir = directory
        self.build_log_retention = retention

    def setMaxHoldAge(self, value):
        if value is None or value <= 0:
            value = math.inf
        self.max_hold_age = value

    def setWebApp(self, webapp_cfg):
        if webapp_cfg is None:
            webapp_cfg = {}
        self.webapp = {
            'port': webapp_cfg.get('port', 8005),
            'listen_address': webapp_cfg.get('listen_address', '0.0.0.0')
        }

    def setZooKeeperTLS(self, zk_tls):
        if not zk_tls:
            return
        self.zookeeper_tls_cert = zk_tls.get('cert')
        self.zookeeper_tls_key = zk_tls.get('key')
        self.zookeeper_tls_ca = zk_tls.get('ca')

    def setZooKeeperServers(self, zk_cfg):
        if not zk_cfg:
            return

        hosts = []
        for server in zk_cfg:
            z = ZooKeeperConnectionConfig(server['host'],
                                          server.get('port', 2281),
                                          server.get('chroot', None))
            hosts.append(z)
        self.zookeeper_servers = buildZooKeeperHosts(hosts)

    def setZooKeeperTimeout(self, timeout):
        self.zookeeper_timeout = float(timeout)

    def setDiskImages(self, diskimages_cfg):
        if not diskimages_cfg:
            return

        all_diskimages = {}
        non_abstract_diskimages = []

        # create a dict and split out the abstract images which don't
        # become final images, but can still be referenced as parent:
        for diskimage in diskimages_cfg:
            name = diskimage['name']
            all_diskimages[name] = diskimage
            if not diskimage.get('abstract', False):
                non_abstract_diskimages.append(diskimage)

        def _merge_image_cfg(diskimage, parent):
            parent_cfg = all_diskimages[parent]
            if parent_cfg.get('parent', None):
                _merge_image_cfg(diskimage, parent_cfg['parent'])
            diskimage.setFromConfig(parent_cfg)

        for cfg in non_abstract_diskimages:
            d = DiskImage(cfg['name'])

            # Walk the parents, if any, and set their values
            if cfg.get('parent', None):
                _merge_image_cfg(d, cfg.get('parent'))

            # Now set our config, which overrides any values from
            # parents.
            d.setFromConfig(cfg)

            # must be a string, as it's passed as env-var to
            # d-i-b, but might be untyped in the yaml and
            # interpreted as a number (e.g. "21" for fedora)
            d.release = str(d.release)

            # This is expected as a space-separated string
            d.elements = u' '.join(d.elements)

            self.diskimages[d.name] = d

    def setSecureDiskimageEnv(self, diskimages, secure_config_path):
        for diskimage in diskimages:
            if diskimage['name'] not in self.diskimages:
                raise Exception('%s: unknown diskimage %s' %
                                (secure_config_path, diskimage['name']))
            self.diskimages[diskimage['name']].env_vars.update(
                diskimage['env-vars'])

    def setLabels(self, labels_cfg):
        if not labels_cfg:
            return

        for label in labels_cfg:
            l = Label()
            l.name = label['name']
            l.max_ready_age = label.get('max-ready-age', 0)
            l.min_ready = label.get('min-ready', 0)
            l.pools = []
            self.labels[l.name] = l

    def setProviders(self, providers_cfg):
        if not providers_cfg:
            return

        for provider in providers_cfg:
            p = get_provider_config(provider)
            p.load(self)
            self.providers[p.name] = p

    def setTenantResourceLimits(self, tenant_resource_limits_cfg):
        if not tenant_resource_limits_cfg:
            return
        for resource_limit in tenant_resource_limits_cfg:
            resource_limit = resource_limit.copy()
            tenant_name = resource_limit.pop('tenant-name')
            limits = {}
            limits['cores'] = resource_limit.pop('max-cores', math.inf)
            limits['instances'] = resource_limit.pop('max-servers', math.inf)
            limits['ram'] = resource_limit.pop('max-ram', math.inf)
            if 'max-volumes' in resource_limit:
                limits['volumes'] = resource_limit.pop('max-volumes', math.inf)
            if 'max-volume-gb' in resource_limit:
                limits['volume-gb'] = resource_limit.pop(
                    'max-volume-gb', math.inf)
            for k, v in resource_limit.items():
                limits[k] = v
            self.tenant_resource_limits[tenant_name] = limits


class Label(ConfigValue):
    def __init__(self):
        self.name = None
        self.max_ready_age = None
        self.min_ready = None
        self.pools = None

    def __eq__(self, other):
        if isinstance(other, Label):
            return (self.name == other.name and
                    self.max_ready_age == other.max_ready_age and
                    self.min_ready == other.min_ready and
                    self.pools == other.pools)
        return False

    def __repr__(self):
        return "<Label %s>" % self.name


class DiskImage(ConfigValue):
    BUILD_TIMEOUT = (8 * 60 * 60)  # 8 hours
    REBUILD_AGE = (24 * 60 * 60)   # 24 hours

    def __init__(self, name):
        self.name = name
        self.build_timeout = self.BUILD_TIMEOUT
        self.dib_cmd = 'disk-image-create'
        self.elements = []
        self.env_vars = {}
        self.image_types = set()
        self.delete_after_upload = False
        self.keep_image_types = set()
        self.pause = False
        self.python_path = 'auto'
        self.shell_type = None
        self.rebuild_age = self.REBUILD_AGE
        self.release = ''
        self.username = 'zuul'
        self.meta = {}

    def setFromConfig(self, config):
        '''Merge values from configuration file

        This merges the values from a config dictionary (from the YAML
        config file) into the current diskimage.  Values from the
        specified config file will override any current values with
        the following exceptions:

        * elements append to the list
        * env_vars dict has update() sematics (new keys append,
          existing keys overwrite)

        This may be run multiple times to implement inheritance.

        :param dict config: The diskimage config from the config file
        '''
        build_timeout = config.get('build-timeout', None)
        if build_timeout:
            self.build_timeout = build_timeout
        dib_cmd = config.get('dib-cmd', None)
        if dib_cmd:
            self.dib_cmd = dib_cmd
        elements = config.get('elements', [])
        self.elements.extend(elements)
        env_vars = config.get('env-vars', {})
        self.env_vars.update(env_vars)
        image_types = config.get('formats', None)
        if image_types:
            self.image_types = set(image_types)
        keep_image_types = config.get('keep-formats', None)
        if keep_image_types:
            self.keep_image_types = set(keep_image_types)
        delete_after_upload = config.get('delete-after-upload', None)
        if delete_after_upload is not None:
            self.delete_after_upload = delete_after_upload
        pause = config.get('pause', None)
        if pause:
            self.pause = pause
        python_path = config.get('python-path', None)
        if python_path:
            self.python_path = python_path
        shell_type = config.get('shell-type', None)
        if shell_type:
            self.shell_type = shell_type
        rebuild_age = config.get('rebuild-age', None)
        if rebuild_age:
            self.rebuild_age = rebuild_age
        release = config.get('release', None)
        if release:
            self.release = release
        username = config.get('username', None)
        if username:
            self.username = username
        self.meta = config.get('metadata', {})

    def __eq__(self, other):
        if isinstance(other, DiskImage):
            return (other.name == self.name and
                    other.build_timeout == self.build_timeout and
                    other.dib_cmd == self.dib_cmd and
                    other.elements == self.elements and
                    other.env_vars == self.env_vars and
                    other.image_types == self.image_types and
                    other.pause == self.pause and
                    other.python_path == self.python_path and
                    other.shell_type == self.shell_type and
                    other.rebuild_age == self.rebuild_age and
                    other.release == self.release and
                    other.username == self.username and
                    other.meta == self.meta)
        return False

    def __repr__(self):
        return "<DiskImage %s>" % self.name


def as_list(item):
    if not item:
        return []
    if isinstance(item, list):
        return item
    return [item]


def get_provider_config(provider):
    provider.setdefault('driver', 'openstack')
    # Ensure legacy configuration still works when using fake cloud
    if provider.get('name', '').startswith('fake'):
        provider['driver'] = 'fake'
    driver = Drivers.get(provider['driver'])
    return driver.getProviderConfig(provider)


def substitute_env_vars(config_str, env):
    return functools.reduce(
        lambda config, env_item: config.replace(
            "%(" + env_item[0] + ")", env_item[1]),
        [(k, v) for k, v in env.items()
         if k.startswith('NODEPOOL_')],
        config_str)


def openConfig(path, env):
    retry = 3

    # Since some nodepool code attempts to dynamically re-read its config
    # file, we need to handle the race that happens if an outside entity
    # edits it (causing it to temporarily not exist) at the same time we
    # attempt to reload it.
    while True:
        try:
            with open(path) as f:
                return yaml.load(
                    substitute_env_vars(f.read(), env), SafeLoader)
        except IOError as e:
            if e.errno == 2:
                retry = retry - 1
                time.sleep(.5)
            else:
                raise e
            if retry == 0:
                raise e


def loadConfig(config_path, env=os.environ):
    config_mtime = os.stat(config_path).st_mtime_ns
    config = openConfig(config_path, env)

    # Call driver config reset now to clean global hooks like openstacksdk
    for driver in Drivers.drivers.values():
        driver.reset()

    newconfig = Config()

    newconfig.setElementsDir(config.get('elements-dir'))
    newconfig.setImagesDir(config.get('images-dir'))
    newconfig.setBuildLog(config.get('build-log-dir'),
                          config.get('build-log-retention'))
    newconfig.setMaxHoldAge(config.get('max-hold-age'))
    newconfig.setWebApp(config.get('webapp'))
    newconfig.setZooKeeperServers(config.get('zookeeper-servers'))
    newconfig.setZooKeeperTimeout(config.get('zookeeper-timeout', 10.0))
    newconfig.setDiskImages(config.get('diskimages'))
    newconfig.setLabels(config.get('labels'))
    newconfig.setProviders(config.get('providers'))
    newconfig.setZooKeeperTLS(config.get('zookeeper-tls'))
    newconfig.setTenantResourceLimits(config.get('tenant-resource-limits'))
    newconfig.setConfigPathMtime(config_path, config_mtime)

    return newconfig


def loadSecureConfig(config, secure_config_path, env=os.environ):
    secure_mtime = os.stat(secure_config_path).st_mtime_ns
    secure = openConfig(secure_config_path, env)
    if not secure:   # empty file
        return

    # Eliminate any servers defined in the normal config
    if secure.get('zookeeper-servers', []):
        config.zookeeper_servers = {}

    config.setZooKeeperServers(secure.get('zookeeper-servers'))
    config.setSecureDiskimageEnv(
        secure.get('diskimages', []), secure_config_path)
    config.setZooKeeperTLS(secure.get('zookeeper-tls'))
    config.setConfigPathMtime(secure_config_path, secure_mtime)


def checkRecentConfig(config, config_path, secure_path=None):
    current_config_mtime = config.config_mtimes.get(config_path, 0)
    new_config_mtime = os.stat(config_path).st_mtime_ns
    if current_config_mtime != new_config_mtime:
        return False

    if secure_path:
        current_secure_mtime = config.config_mtimes.get(secure_path, 0)
        new_secure_mtime = os.stat(secure_path).st_mtime_ns
        if current_secure_mtime != new_secure_mtime:
            return False

    return True
