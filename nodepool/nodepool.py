#!/usr/bin/env python

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

import logging
import time
import json
import threading
import yaml
import apscheduler.scheduler
import os
from statsd import statsd
import zmq

import nodedb
import nodeutils as utils
import provider_manager
import jenkins_manager

MINS = 60
HOURS = 60 * MINS

WATERMARK_SLEEP = 5          # Interval between checking if new servers needed
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
CONNECT_TIMEOUT = 10 * MINS  # How long to try to connect after a server
                             # is ACTIVE
NODE_CLEANUP = 8 * HOURS     # When to start deleting a node that is not
                             # READY or HOLD
KEEP_OLD_IMAGE = 24 * HOURS  # How long to keep an old (good) image
DELETE_DELAY = 1 * MINS      # Delay before deleting a node that has completed
                             # its job.


class NodeCompleteThread(threading.Thread):
    log = logging.getLogger("nodepool.NodeCompleteThread")

    def __init__(self, nodepool, nodename, jobname, result):
        threading.Thread.__init__(self)
        self.nodename = nodename
        self.nodepool = nodepool
        self.jobname = jobname
        self.result = result

    def run(self):
        try:
            with self.nodepool.db.getSession() as session:
                self.handleEvent(session)
        except Exception:
            self.log.exception("Exception handling event for %s:" %
                               self.nodename)

    def handleEvent(self, session):
        node = session.getNodeByNodename(self.nodename)
        if not node:
            self.log.debug("Unable to find node with nodename: %s" %
                           self.nodename)
            return

        target = self.nodepool.config.targets[node.target_name]
        if self.jobname == target.jenkins_test_job:
            self.log.debug("Test job for node id: %s complete, result: %s" %
                           (node.id, self.result))
            if self.result == 'SUCCESS':
                jenkins = self.nodepool.getJenkinsManager(target)
                old = jenkins.relabelNode(node.nodename, [node.image_name])
                self.log.info("Relabeled jenkins node id: %s from %s to %s" %
                              (node.id, old, node.image_name))
                self.node.state = nodedb.READY
                self.log.info("Node id: %s is ready" % self.node.id)
                self.nodepool.updateStats(session, self.provider.name)
                return
            self.log.info("Node id: %s failed acceptance test, deleting" %
                          self.node.id)

        time.sleep(DELETE_DELAY)
        self.nodepool.deleteNode(session, node)


class NodeUpdateListener(threading.Thread):
    log = logging.getLogger("nodepool.NodeUpdateListener")

    def __init__(self, nodepool, addr):
        threading.Thread.__init__(self)
        self.nodepool = nodepool
        self.socket = self.nodepool.zmq_context.socket(zmq.SUB)
        event_filter = b""
        self.socket.setsockopt(zmq.SUBSCRIBE, event_filter)
        self.socket.connect(addr)
        self._stopped = False

    def run(self):
        while not self._stopped:
            m = self.socket.recv().decode('utf-8')
            try:
                topic, data = m.split(None, 1)
                self.handleEvent(topic, data)
            except Exception:
                self.log.exception("Exception handling job:")

    def handleEvent(self, topic, data):
        self.log.debug("Received: %s %s" % (topic, data))
        args = json.loads(data)
        build = args['build']
        if 'node_name' not in build:
            return
        jobname = args['name']
        nodename = args['build']['node_name']
        if topic == 'onStarted':
            self.handleStartPhase(nodename, jobname)
        elif topic == 'onCompleted':
            pass
        elif topic == 'onFinalized':
            result = args['build'].get('status')
            self.handleCompletePhase(nodename, jobname, result)
        else:
            raise Exception("Received job for unhandled phase: %s" %
                            topic)

    def handleStartPhase(self, nodename, jobname):
        with self.nodepool.db.getSession() as session:
            node = session.getNodeByNodename(nodename)
            if not node:
                self.log.debug("Unable to find node with nodename: %s" %
                               nodename)
                return

            target = self.nodepool.config.targets[node.target_name]
            if jobname == target.jenkins_test_job:
                self.log.debug("Test job for node id: %s started" % node.id)
                return

            self.log.info("Setting node id: %s to USED" % node.id)
            node.state = nodedb.USED
            self.nodepool.updateStats(session, node.provider_name)

    def handleCompletePhase(self, nodename, jobname, result):
        t = NodeCompleteThread(self.nodepool, nodename, jobname, result)
        t.start()


