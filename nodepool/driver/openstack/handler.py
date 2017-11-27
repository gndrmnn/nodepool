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

import collections
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

    def _imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        ZooKeeper is queried for an image uploaded to the provider that is
        in the READY state.

        :returns: True if it is available, False otherwise.
        '''
        for label in self.request.node_types:

            if self.pool.labels[label].cloud_image:
                if not self.manager.labelReady(self.pool.labels[label]):
                    return False
            else:
                if not self.zk.getMostRecentImageUpload(
                        self.pool.labels[label].diskimage.name,
                        self.provider.name):
                    return False
        return True

    def _invalidNodeTypes(self):
        '''
        Return any node types that are invalid for this provider.

        :returns: A list of node type names that are invalid, or an empty
            list if all are valid.
        '''
        invalid = []
        for ntype in self.request.node_types:
            if ntype not in self.pool.labels:
                invalid.append(ntype)
        return invalid

    def _countNodes(self):
        '''
        Query ZooKeeper to determine the number of provider nodes launched.

        :returns: An integer for the number launched for this provider.
        '''
        count = 0
        for node in self.zk.nodeIterator():
            if (node.provider == self.provider.name and
                node.pool == self.pool.name):
                count += 1
        return count

    def _waitForNodeSet(self):
        '''
        Fill node set for the request.

        Obtain nodes for the request, pausing all new request handling for
        this provider until the node set can be filled.

        We attempt to group the node set within the same provider availability
        zone. For this to work properly, the provider entry in the nodepool
        config must list the availability zones. Otherwise, new nodes will be
        put in random AZs at nova's whim. The exception being if there is an
        existing node in the READY state that we can select for this node set.
        Its AZ will then be used for new nodes, as well as any other READY
        nodes.

        note:: This code is a bit racey in its calculation of the number of
            nodes in use for quota purposes. It is possible for multiple
            launchers to be doing this calculation at the same time. Since we
            currently have no locking mechanism around the "in use"
            calculation, if we are at the edge of the quota, one of the
            launchers could attempt to launch a new node after the other
            launcher has already started doing so. This would cause an
            expected failure from the underlying library, which is ok for now.
        '''
        if not self.launch_manager:
            self.launch_manager = OpenStackNodeLaunchManager(
                self.zk, self.pool, self.manager,
                self.request.requestor, retries=self.provider.launch_retries)

        # Since this code can be called more than once for the same request,
        # we need to calculate the difference between our current node set
        # and what was requested. We cannot use set operations here since a
        # node type can appear more than once in the requested types.
        saved_types = collections.Counter([n.type for n in self.nodeset])
        requested_types = collections.Counter(self.request.node_types)
        diff = requested_types - saved_types
        needed_types = list(diff.elements())

        ready_nodes = self.zk.getReadyNodesOfTypes(needed_types)

        for ntype in needed_types:
            # First try to grab from the list of already available nodes.
            got_a_node = False
            if self.request.reuse and ntype in ready_nodes:
                for node in ready_nodes[ntype]:
                    # Only interested in nodes from this provider and
                    # pool, and within the selected AZ.
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue
                    if self.chosen_az and node.az != self.chosen_az:
                        continue

                    try:
                        self.zk.lockNode(node, blocking=False)
                    except exceptions.ZKLockException:
                        # It's already locked so skip it.
                        continue
                    else:
                        if self.paused:
                            self.log.debug("Unpaused request %s", self.request)
                            self.paused = False

                        self.log.debug(
                            "Locked existing node %s for request %s",
                            node.id, self.request.id)
                        got_a_node = True
                        node.allocated_to = self.request.id
                        self.zk.storeNode(node)
                        self.nodeset.append(node)

                        # If we haven't already chosen an AZ, select the
                        # AZ from this ready node. This will cause new nodes
                        # to share this AZ, as well.
                        if not self.chosen_az and node.az:
                            self.chosen_az = node.az
                        break

            # Could not grab an existing node, so launch a new one.
            if not got_a_node:
                # Select grouping AZ if we didn't set AZ from a selected,
                # pre-existing node
                if not self.chosen_az:
                    self.chosen_az = random.choice(
                        self.pool.azs or self.manager.getAZs())

                # If we calculate that we're at capacity, pause until nodes
                # are released by Zuul and removed by the DeletedNodeWorker.
                if self._countNodes() >= self.pool.max_servers:
                    if not self.paused:
                        self.log.debug(
                            "Pausing request handling to satisfy request %s",
                            self.request)
                    self.paused = True
                    return

                if self.paused:
                    self.log.debug("Unpaused request %s", self.request)
                    self.paused = False

                node = zk.Node()
                node.state = zk.INIT
                node.type = ntype
                node.provider = self.provider.name
                node.pool = self.pool.name
                node.az = self.chosen_az
                node.cloud = self.provider.cloud_config.name
                node.region = self.provider.region_name
                node.launcher = self.launcher_id
                node.allocated_to = self.request.id

                # Note: It should be safe (i.e., no race) to lock the node
                # *after* it is stored since nodes in INIT state are not
                # locked anywhere.
                self.zk.storeNode(node)
                self.zk.lockNode(node, blocking=False)
                self.log.debug("Locked building node %s for request %s",
                               node.id, self.request.id)

                # Set state AFTER lock so sthat it isn't accidentally cleaned
                # up (unlocked BUILDING nodes will be deleted).
                node.state = zk.BUILDING
                self.zk.storeNode(node)

                self.nodeset.append(node)
                self.launch_manager.launch(node)

    def run_handler(self):
        '''
        Main body for the OpenStackNodeRequestHandler.
        '''
        self._setFromPoolWorker()

        # We have the launcher_id attr after _setFromPoolWorker() is called.
        self.log = logging.getLogger(
            "nodepool.driver.openstack.OpenStackNodeRequestHandler[%s]" %
            self.launcher_id)

        declined_reasons = []
        invalid_types = self._invalidNodeTypes()
        if invalid_types:
            declined_reasons.append('node type(s) [%s] not available' %
                                    ','.join(invalid_types))
        elif not self._imagesAvailable():
            declined_reasons.append('images are not available')
        if len(self.request.node_types) > self.pool.max_servers:
            declined_reasons.append('it would exceed quota')

        # For min-ready requests, which do not re-use READY nodes, let's
        # decline if this provider is already at capacity. Otherwise, we
        # could end up wedged until another request frees up a node.
        if self.request.requestor == "NodePool:min-ready":
            current_count = self.zk.countPoolNodes(self.provider.name,
                                                   self.pool.name)
            # Use >= because dynamic config changes to max-servers can leave
            # us with more than max-servers.
            if current_count >= self.pool.max_servers:
                declined_reasons.append("provider cannot satisify min-ready")

        if declined_reasons:
            self.log.debug("Declining node request %s because %s",
                           self.request.id, ', '.join(declined_reasons))
            self.request.declined_by.append(self.launcher_id)
            launchers = set(self.zk.getRegisteredLaunchers())
            if launchers.issubset(set(self.request.declined_by)):
                self.log.debug("Failing declined node request %s",
                               self.request.id)
                # All launchers have declined it
                self.request.state = zk.FAILED
            self.unlockNodeSet(clear_allocation=True)

            # If conditions have changed for a paused request to now cause us
            # to decline it, we need to unpause so we don't keep trying it
            if self.paused:
                self.paused = False
                # If we didn't mark the request as failed above, reset it.
                if self.request.state != zk.FAILED:
                    self.request.state = zk.REQUESTED

            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
            return

        if self.paused:
            self.log.debug("Retrying node request %s", self.request.id)
        else:
            self.log.debug("Accepting node request %s", self.request.id)
            self.request.state = zk.PENDING
            self.zk.storeNodeRequest(self.request)

        self._waitForNodeSet()
