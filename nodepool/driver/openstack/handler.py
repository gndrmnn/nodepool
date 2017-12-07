# Copyright (C) 2011-2014 OpenStack Foundation
# Copyright 2017 Red Hat
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

import logging
import pprint
import random
import threading
import time

from nodepool import exceptions
from nodepool import nodeutils as utils
from nodepool import stats
from nodepool import zk
from nodepool.driver import NodeLaunchManager
from nodepool.driver import NodeRequestHandler


class NodeLauncher(threading.Thread, stats.StatsReporter):
    log = logging.getLogger("nodepool.driver.openstack."
                            "NodeLauncher")

    def __init__(self, zk, provider_label, provider_manager, requestor,
                 node, retries):
        '''
        Initialize the launcher.

        :param ZooKeeper zk: A ZooKeeper object.
        :param ProviderLabel provider: A config ProviderLabel object.
        :param ProviderManager provider_manager: The manager object used to
            interact with the selected provider.
        :param str requestor: Identifier for the request originator.
        :param Node node: The node object.
        :param int retries: Number of times to retry failed launches.
        '''
        threading.Thread.__init__(self, name="NodeLauncher-%s" % node.id)
        stats.StatsReporter.__init__(self)
        self.log = logging.getLogger("nodepool.NodeLauncher-%s" % node.id)
        self._zk = zk
        self._label = provider_label
        self._manager = provider_manager
        self._node = node
        self._retries = retries
        self._image_name = None
        self._requestor = requestor

        self._pool = self._label.pool
        self._provider = self._pool.provider
        if self._label.diskimage:
            self._diskimage = self._provider.diskimages[self._label.diskimage.name]
        else:
            self._diskimage = None

    def logConsole(self, server_id, hostname):
        if not self._label.console_log:
            return
        console = self._manager.getServerConsole(server_id)
        if console:
            self.log.debug('Console log from hostname %s:' % hostname)
            for line in console.splitlines():
                self.log.debug(line.rstrip())

    def _launchNode(self):
        if self._label.diskimage:
            # launch using diskimage
            cloud_image = self._zk.getMostRecentImageUpload(
                self._diskimage.name, self._provider.name)

            if not cloud_image:
                raise exceptions.LaunchNodepoolException(
                    "Unable to find current cloud image %s in %s" %
                    (self._diskimage.name, self._provider.name)
                )

            config_drive = self._diskimage.config_drive
            image_external = dict(id=cloud_image.external_id)
            image_id = "{path}/{upload_id}".format(
                path=self._zk._imageUploadPath(cloud_image.image_name,
                                               cloud_image.build_id,
                                               cloud_image.provider_name),
                upload_id=cloud_image.id)
            image_name = self._diskimage.name
            username = cloud_image.username

        else:
            # launch using unmanaged cloud image
            config_drive = self._label.cloud_image.config_drive

            image_external = self._label.cloud_image.external
            image_id = self._label.cloud_image.name
            image_name = self._label.cloud_image.name

            # TODO(tobiash): support username also for unmanaged cloud images
            username = None

        hostname = self._provider.hostname_format.format(
            label=self._label, provider=self._provider, node=self._node
        )

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self._provider.name,
                                           image_name,
                                           self._node.id))

        # NOTE: We store the node ID in the server metadata to use for leaked
        # instance detection. We cannot use the external server ID for this
        # because that isn't available in ZooKeeper until after the server is
        # active, which could cause a race in leak detection.

        server = self._manager.createServer(
            hostname,
            image=image_external,
            min_ram=self._label.min_ram,
            flavor_name=self._label.flavor_name,
            key_name=self._label.key_name,
            az=self._node.az,
            config_drive=config_drive,
            nodepool_node_id=self._node.id,
            nodepool_node_label=self._node.type,
            nodepool_image_name=image_name,
            networks=self._pool.networks,
            boot_from_volume=self._label.boot_from_volume,
            volume_size=self._label.volume_size)

        self._node.external_id = server.id
        self._node.hostname = hostname
        self._node.image_id = image_id
        if username:
            self._node.username = username

        # Checkpoint save the updated node info
        self._zk.storeNode(self._node)

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server.id, self._node.id))
        server = self._manager.waitForServer(
            server, self._provider.launch_timeout,
            auto_ip=self._pool.auto_floating_ip)

        if server.status != 'ACTIVE':
            raise exceptions.LaunchStatusException("Server %s for node id: %s "
                                                   "status: %s" %
                                                   (server.id, self._node.id,
                                                    server.status))

        # If we didn't specify an AZ, set it to the one chosen by Nova.
        # Do this after we are done waiting since AZ may not be available
        # immediately after the create request.
        if not self._node.az:
            self._node.az = server.location.zone

        interface_ip = server.interface_ip
        if not interface_ip:
            self.log.debug(
                "Server data for failed IP: %s" % pprint.pformat(
                    server))
            raise exceptions.LaunchNetworkException(
                "Unable to find public IP of server")

        self._node.interface_ip = interface_ip
        self._node.public_ipv4 = server.public_v4
        self._node.public_ipv6 = server.public_v6
        self._node.private_ipv4 = server.private_v4
        # devstack-gate multi-node depends on private_v4 being populated
        # with something. On clouds that don't have a private address, use
        # the public.
        if not self._node.private_ipv4:
            self._node.private_ipv4 = server.public_v4

        # Checkpoint save the updated node info
        self._zk.storeNode(self._node)

        self.log.debug(
            "Node %s is running [region: %s, az: %s, ip: %s ipv4: %s, "
            "ipv6: %s]" %
            (self._node.id, self._node.region, self._node.az,
             self._node.interface_ip, self._node.public_ipv4,
             self._node.public_ipv6))

        # Get the SSH public keys for the new node and record in ZooKeeper
        try:
            self.log.debug("Gathering host keys for node %s", self._node.id)
            host_keys = utils.keyscan(
                interface_ip, timeout=self._provider.boot_timeout)
            if not host_keys:
                raise exceptions.LaunchKeyscanException(
                    "Unable to gather host keys")
        except exceptions.SSHTimeoutException:
            self.logConsole(self._node.external_id, self._node.hostname)
            raise

        self._node.host_keys = host_keys
        self._zk.storeNode(self._node)

    def _run(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchNode()
                break
            except Exception:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self._node.id)
                # If we created an instance, delete it.
                if self._node.external_id:
                    self._manager.cleanupNode(self._node.external_id)
                    self._manager.waitForNodeCleanup(self._node.external_id)
                    self._node.external_id = None
                    self._node.public_ipv4 = None
                    self._node.public_ipv6 = None
                    self._node.interface_ip = None
                    self._zk.storeNode(self._node)
                if attempts == self._retries:
                    raise
                attempts += 1

        self._node.state = zk.READY
        self._zk.storeNode(self._node)
        self.log.info("Node id %s is ready", self._node.id)

    def run(self):
        start_time = time.time()
        statsd_key = 'ready'

        try:
            self._run()
        except Exception as e:
            self.log.exception("Launch failed for node %s:",
                               self._node.id)
            self._node.state = zk.FAILED
            self._zk.storeNode(self._node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

        try:
            dt = int((time.time() - start_time) * 1000)
            self.recordLaunchStats(statsd_key, dt, self._image_name,
                                   self._node.provider, self._node.az,
                                   self._requestor)
            self.updateNodeStats(self._zk, self._provider)
        except Exception:
            self.log.exception("Exception while reporting stats:")


class OpenStackNodeLaunchManager(NodeLaunchManager):
    def launch(self, node):
        '''
        Launch a new node as described by the supplied Node.

        We expect each NodeLauncher thread to directly modify the node that
        is passed to it. The poll() method will expect to see the node.state
        attribute to change as the node is processed.

        :param Node node: The node object.
        '''
        self._nodes.append(node)
        provider_label = self._pool.labels[node.type]
        t = NodeLauncher(self._zk, provider_label, self._manager,
                         self._requestor, node, self._retries)
        t.start()
        self._threads.append(t)


class OpenStackNodeRequestHandler(NodeRequestHandler):

    def __init__(self, pw, request):
        super(OpenStackNodeRequestHandler, self).__init__(pw, request)
        self.chosen_az = None

    def check_reusable_node(self, node):
        if self.chosen_az and node.az != self.chosen_az:
            return False
        return True

    def node_reused(self, node):
        """
        We attempt to group the node set within the same provider availability
        zone.
        For this to work properly, the provider entry in the nodepool
        config must list the availability zones. Otherwise, new nodes will be
        put in random AZs at nova's whim. The exception being if there is an
        existing node in the READY state that we can select for this node set.
        Its AZ will then be used for new nodes, as well as any other READY
        nodes.
        """
        # If we haven't already chosen an AZ, select the
        # AZ from this ready node. This will cause new nodes
        # to share this AZ, as well.
        if not self.chosen_az and node.az:
            self.chosen_az = node.az

    def set_node_metadata(self, node):
        # Select grouping AZ if we didn't set AZ from a selected,
        # pre-existing node
        if not self.chosen_az:
            self.chosen_az = random.choice(
                self.pool.azs or self.manager.getAZs())
        node.az = self.chosen_az
        node.cloud = self.provider.cloud_config.name
        node.region = self.provider.region_name

    def launch(self, node):
        if not self.launch_manager:
            self.launch_manager = OpenStackNodeLaunchManager(
                self.zk, self.pool, self.manager,
                self.request.requestor, retries=self.provider.launch_retries)
        self.launch_manager.launch(node)