class NodeLauncher(threading.Thread):
    log = logging.getLogger("nodepool.NodeLauncher")

    def __init__(self, nodepool, provider, image, target, node_id):
        threading.Thread.__init__(self)
        self.provider = provider
        self.image = image
        self.target = target
        self.node_id = node_id
        self.nodepool = nodepool

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in run method:")

    def _run(self):
        with self.nodepool.db.getSession() as session:
            self.log.debug("Launching node id: %s" % self.node_id)
            try:
                self.node = session.getNode(self.node_id)
                self.manager = self.nodepool.getProviderManager(self.provider)
            except Exception:
                self.log.exception("Exception preparing to launch node id: %s:"
                                   % self.node_id)
                return

            try:
                self.launchNode(session)
            except Exception:
                self.log.exception("Exception launching node id: %s:" %
                                   self.node_id)
                try:
                    self.nodepool.deleteNode(session, self.node)
                except Exception:
                    self.log.exception("Exception deleting node id: %s:" %
                                       self.node_id)
                    return

    def launchNode(self, session):
        start_time = time.time()

        hostname = '%s-%s-%s.slave.openstack.org' % (
            self.image.name, self.provider.name, self.node.id)
        self.node.hostname = hostname
        self.node.nodename = hostname.split('.')[0]
        self.node.target_name = self.target.name

        snap_image = session.getCurrentSnapshotImage(
            self.provider.name, self.image.name)
        if not snap_image:
            raise Exception("Unable to find current snapshot image %s in %s" %
                            (self.image.name, self.provider.name))

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self.provider.name,
                                           self.image.name, self.node_id))
        server_id = self.manager.createServer(hostname,
                                              self.image.min_ram,
                                              snap_image.external_id)
        self.node.external_id = server_id
        session.commit()

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server_id, self.node.id))
        server = self.manager.waitForServer(server_id)
        if server['status'] != 'ACTIVE':
            raise Exception("Server %s for node id: %s status: %s" %
                            (server_id, self.node.id, server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server['server_id'])
        if not ip:
            raise Exception("Unable to find public IP of server")

        self.node.ip = ip
        self.log.debug("Node id: %s is running, testing ssh" % self.node.id)
        if not utils.ssh_connect(ip, 'jenkins'):
            raise Exception("Unable to connect via ssh")

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.launch.%s.%s.%s' % (self.image.name,
                                                self.provider.name,
                                                self.target.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        # Do this before adding to jenkins to avoid a race where
        # Jenkins might immediately use the node before we've updated
        # the state:
        if self.target.jenkins_test_job:
            self.node.state = nodedb.TEST
            self.log.info("Node id: %s is in testing" % self.node.id)
        else:
            self.node.state = nodedb.READY
            self.log.info("Node id: %s is ready" % self.node.id)
        self.nodepool.updateStats(session, self.provider.name)

        if self.target.jenkins_url:
            self.log.debug("Adding node id: %s to jenkins" % self.node.id)
            self.createJenkinsNode()
            self.log.info("Node id: %s added to jenkins" % self.node.id)

    def createJenkinsNode(self):
        jenkins = self.nodepool.getJenkinsManager(self.target)

        if self.target.jenkins_test_job:
            labels = []
        else:
            labels = self.image.name
        args = dict(name=self.node.nodename,
                    host=self.node.ip,
                    description='Dynamic single use %s node' % self.image.name,
                    labels=labels,
                    executors=1,
                    root='/home/jenkins')
        if self.target.jenkins_credentials_id:
            args['credentials_id'] = self.target.jenkins_credentials_id
        else:
            args['username'] = 'jenkins'
            args['private_key'] = '/var/lib/jenkins/.ssh/id_rsa'

        jenkins.createNode(**args)

        if self.target.jenkins_test_job:
            params = dict(NODE=self.node.nodename)
            jenkins.startBuild(self.target.jenkins_test_job, params)


class ImageUpdater(threading.Thread):
    log = logging.getLogger("nodepool.ImageUpdater")

    def __init__(self, nodepool, provider, image, snap_image_id):
        threading.Thread.__init__(self)
        self.provider = provider
        self.image = image
        self.snap_image_id = snap_image_id
        self.nodepool = nodepool
        self.scriptdir = self.nodepool.config.scriptdir

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in run method:")

    def _run(self):
        with self.nodepool.db.getSession() as session:
            self.log.debug("Updating image %s in %s " % (self.image.name,
                                                         self.provider.name))
            try:
                self.snap_image = session.getSnapshotImage(
                    self.snap_image_id)
                self.manager = self.nodepool.getProviderManager(self.provider)
            except Exception:
                self.log.exception("Exception preparing to update image %s "
                                   "in %s:" % (self.image.name,
                                               self.provider.name))
                return

            try:
                self.updateImage(session)
            except Exception:
                self.log.exception("Exception updating image %s in %s:" %
                                   (self.image.name, self.provider.name))
                try:
                    if self.snap_image:
                        self.nodepool.deleteImage(self.snap_image)
                except Exception:
                    self.log.exception("Exception deleting image id: %s:" %
                                       self.snap_image.id)
                    return

    def updateImage(self, session):
        start_time = time.time()
        timestamp = int(start_time)

        hostname = ('%s-%s.template.openstack.org' %
                    (self.image.name, str(timestamp)))
        self.log.info("Creating image id: %s for %s in %s" %
                      (self.snap_image.id, self.image.name,
                       self.provider.name))
        if self.manager.hasExtension('os-keypairs'):
            key_name = hostname.split('.')[0]
            key = self.manager.addKeypair(key_name)
        else:
            key_name = None
            key = None

        server_id = self.manager.createServer(hostname,
                                              self.image.min_ram,
                                              image_name=self.image.base_image,
                                              key_name=key_name)
        self.snap_image.hostname = hostname
        self.snap_image.version = timestamp
        self.snap_image.server_external_id = server_id
        session.commit()

        self.log.debug("Image id: %s waiting for server %s" %
                       (self.snap_image.id, server_id))
        server = self.manager.waitForServer(server_id)
        if server['status'] != 'ACTIVE':
            raise Exception("Server %s for image id: %s status: %s" %
                            (server_id, self.snap_image.id, server['status']))

        ip = server.get('public_v4')
        if not ip and self.manager.hasExtension('os-floating-ips'):
            ip = self.manager.addPublicIP(server['server_id'])
        if not ip:
            raise Exception("Unable to find public IP of server")
        server['public_v4'] = ip

        self.bootstrapServer(server, key)

        image_id = self.manager.createImage(server_id, hostname)
        self.snap_image.external_id = image_id
        session.commit()
        self.log.debug("Image id: %s building image %s" %
                       (self.snap_image.id, image_id))
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        self.manager.waitForImage(image_id, IMAGE_TIMEOUT)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (self.image.name,
                                                   self.provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        self.snap_image.state = nodedb.READY
        session.commit()
        self.log.info("Image %s in %s is ready" % (hostname,
                                                   self.provider.name))

        try:
            # We made the snapshot, try deleting the server, but it's okay
            # if we fail.  The reap script will find it and try again.
            self.manager.cleanupServer(server_id)
        except:
            self.log.exception("Exception encountered deleting server"
                               " %s for image id: %s" %
                               (server_id, self.snap_image.id))

    def bootstrapServer(self, server, key):
        ssh_kwargs = {}
        if key:
            ssh_kwargs['pkey'] = key
        else:
            ssh_kwargs['password'] = server['admin_pass']

        for username in ['root', 'ubuntu']:
            host = utils.ssh_connect(server['public_v4'], username,
                                     ssh_kwargs,
                                     timeout=CONNECT_TIMEOUT)
            if host:
                break

        if not host:
            raise Exception("Unable to log in via SSH")

        host.ssh("make scripts dir", "mkdir -p scripts")
        for fname in os.listdir(self.scriptdir):
            host.scp(os.path.join(self.scriptdir, fname), 'scripts/%s' % fname)
        host.ssh("move scripts to opt", "sudo mv scripts /opt/nodepool-scripts")
        host.ssh("set scripts permissions", "sudo chmod -R a+rx /opt/nodepool-scripts")
        if self.image.setup:
            env_vars = ''
            for k, v in os.environ.items():
                if k.startswith('NODEPOOL_'):
                    env_vars += ' %s="%s"' % (k, v)
            r = host.ssh("run setup script", "cd /opt/nodepool-scripts && %s ./%s" %
                         (env_vars, self.image.setup))
            if not r:
                raise Exception("Unable to run setup scripts")


class ConfigValue(object):
    pass


class Config(ConfigValue):
    pass


class Provider(ConfigValue):
    pass


class ProviderImage(ConfigValue):
    pass


class Target(ConfigValue):
    pass


class TargetImage(ConfigValue):
    pass


class TargetImageProvider(ConfigValue):
    pass


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, configfile):
        threading.Thread.__init__(self)
        self.configfile = configfile
        self.zmq_context = None
        self.zmq_listeners = {}
        self.db = None
        self.dburi = None
        self.apsched = apscheduler.scheduler.Scheduler()
        self.apsched.start()

        self.update_cron = ''
        self.update_job = None
        self.cleanup_cron = ''
        self.cleanup_job = None
        self.check_cron = ''
        self.check_job = None
        self._stopped = False
        self.config = None
        self.loadConfig()

    def stop(self):
        self._stopped = True
        self.zmq_context.destroy()
        self.apsched.shutdown()

    def loadConfig(self):
        self.log.debug("Loading configuration")
        config = yaml.load(open(self.configfile))

        update_cron = config.get('cron', {}).get('image-update', '14 2 * * *')
        cleanup_cron = config.get('cron', {}).get('cleanup', '27 */6 * * *')
        check_cron = config.get('cron', {}).get('check', '*/15 * * * *')
        if (update_cron != self.update_cron):
            if self.update_job:
                self.apsched.unschedule_job(self.update_job)
            parts = update_cron.split()
            minute, hour, dom, month, dow = parts[:5]
            self.apsched.add_cron_job(self._doUpdateImages,
                                      day=dom,
                                      day_of_week=dow,
                                      hour=hour,
                                      minute=minute)
            self.update_cron = update_cron
        if (cleanup_cron != self.cleanup_cron):
            if self.cleanup_job:
                self.apsched.unschedule_job(self.cleanup_job)
            parts = cleanup_cron.split()
            minute, hour, dom, month, dow = parts[:5]
            self.apsched.add_cron_job(self._doPeriodicCleanup,
                                      day=dom,
                                      day_of_week=dow,
                                      hour=hour,
                                      minute=minute)
            self.cleanup_cron = cleanup_cron
        if (check_cron != self.check_cron):
            if self.check_job:
                self.apsched.unschedule_job(self.check_job)
            parts = check_cron.split()
            minute, hour, dom, month, dow = parts[:5]
            self.apsched.add_cron_job(self._doPeriodicCheck,
                                      day=dom,
                                      day_of_week=dow,
                                      hour=hour,
                                      minute=minute)
            self.check_cron = check_cron

        newconfig = Config()
        newconfig.providers = {}
        newconfig.targets = {}
        newconfig.scriptdir = config.get('script-dir')
        newconfig.dburi = config.get('dburi')
        newconfig.provider_managers = {}
        newconfig.jenkins_managers = {}
        stop_managers = []

        for provider in config['providers']:
            p = Provider()
            p.name = provider['name']
            newconfig.providers[p.name] = p
            p.username = provider['username']
            p.password = provider['password']
            p.project_id = provider['project-id']
            p.auth_url = provider['auth-url']
            p.service_type = provider.get('service-type')
            p.service_name = provider.get('service-name')
            p.region_name = provider.get('region-name')
            p.max_servers = provider['max-servers']
            p.rate = provider.get('rate', 1.0)
            oldmanager = None
            if self.config:
                oldmanager = self.config.provider_managers.get(p.name)
            if oldmanager:
                if (p.username != oldmanager.provider.username or
                    p.password != oldmanager.provider.password or
                    p.project_id != oldmanager.provider.project_id or
                    p.auth_url != oldmanager.provider.auth_url or
                    p.service_type != oldmanager.provider.service_type or
                    p.service_name != oldmanager.provider.service_name or
                    p.region_name != oldmanager.provider.region_name):
                    stop_managers.append(oldmanager)
                    oldmanager = None
            if oldmanager:
                newconfig.provider_managers[p.name] = oldmanager
            else:
                self.log.debug("Creating new ProviderManager object for %s" %
                               p.name)
                newconfig.provider_managers[p.name] = \
                    provider_manager.ProviderManager(p)
                newconfig.provider_managers[p.name].start()
            p.images = {}
            for image in provider['images']:
                i = ProviderImage()
                i.name = image['name']
                p.images[i.name] = i
                i.base_image = image['base-image']
                i.min_ram = image['min-ram']
                i.setup = image.get('setup')
                i.reset = image.get('reset')
        for target in config['targets']:
            t = Target()
            t.name = target['name']
            newconfig.targets[t.name] = t
            jenkins = target.get('jenkins')
            if jenkins:
                t.jenkins_url = jenkins['url']
                t.jenkins_user = jenkins['user']
                t.jenkins_apikey = jenkins['apikey']
                t.jenkins_credentials_id = jenkins.get('credentials-id')
                t.jenkins_test_job = jenkins.get('test-job')
            else:
                t.jenkins_url = None
                t.jenkins_user = None
                t.jenkins_apikey = None
                t.jenkins_credentials_id = None
                t.jenkins_test_job = None
            t.rate = target.get('rate', 1.0)
            oldmanager = None
            if self.config:
                oldmanager = self.config.jenkins_managers.get(t.name)
            if oldmanager:
                if (t.jenkins_url != oldmanager.target.jenkins_url or
                    t.jenkins_user != oldmanager.target.jenkins_user or
                    t.jenkins_apikey != oldmanager.target.jenkins_apikey):
                    stop_managers.append(oldmanager)
                    oldmanager = None
            if oldmanager:
                newconfig.jenkins_managers[t.name] = oldmanager
            else:
                self.log.debug("Creating new JenkinsManager object for %s" %
                               t.name)
                newconfig.jenkins_managers[t.name] = \
                    jenkins_manager.JenkinsManager(t)
                newconfig.jenkins_managers[t.name].start()
            t.images = {}
            for image in target['images']:
                i = TargetImage()
                i.name = image['name']
                t.images[i.name] = i
                i.providers = {}
                for provider in image['providers']:
                    p = TargetImageProvider()
                    p.name = provider['name']
                    i.providers[p.name] = p
                    p.min_ready = provider['min-ready']
        self.config = newconfig
        for oldmanager in stop_managers:
            oldmanager.stop()
        if self.config.dburi != self.dburi:
            self.dburi = self.config.dburi
            self.db = nodedb.NodeDatabase(self.config.dburi)
        self.startUpdateListeners(config['zmq-publishers'])

    def getProviderManager(self, provider):
        return self.config.provider_managers[provider.name]

    def getJenkinsManager(self, target):
        return self.config.jenkins_managers[target.name]

    def startUpdateListeners(self, publishers):
        running = set(self.zmq_listeners.keys())
        configured = set(publishers)
        if running == configured:
            self.log.debug("Listeners do not need to be updated")
            return

        if self.zmq_context:
            self.log.debug("Stopping listeners")
            self.zmq_context.destroy()
            self.zmq_listeners = {}
        self.zmq_context = zmq.Context()
        for addr in publishers:
            self.log.debug("Starting listener for %s" % addr)
            listener = NodeUpdateListener(self, addr)
            self.zmq_listeners[addr] = listener
            listener.start()

    def getNumNeededNodes(self, session, target, provider, image):
        # Count machines that are ready and machines that are building,
        # so that if the provider is very slow, we aren't queueing up tons
        # of machines to be built.
        n_ready = len(session.getNodes(provider.name, image.name, target.name,
                                       nodedb.READY))
        n_building = len(session.getNodes(provider.name, image.name,
                                          target.name, nodedb.BUILDING))
        n_provider = len(session.getNodes(provider.name))
        num_to_launch = provider.min_ready - (n_ready + n_building)

        # Don't launch more than our provider max
        max_servers = self.config.providers[provider.name].max_servers
        num_to_launch = min(max_servers - n_provider, num_to_launch)

        # Don't launch less than 0
        num_to_launch = max(0, num_to_launch)

        return num_to_launch

    def run(self):
        while not self._stopped:
            try:
                self.loadConfig()
                with self.db.getSession() as session:
                    self._run(session)
            except Exception:
                self.log.exception("Exception in main loop:")
            time.sleep(WATERMARK_SLEEP)

    def _run(self, session):
        self.checkForMissingImages(session)
        for target in self.config.targets.values():
            self.log.debug("Examining target: %s" % target.name)
            for image in target.images.values():
                for provider in image.providers.values():
                    num_to_launch = self.getNumNeededNodes(
                        session, target, provider, image)
                    if num_to_launch:
                        self.log.info("Need to launch %s %s nodes for "
                                      "%s on %s" %
                                      (num_to_launch, image.name,
                                       target.name, provider.name))
                    for i in range(num_to_launch):
                        snap_image = session.getCurrentSnapshotImage(
                            provider.name, image.name)
                        if not snap_image:
                            self.log.debug("No current image for %s on %s"
                                           % (provider.name, image.name))
                        else:
                            self.launchNode(session, provider, image, target)

    def checkForMissingImages(self, session):
        # If we are missing an image, run the image update function
        # outside of its schedule.
        missing = False
        for target in self.config.targets.values():
            for image in target.images.values():
                for provider in image.providers.values():
                    found = False
                    for snap_image in session.getSnapshotImages():
                        if (snap_image.provider_name == provider.name and
                            snap_image.image_name == image.name and
                            snap_image.state in [nodedb.READY,
                                                 nodedb.BUILDING]):
                            found = True
                    if not found:
                        self.log.warning("Missing image %s on %s" %
                                         (image.name, provider.name))
                        missing = True
        if missing:
            self.updateImages(session)

    def _doUpdateImages(self):
        try:
            with self.db.getSession() as session:
                self.updateImages(session)
        except Exception:
            self.log.exception("Exception in periodic image update:")

    def updateImages(self, session):
        # This function should be run periodically to create new snapshot
        # images.
        for provider in self.config.providers.values():
            for image in provider.images.values():
                snap_image = session.createSnapshotImage(
                    provider_name=provider.name,
                    image_name=image.name)
                t = ImageUpdater(self, provider, image, snap_image.id)
                t.start()
                # Enough time to give them different timestamps (versions)
                # Just to keep things clearer.
                time.sleep(2)

    def launchNode(self, session, provider, image, target):
        provider = self.config.providers[provider.name]
        image = provider.images[image.name]
        node = session.createNode(provider.name, image.name, target.name)
        t = NodeLauncher(self, provider, image, target, node.id)
        t.start()

    def deleteNode(self, session, node):
        # Delete a node
        start_time = time.time()
        node.state = nodedb.DELETE
        self.updateStats(session, node.provider_name)
        provider = self.config.providers[node.provider_name]
        target = self.config.targets[node.target_name]
        manager = self.getProviderManager(provider)

        if target.jenkins_url:
            jenkins = self.getJenkinsManager(target)
            jenkins_name = node.nodename
            if jenkins.nodeExists(jenkins_name):
                jenkins.deleteNode(jenkins_name)
            self.log.info("Deleted jenkins node id: %s" % node.id)

        if node.external_id:
            try:
                server = manager.getServer(node.external_id)
                self.log.debug('Deleting server %s for node id: %s' %
                               (node.external_id,
                                node.id))
                manager.cleanupServer(server['id'])
            except provider_manager.NotFound:
                pass

        node.delete()
        self.log.info("Deleted node id: %s" % node.id)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.delete.%s.%s.%s' % (node.image_name,
                                                node.provider_name,
                                                node.target_name)
            statsd.timing(key, dt)
            statsd.incr(key)
        self.updateStats(session, node.provider_name)

    def deleteImage(self, snap_image):
        # Delete an image (and its associated server)
        snap_image.state = nodedb.DELETE
        provider = self.config.providers[snap_image.provider_name]
        manager = self.getProviderManager(provider)

        if snap_image.server_external_id:
            try:
                server = manager.getServer(snap_image.server_external_id)
                self.log.debug('Deleting server %s for image id: %s' %
                               (snap_image.server_external_id,
                                snap_image.id))
                manager.cleanupServer(server['id'])
            except provider_manager.NotFound:
                self.log.warning('Image server id %s not found' %
                                 snap_image.server_external_id)

        if snap_image.external_id:
            try:
                remote_image = manager.getImage(snap_image.external_id)
                self.log.debug('Deleting image %s' % remote_image.id)
                manager.deleteImage(remote_image['id'])
            except provider_manager.NotFound:
                self.log.warning('Image id %s not found' %
                                 snap_image.external_id)

        snap_image.delete()
        self.log.info("Deleted image id: %s" % snap_image.id)

    def _doPeriodicCleanup(self):
        try:
            with self.db.getSession() as session:
                self.periodicCleanup(session)
        except Exception:
            self.log.exception("Exception in periodic cleanup:")

    def periodicCleanup(self, session):
        # This function should be run periodically to clean up any hosts
        # that may have slipped through the cracks, as well as to remove
        # old images.

        self.log.debug("Starting periodic cleanup")
        for node in session.getNodes():
            if node.state in [nodedb.READY, nodedb.HOLD]:
                continue
            delete = False
            if (node.state == nodedb.DELETE):
                self.log.warning("Deleting node id: %s which is in delete "
                                 "state" % node.id)
                delete = True
            elif time.time() - node.state_time > NODE_CLEANUP:
                self.log.warning("Deleting node id: %s which has been in %s "
                                 "state for %s hours" %
                                 (node.id, node.state,
                                  node.state_time / (60 * 60)))
                delete = True
            if delete:
                try:
                    self.deleteNode(session, node)
                except Exception:
                    self.log.exception("Exception deleting node id: "
                                       "%s" % node.id)

        for image in session.getSnapshotImages():
            # Normally, reap images that have sat in their current state
            # for 24 hours, unless the image is the current snapshot
            delete = False
            if image.provider_name not in self.config.providers:
                delete = True
                self.log.info("Deleting image id: %s which has no current "
                              "provider" % image.id)
            elif (image.image_name not in
                  self.config.providers[image.provider_name].images):
                delete = True
                self.log.info("Deleting image id: %s which has no current "
                              "base image" % image.id)
            else:
                current = session.getCurrentSnapshotImage(image.provider_name,
                                                          image.image_name)
                if (current and image != current and
                    (time.time() - current.state_time) > KEEP_OLD_IMAGE):
                    self.log.info("Deleting image id: %s because the current "
                                  "image is %s hours old" %
                                  (image.id, current.state_time / (60 * 60)))
                    delete = True
            if delete:
                try:
                    self.deleteImage(image)
                except Exception:
                    self.log.exception("Exception deleting image id: %s:" %
                                       image.id)
        self.log.debug("Finished periodic cleanup")

    def _doPeriodicCheck(self):
        try:
            with self.db.getSession() as session:
                self.periodicCheck(session)
        except Exception:
            self.log.exception("Exception in periodic chack:")

    def periodicCheck(self, session):
        # This function should be run periodically to make sure we can
        # still access hosts via ssh.

        self.log.debug("Starting periodic check")
        for node in session.getNodes():
            if node.state != nodedb.READY:
                continue
            try:
                if utils.ssh_connect(node.ip, 'jenkins'):
                    continue
            except Exception:
                self.log.exception("SSH Check failed for node id: %s" % node.id)
                self.deleteNode(session, node)
        self.log.debug("Finished periodic check")

    def updateStats(self, session, provider_name):
        if not statsd:
            return
        # This may be called outside of the main thread.
        provider = self.config.providers[provider_name]

        states = {}

        for target in self.config.targets.values():
            for image in target.images.values():
                for provider in image.providers.values():
                    base_key = 'nodepool.target.%s.%s.%s' % (
                        target.name, image.name,
                        provider.name)
                    key = '%s.min_ready' % base_key
                    statsd.gauge(key, provider.min_ready)
                    for state in nodedb.STATE_NAMES.values():
                        key = '%s.%s' % (base_key, state)
                        states[key] = 0

        for node in session.getNodes():
            if node.state not in nodedb.STATE_NAMES:
                continue
            key = 'nodepool.target.%s.%s.%s.%s' % (
                node.target_name, node.image_name,
                node.provider_name, nodedb.STATE_NAMES[node.state])
            states[key] += 1

        for key, count in states.items():
            statsd.gauge(key, count)

        for provider in self.config.providers.values():
            key = 'nodepool.provider.%s.max_servers' % provider.name
            statsd.gauge(key, provider.max_servers)
