# Copyright 2019 Red Hat
# Copyright 2021-2023 Acme Gating, LLC
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


from concurrent.futures.thread import ThreadPoolExecutor
import errno
import fcntl
import logging
import math
import os
import random
import select
import socket
import threading
import time

from nodepool.driver import Driver, NodeRequestHandler, Provider
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.logconfig import get_annotated_logger
from nodepool import stats
from nodepool import exceptions
from nodepool.zk import zookeeper as zk

from kazoo import exceptions as kze
import cachetools
import paramiko


class StateMachineNodeLauncher(stats.StatsReporter):
    """The state of the state machine.

    This driver collects state machines from the underlying cloud
    adapter implementations; those state machines handle building the
    node.  But we still have our own accounting and a little bit of
    work to do on either side of that process, so this data structure
    holds the extra information we need for that.
    """

    def __init__(self, handler, node, provider_config):
        launcher = handler.pw.nodepool
        super().__init__(launcher.statsd)
        # Based on utils.NodeLauncher
        logger = logging.getLogger(
            f"nodepool.StateMachineNodeLauncher.{provider_config.name}")
        request = handler.request
        self.log = get_annotated_logger(logger,
                                        event_id=request.event_id,
                                        node_request_id=request.id,
                                        node_id=node.id)
        self.handler = handler
        self.zk = handler.zk
        self.node = node
        self.provider_config = provider_config
        # Local additions:
        self.start_future = None
        self.manager = handler.manager
        self.start_time = None
        self.attempts = 0
        self.retries = self.manager.provider.launch_retries
        self.state_machine = None
        self.nodescan_request = None
        # To handle deletions:
        self.delete_state_machine = None

    @property
    def complete(self):
        if self.node.state != zk.BUILDING:
            return True

    def launch(self):
        # This is called when we initially start building the node,
        # but it can also be called multiple times in case we retry
        # the launch.
        self.attempts += 1
        if self.attempts == 1:
            # On our first attempt, submit it to the threadpool worker
            # so the initial node lock happens asynchronously.
            self.start_future = self.manager.state_machine_start_worker.submit(
                self.startStateMachine)
        else:
            # On subsequent attempts, run this synchronously since
            # we're out of the _assignHandlers thread.
            self.startStateMachine()

    def startStateMachine(self):
        label = self.handler.pool.labels[self.node.type[0]]
        if label.diskimage:
            diskimage = self.provider_config.diskimages[
                label.diskimage.name]
            cloud_image = self.zk.getMostRecentImageUpload(
                diskimage.name, self.provider_config.name, cached=True)

            if not cloud_image:
                raise exceptions.LaunchNodepoolException(
                    "Unable to find current cloud image %s in %s" %
                    (diskimage.name, self.provider_config.name)
                )

            image_external_id = cloud_image.external_id
            self.node.image_id = "{path}/{upload_id}".format(
                path=self.zk._imageUploadPath(
                    cloud_image.image_name,
                    cloud_image.build_id,
                    cloud_image.provider_name),
                upload_id=cloud_image.id)
            image = diskimage
        else:
            image_external_id = None
            self.node.image_id = label.cloud_image.name
            image = label.cloud_image

        self.node.username = image.username
        self.node.python_path = image.python_path
        self.node.shell_type = image.shell_type
        self.node.connection_port = image.connection_port
        self.node.connection_type = image.connection_type
        qi = self.manager.quotaNeededByLabel(label.name, self.handler.pool)
        if qi:
            self.node.resources = qi.get_resources()

        self.zk.storeNode(self.node)

        # Windows computer names can be no more than 15 chars long.
        hostname = 'np' + self.node.id
        metadata = {'nodepool_node_id': self.node.id,
                    'nodepool_pool_name': self.handler.pool.name,
                    'nodepool_provider_name': self.manager.provider.name}
        self.state_machine = self.manager.adapter.getCreateStateMachine(
            hostname, label, image_external_id, metadata,
            self.handler.request, self.handler.chosen_az, self.log)

    def updateNodeFromInstance(self, instance):
        if instance is None:
            return

        node = self.node
        pool = self.handler.pool

        if (pool.use_internal_ip and
            (instance.private_ipv4 or instance.private_ipv6)):
            server_ip = instance.private_ipv4 or instance.private_ipv6
        else:
            server_ip = instance.interface_ip

        node.external_id = instance.external_id
        node.interface_ip = server_ip
        node.public_ipv4 = instance.public_ipv4
        node.private_ipv4 = instance.private_ipv4
        node.public_ipv6 = instance.public_ipv6
        node.host_id = instance.host_id
        node.cloud = instance.cloud
        node.region = instance.region
        node.az = instance.az
        node.driver_data = instance.driver_data
        node.slot = instance.slot

        # Optionally, if the node has updated values that we set from
        # the image attributes earlier, set those.
        for attr in ('username', 'python_path', 'shell_type',
                     'connection_port', 'connection_type',
                     'host_keys'):
            if hasattr(instance, attr):
                setattr(node, attr, getattr(instance, attr))

        # As a special case for metastatic, if we got node_attributes
        # from the backing driver, use them as default values and let
        # the values from the pool override.
        instance_node_attrs = getattr(instance, 'node_attributes', None)
        if instance_node_attrs is not None:
            attrs = instance_node_attrs.copy()
            if node.attributes:
                attrs.update(node.attributes)
            node.attributes = attrs

        self.zk.storeNode(node)

    def runDeleteStateMachine(self):
        # This is similar to StateMachineNodeDeleter.runStateMachine,
        # but the error handling and cleanup are different.
        # Return True if there is no delete work to do

        if self.delete_state_machine is None:
            return True

        state_machine = self.delete_state_machine
        node = self.node

        if state_machine.complete:
            self.delete_state_machine = None
            return True

        try:
            if state_machine.external_id:
                old_state = state_machine.state
                state_machine.advance()
                if state_machine.state != old_state:
                    self.log.debug(
                        "Launch-delete state machine for %s advanced "
                        "from %s to %s",
                        node.id, old_state, state_machine.state)
            else:
                state_machine.complete = True
                self.delete_state_machine = None
                return True

            if not state_machine.complete:
                return

        except exceptions.NotFound:
            self.log.info("Instance %s not found in provider %s",
                          state_machine.external_id, node.provider)
            state_machine.complete = True
            self.delete_state_machine = None
            return True
        except Exception:
            self.log.exception("Error in launch-delete state machine:")
            # We must keep trying the delete until timeout in
            # order to avoid having two servers for the same
            # node id.
            self.delete_state_machine = \
                self.manager.adapter.getDeleteStateMachine(
                    state_machine.external_id, self.log)

    def runStateMachine(self):
        instance = None
        node = self.node
        statsd_key = 'ready'

        try:
            if self.state_machine is None:
                if self.start_future and self.start_future.done():
                    self.start_future.result()
                else:
                    return
            state_machine = self.state_machine
            if self.start_time is None:
                self.start_time = time.monotonic()
            if (state_machine.complete and self.nodescan_request
                and self.nodescan_request.complete):
                try:
                    keys = self.nodescan_request.result()
                    dt = int(time.monotonic() -
                             self.nodescan_request.start_time)
                    self.log.debug("Scanned keys in %s", dt)
                except Exception:
                    self.log.exception("Exception scanning keys:")
                    raise exceptions.LaunchKeyscanException(
                        "Can't scan instance %s key" % node.id)
                if keys:
                    node.host_keys = keys
                self.log.debug(f"Node {node.id} is ready")
                node.state = zk.READY
                self.zk.storeNode(node)
                try:
                    dt = int((time.monotonic() - self.start_time) * 1000)
                    self.recordLaunchStats(statsd_key, dt)
                except Exception:
                    self.log.exception("Exception while reporting stats:")
                return True

            now = time.monotonic()
            if (now - state_machine.start_time >
                self.manager.provider.launch_timeout):
                raise Exception("Timeout waiting for instance creation")

            if not self.runDeleteStateMachine():
                return

            old_state = state_machine.state
            instance = state_machine.advance()
            if state_machine.state != old_state:
                self.log.debug("State machine for %s advanced from %s to %s",
                               node.id, old_state, state_machine.state)
            if not node.external_id and state_machine.external_id:
                node.external_id = state_machine.external_id
                self.zk.storeNode(node)
            if state_machine.complete and not self.nodescan_request:
                self.updateNodeFromInstance(instance)
                self.log.debug("Submitting nodescan request for %s",
                               node.interface_ip)
                label = self.handler.pool.labels[self.node.type[0]]
                self.nodescan_request = NodescanRequest(
                    node,
                    label.host_key_checking,
                    self.manager.provider.boot_timeout,
                    self.log)
                self.manager.nodescan_worker.addRequest(self.nodescan_request)
        except kze.SessionExpiredError:
            # Our node lock is gone, leaving the node state as BUILDING.
            # This will get cleaned up in ZooKeeper automatically, but we
            # must still set our cached node state to FAILED for the
            # NodeLaunchManager's poll() method.
            self.log.error(
                "Lost ZooKeeper session trying to launch for node %s",
                node.id)
            node.state = zk.FAILED
            if self.state_machine:
                node.external_id = self.state_machine.external_id
            statsd_key = 'error.zksession'
            self.manager.nodescan_worker.removeRequest(self.nodescan_request)
            self.nodescan_request = None
        except exceptions.QuotaException:
            self.log.info("Aborting node %s due to quota failure", node.id)
            node.state = zk.ABORTED
            if self.state_machine:
                node.external_id = self.state_machine.external_id
            self.zk.storeNode(node)
            statsd_key = 'error.quota'
            self.manager.invalidateQuotaCache()
            self.manager.nodescan_worker.removeRequest(self.nodescan_request)
            self.nodescan_request = None
        except Exception as e:
            self.log.exception("Launch attempt %d/%d for node %s, failed:",
                               self.attempts, self.retries, node.id)
            if self.state_machine and self.state_machine.external_id:
                # If we're deleting, don't overwrite the node external
                # id, because we may make another delete state machine
                # below.
                node.external_id = self.state_machine.external_id
                self.zk.storeNode(node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'
            try:
                dt = int((time.monotonic() - self.start_time) * 1000)
                self.recordLaunchStats(statsd_key, dt)
            except Exception:
                self.log.exception("Exception while reporting stats:")

            if isinstance(e, exceptions.LaunchKeyscanException):
                try:
                    label = self.handler.pool.labels[node.type[0]]
                    self.manager.adapter.notifyNodescanFailure(
                        label, node.external_id)
                    console = self.manager.adapter.getConsoleLog(
                        label, node.external_id)
                    if console:
                        self.log.info('Console log from external id %s:',
                                      node.external_id)
                        for line in console.splitlines():
                            self.log.info(line.rstrip())
                except NotImplementedError:
                    pass
                except Exception:
                    self.log.exception("Exception while logging console:")

            self.manager.nodescan_worker.removeRequest(self.nodescan_request)
            self.nodescan_request = None

            if self.attempts >= self.retries:
                node.state = zk.FAILED
                return True
            else:
                # Before retrying the launch, delete what we have done
                # so far.  This is accomplished using a delete state
                # machine which we run inside this method to unwind
                # the launch.  Once that is finished, we will restart
                # the launch as normal.  The launch method below
                # prepares everything for the next launch (and
                # increments the count of attempts).
                self.delete_state_machine = \
                    self.manager.adapter.getDeleteStateMachine(
                        node.external_id, self.log)
                self.launch()
                return

        if node.state != zk.BUILDING:
            try:
                dt = int((time.monotonic() - self.start_time) * 1000)
                self.recordLaunchStats(statsd_key, dt)
            except Exception:
                self.log.exception("Exception while reporting stats:")
            return True


class StateMachineNodeDeleter:
    """The state of the state machine.

    This driver collects state machines from the underlying cloud
    adapter implementations; those state machines handle building the
    node.  But we still have our own accounting and a little bit of
    work to do on either side of that process, so this data structure
    holds the extra information we need for that.
    """

    DELETE_TIMEOUT = 600

    def __init__(self, zk, provider_manager, node):
        # Based on utils.NodeDeleter
        logger = logging.getLogger(
            "nodepool.StateMachineNodeDeleter."
            f"{provider_manager.provider.name}")
        self.log = get_annotated_logger(logger,
                                        node_id=node.id)
        self.manager = provider_manager
        self.zk = zk
        # Note: the node is locked
        self.node = node
        # Local additions:
        self.start_time = time.monotonic()
        self.state_machine = self.manager.adapter.getDeleteStateMachine(
            node.external_id, self.log)

    @property
    def complete(self):
        return self.state_machine.complete

    def runStateMachine(self):
        state_machine = self.state_machine
        node = self.node
        node_exists = (node.id is not None)

        if state_machine.complete:
            return True

        try:
            now = time.monotonic()
            if now - state_machine.start_time > self.DELETE_TIMEOUT:
                raise Exception("Timeout waiting for instance deletion")

            if state_machine.state == state_machine.START:
                node.state = zk.DELETING
                self.zk.storeNode(node)

            if node.external_id:
                old_state = state_machine.state
                state_machine.advance()
                if state_machine.state != old_state:
                    self.log.debug("State machine for %s advanced "
                                   "from %s to %s",
                                   node.id, old_state, state_machine.state)
            else:
                state_machine.complete = True

            if not state_machine.complete:
                return

        except exceptions.NotFound:
            self.log.info("Instance %s not found in provider %s",
                          node.external_id, node.provider)
        except Exception:
            self.log.exception("Exception deleting instance %s from %s:",
                               node.external_id, node.provider)
            # Don't delete the ZK node in this case, but do unlock it
            if node_exists:
                self.zk.unlockNode(node)
            state_machine.complete = True
            return

        if node_exists:
            self.log.info(
                "Deleting ZK node id=%s, state=%s, external_id=%s",
                node.id, node.state, node.external_id)
            # This also effectively releases the lock
            self.zk.deleteNode(node)
            self.manager.nodeDeletedNotification(node)
        return True

    def join(self):
        # This is used by the CLI for synchronous deletes
        while self in self.manager.deleters:
            time.sleep(0)


class StateMachineHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.StateMachineHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self.chosen_az = None
        self.launchers = []

    @property
    def alive_thread_count(self):
        return len([nl for nl in self.launchers if nl.complete])

    def imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        :returns: True if it is available, False otherwise.
        '''
        for label in self.request.node_types:
            if self.pool.labels[label].cloud_image:
                if not self.manager.labelReady(self.pool.labels[label]):
                    return False
            else:
                if not self.zk.getMostRecentImageUpload(
                        self.pool.labels[label].diskimage.name,
                        self.provider.name, cached=True):
                    return False
        return True

    def hasProviderQuota(self, node_types):
        '''
        Checks if a provider has enough quota to handle a list of nodes.
        This does not take our currently existing nodes into account.

        :param node_types: list of node types to check
        :return: True if the node list fits into the provider, False otherwise
        '''
        needed_quota = QuotaInformation()

        for ntype in node_types:
            needed_quota.add(
                self.manager.quotaNeededByLabel(ntype, self.pool))

        ignore = False
        if hasattr(self.pool, 'ignore_provider_quota'):
            ignore = self.pool.ignore_provider_quota
        if not ignore:
            cloud_quota = self.manager.estimatedNodepoolQuota()
            cloud_quota.subtract(needed_quota)

            if not cloud_quota.non_negative():
                return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        args = dict(
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf,
        )
        if getattr(self.pool, 'max_volumes', None):
            args['volumes'] = self.pool.max_volumes
        if getattr(self.pool, 'max_volume_gb', None):
            args['volume_gb'] = self.pool.max_volume_gb
        args.update(getattr(self.pool, 'max_resources', {}))
        pool_quota = QuotaInformation(**args)
        pool_quota.subtract(needed_quota)
        return pool_quota.non_negative()

    def hasRemainingQuota(self, ntype):
        '''
        Checks if the predicted quota is enough for an additional node of type
        ntype.

        :param ntype: node type for the quota check
        :return: True if there is enough quota, False otherwise
        '''
        needed_quota = self.manager.quotaNeededByLabel(ntype, self.pool)
        self.log.debug("Needed quota: %s", needed_quota)

        if not self.pool.ignore_provider_quota:
            # Calculate remaining quota which is calculated as:
            # quota = <total nodepool quota> - <used quota> - <quota for node>
            cloud_quota = self.manager.estimatedNodepoolQuota()
            cloud_quota.subtract(
                self.manager.estimatedNodepoolQuotaUsed())
            cloud_quota.subtract(needed_quota)
            self.log.debug("Predicted remaining provider quota: %s",
                           cloud_quota)

            if not cloud_quota.non_negative():
                return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        args = dict(
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf,
        )
        if getattr(self.pool, 'max_volumes', None):
            args['volumes'] = self.pool.max_volumes
        if getattr(self.pool, 'max_volume_gb', None):
            args['volume-gb'] = self.pool.max_volume_gb
        args.update(getattr(self.pool, 'max_resources', {}))
        pool_quota = QuotaInformation(**args)
        pool_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed(self.pool))
        self.log.debug("Current pool quota: %s" % pool_quota)
        pool_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining pool quota: %s", pool_quota)

        return pool_quota.non_negative()

    def checkReusableNode(self, node):
        if self.chosen_az and node.az != self.chosen_az:
            return False
        return True

    def nodeReusedNotification(self, node):
        """
        We attempt to group the node set within the same provider availability
        zone.
        For this to work properly, the provider entry in the nodepool
        config must list the availability zones. Otherwise, new node placement
        will be determined by the cloud. The exception being if there is an
        existing node in the READY state that we can select for this node set.
        Its AZ will then be used for new nodes, as well as any other READY
        nodes.
        """
        # If we haven't already chosen an AZ, select the
        # AZ from this ready node. This will cause new nodes
        # to share this AZ, as well.
        if not self.chosen_az and node.az:
            self.chosen_az = node.az

    def setNodeMetadata(self, node):
        """
        Select grouping AZ if we didn't set AZ from a selected,
        pre-existing node
        """
        if not self.chosen_az:
            self.chosen_az = random.choice(
                self.pool.azs or self.manager.adapter.getAZs())

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY, FAILED
        or ABORTED), we'll know all threads have finished the launch process.
        '''
        all_complete = True
        for launcher in self.launchers:
            if not launcher.complete:
                all_complete = False

        return all_complete

    def launch(self, node):
        launcher = StateMachineNodeLauncher(self, node, self.provider)
        launcher.launch()
        self.launchers.append(launcher)
        self.manager.launchers.append(launcher)


class StateMachineProvider(Provider, QuotaSupport):

    """The Provider implementation for the StateMachineManager driver
       framework"""
    # Loop interval with no state machines
    MINIMUM_SLEEP = 1
    # Max loop interval when state machines are running
    MAXIMUM_SLEEP = 1

    def __init__(self, adapter, provider):
        self.log = logging.getLogger(
            f"nodepool.StateMachineProvider.{provider.name}")
        super().__init__()
        self._statsd = stats.get_client()
        self.provider = provider
        self.adapter = adapter
        # State machines
        self.deleters = []
        self.launchers = []
        self._zk = None
        self.nodescan_worker = NodescanWorker()
        self.create_state_machine_thread = None
        self.delete_state_machine_thread = None
        self.start_machine_start_worker = None
        self.running = False
        num_labels = sum([len(pool.labels)
                          for pool in provider.pools.values()])
        self.label_quota_cache = cachetools.LRUCache(num_labels)
        self.possibly_leaked_nodes = {}
        self.possibly_leaked_uploads = {}
        self.stop_thread = None
        self.error_labels = set()

    def start(self, zk_conn):
        super().start(zk_conn)
        self.running = True
        self._zk = zk_conn

        self.nodescan_worker.start()
        self.create_state_machine_thread = threading.Thread(
            target=self._runCreateStateMachines,
            daemon=True)
        self.create_state_machine_thread.start()
        self.delete_state_machine_thread = threading.Thread(
            target=self._runDeleteStateMachines,
            daemon=True)
        self.delete_state_machine_thread.start()
        # This is mostly ZK operations so we don't expect to need as
        # much parallelism.
        workers = 8
        self.log.info("Create state machiner starter with max workers=%s",
                      workers)
        self.state_machine_start_worker = ThreadPoolExecutor(
            thread_name_prefix=f'start-{self.provider.name}',
            max_workers=workers)

    def stop(self):
        self.log.debug("Stopping")
        self.stop_thread = threading.Thread(
            target=self._stop,
            daemon=True)
        self.stop_thread.start()

    def _stop(self):
        if (self.create_state_machine_thread or
            self.delete_state_machine_thread):
            while self.launchers or self.deleters:
                time.sleep(1)
            self.running = False
        self.nodescan_worker.stop()
        if self.state_machine_start_worker:
            self.state_machine_start_worker.shutdown()
        self.adapter.stop()
        self.log.debug("Stopped")

    def idle(self):
        pass

    def join(self):
        self.log.debug("Joining")
        if self.create_state_machine_thread:
            self.create_state_machine_thread.join()
        if self.delete_state_machine_thread:
            self.delete_state_machine_thread.join()
        if self.stop_thread:
            self.stop_thread.join()
        self.nodescan_worker.join()
        self.log.debug("Joined")

    def _runStateMachines(self, create_or_delete, state_machines):
        while self.running:
            to_remove = []
            loop_start = time.monotonic()
            if state_machines:
                self.log.debug("Running %s %s state machines",
                               len(state_machines), create_or_delete)
            for sm in state_machines:
                try:
                    node_id = None
                    if sm.node:
                        node_id = sm.node.id
                    sm.runStateMachine()
                    if sm.complete:
                        self.log.debug(
                            f"Removing {create_or_delete} state machine "
                            f"for {node_id} from runner")
                        to_remove.append(sm)
                except Exception:
                    self.log.exception(
                        f"Error running {create_or_delete} state machine "
                        f"for {node_id}:")
            for sm in to_remove:
                state_machines.remove(sm)
            loop_end = time.monotonic()
            if state_machines or to_remove:
                self.log.debug("Ran %s %s state machines in %s seconds",
                               len(state_machines), create_or_delete,
                               loop_end - loop_start)
            if state_machines:
                time.sleep(max(0, self.MAXIMUM_SLEEP -
                               (loop_end - loop_start)))
            else:
                time.sleep(self.MINIMUM_SLEEP)

    def _runCreateStateMachines(self):
        self._runStateMachines("create", self.launchers)

    def _runDeleteStateMachines(self):
        self._runStateMachines("delete", self.deleters)

    def getRequestHandler(self, poolworker, request):
        return StateMachineHandler(poolworker, request)

    def labelReady(self, label):
        return self.adapter.labelReady(label)

    def getProviderLimits(self):
        try:
            return self.adapter.getQuotaLimits()
        except NotImplementedError:
            return QuotaInformation(
                cores=math.inf,
                instances=math.inf,
                ram=math.inf,
                default=math.inf)

    def quotaNeededByLabel(self, ntype, pool):
        provider_label = pool.labels[ntype]
        qi = self.label_quota_cache.get(provider_label)
        if qi is not None:
            return qi
        try:
            qi = self.adapter.getQuotaForLabel(provider_label)
            self.log.debug("Quota required for %s: %s",
                           provider_label.name, qi)
        except exceptions.RuntimeConfigurationException as e:
            # The adapter reported a permanent inability to work with
            # this label, so we take the label offline and return an
            # empty quota info object so that the request can proceed
            # and then fail.
            self.log.error("Error getting quota for %s and "
                           "marking label permanently unavailable: %s",
                           provider_label.name, str(e))
            self.error_labels.add(ntype)
            qi = QuotaInformation()
        except NotImplementedError:
            qi = QuotaInformation()
        self.label_quota_cache.setdefault(provider_label, qi)
        return qi

    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        used_quota = QuotaInformation()

        node_ids = set([n.id for n in self._zk.nodeIterator(cached_ids=True)])

        for instance in self.adapter.listInstances():
            meta = instance.metadata
            nodepool_provider_name = meta.get('nodepool_provider_name')
            if (nodepool_provider_name and
                nodepool_provider_name == self.provider.name):
                # This provider (regardless of the launcher) owns this
                # node so it must not be accounted for unmanaged
                # quota; unless it has leaked.
                nodepool_node_id = meta.get('nodepool_node_id')
                if nodepool_node_id and nodepool_node_id in node_ids:
                    # It has not leaked.
                    continue

            try:
                qi = instance.getQuotaInformation()
            except NotImplementedError:
                qi = QuotaInformation()
            used_quota.add(qi)

        return used_quota

    def errorLabels(self):
        return list(self.error_labels)

    def startNodeCleanup(self, node):
        nd = StateMachineNodeDeleter(self._zk, self, node)
        self.deleters.append(nd)
        return nd

    def cleanupNode(self, external_id):
        # This is no longer used due to our custom NodeDeleter
        raise NotImplementedError()

    def waitForNodeCleanup(self, external_id, timeout=600):
        # This is no longer used due to our custom NodeDeleter
        raise NotImplementedError()

    def cleanupLeakedResources(self):
        known_nodes = set()

        for node in self._zk.nodeIterator(cached_ids=True):
            if node.provider != self.provider.name:
                continue
            known_nodes.add(node.id)

        known_uploads = set()
        uploads = self._zk.getProviderUploads(self.provider.name)
        for image in uploads.values():
            for build in image.values():
                for upload in build:
                    known_uploads.add(upload.id)

        newly_leaked_nodes = {}
        newly_leaked_uploads = {}
        for resource in self.adapter.listResources():
            pn = resource.metadata.get('nodepool_provider_name')
            if pn != self.provider.name:
                continue
            node_id = resource.metadata.get('nodepool_node_id')
            upload_id = resource.metadata.get('nodepool_upload_id')
            if node_id and node_id not in known_nodes:
                newly_leaked_nodes[node_id] = resource
                if node_id in self.possibly_leaked_nodes:
                    # We've seen this twice now, so it's not a race
                    # condition.
                    try:
                        self.adapter.deleteResource(resource)
                        if self._statsd:
                            key = ('nodepool.provider.%s.leaked.%s'
                                   % (self.provider.name,
                                      resource.plural_metric_name))
                            self._statsd.incr(key, 1)
                    except Exception:
                        self.log.exception("Unable to delete leaked "
                                           f"resource for node {node_id}")
            if upload_id and upload_id not in known_uploads:
                newly_leaked_uploads[upload_id] = resource
                if upload_id in self.possibly_leaked_uploads:
                    # We've seen this twice now, so it's not a race
                    # condition.
                    try:
                        self.adapter.deleteResource(resource)
                        if self._statsd:
                            key = ('nodepool.provider.%s.leaked.%s'
                                   % (self.provider.name,
                                      resource.plural_metric_name))
                            self._statsd.incr(key, 1)
                    except Exception:
                        self.log.exception("Unable to delete leaked "
                                           f"resource for upload {upload_id}")
        self.possibly_leaked_nodes = newly_leaked_nodes
        self.possibly_leaked_uploads = newly_leaked_uploads

    # Image handling

    def uploadImage(self, provider_image, image_name, filename,
                    image_type=None, meta=None, md5=None, sha256=None):
        meta = meta.copy()
        meta['nodepool_provider_name'] = self.provider.name
        return self.adapter.uploadImage(provider_image, image_name,
                                        filename,
                                        image_format=image_type,
                                        metadata=meta, md5=md5,
                                        sha256=sha256)

    def deleteImage(self, name, id):
        return self.adapter.deleteImage(external_id=id)


# Driver implementation

class StateMachineDriver(Driver):
    """Entrypoint for a state machine driver"""

    def getProvider(self, provider_config):
        # Return a provider.
        # Usually this method does not need to be overridden.
        adapter = self.getAdapter(provider_config)
        return StateMachineProvider(adapter, provider_config)

    # Public interface

    def getProviderConfig(self, provider):
        """Instantiate a config object

        :param dict provider: A dictionary of YAML config describing
            the provider.
        :returns: A ProviderConfig instance with the parsed data.
        """
        raise NotImplementedError()

    def getAdapter(self, provider_config):
        """Instantiate an adapter

        :param ProviderConfig provider_config: An instance of
            ProviderConfig previously returned by :py:meth:`getProviderConfig`.
        :returns: An instance of :py:class:`Adapter`
        """
        raise NotImplementedError()


# Adapter public interface

class Instance:
    """Represents a cloud instance

    This class is used by the State Machine Driver classes to
    represent a standardized version of a remote cloud instance.
    Implement this class in your driver, override the :py:meth:`load`
    method, and supply as many of the fields as possible.

    The following attributes are required:

    * ready: bool (whether the instance is ready)
    * deleted: bool (whether the instance is in a deleted state)
    * external_id: str (the unique id of the instance)
    * interface_ip: str
    * metadata: dict

    The following are optional:

    * public_ipv4: str
    * public_ipv6: str
    * private_ipv4: str
    * cloud: str
    * az: str
    * region: str
    * host_id: str
    * driver_data: any
    * slot: int

    And the following are even more optional (as they are usually
    already set from the image configuration):

    * username: str
    * python_path: str
    * shell_type: str
    * connection_port: str
    * connection_type: str
    * host_keys: [str]

    This is extremely optional, in fact, it's difficult to imagine
    that it's useful for anything other than the metastatic driver:

    * node_attributes: dict

    """
    def __init__(self):
        self.ready = False
        self.deleted = False
        self.external_id = None
        self.public_ipv4 = None
        self.public_ipv6 = None
        self.private_ipv4 = None
        self.interface_ip = None
        self.cloud = None
        self.az = None
        self.region = None
        self.host_id = None
        self.metadata = {}
        self.driver_data = None
        self.slot = None

    def __repr__(self):
        state = []
        if self.ready:
            state.append('ready')
        if self.deleted:
            state.append('deleted')
        state = ' '.join(state)
        return '<{klass} {external_id} {state}>'.format(
            klass=self.__class__.__name__,
            external_id=self.external_id,
            state=state)

    def getQuotaInformation(self):
        """Return quota information about this instance.

        :returns: A :py:class:`QuotaInformation` object.
        """
        raise NotImplementedError()


class Resource:
    """Represents a cloud resource

    This could be an instance, a disk, a floating IP, or anything
    else.  It is used by the driver to detect leaked resources so the
    adapter can clean them up.

    The `type` attribute should be an alphanumeric string suitable for
    inclusion in a statsd metric name.

    The `metadata` attribute is a dictionary of key/value pairs
    initially supplied by the driver to the adapter when an instance
    or image was created.  This is used by the driver to detect leaked
    resources.  The adapter may add any other information to this
    instance for its own bookeeping (resource type, id, etc).

    The 'plural_metric_name' attribute is set in the constructor
    automatically; override this value if necessary.

    :param str type: The type of resource.
    :param dict metadata: A dictionary of metadata for the resource.

    """

    def __init__(self, metadata, type):
        self.type = type
        self.plural_metric_name = type + 's'
        self.metadata = metadata


class StateMachine:
    START = 'start'

    def __init__(self):
        self.state = self.START
        self.external_id = None
        self.complete = False
        self.start_time = time.monotonic()

    def advance(self):
        pass


class NodescanEvent(threading.Event):
    """A subclass of event that will wake the NodescanWorker poll"""
    def __init__(self, worker, *args, **kw):
        super().__init__(*args, **kw)
        self._zuul_worker = worker

    def set(self):
        super().set()
        try:
            os.write(self._zuul_worker.wake_write, b'\n')
        except Exception:
            pass


class NodescanWorker:
    """Handles requests for nodescans.

    This class has a single thread that drives nodescan requests
    submitted by any provider's pools.

    """
    # This process is highly scalable, except for paramiko which
    # spawns a thread for each ssh connection.  To avoid thread
    # overload, we set a max value for concurrent requests.
    # Simultaneous requests higher than this value will be queued.
    MAX_REQUESTS = 100

    def __init__(self):
        self.wake_read, self.wake_write = os.pipe()
        fcntl.fcntl(self.wake_read, fcntl.F_SETFL, os.O_NONBLOCK)
        self._running = False
        self._active_requests = []
        self._pending_requests = []
        self.poll = select.epoll()
        self.poll.register(self.wake_read, select.EPOLLIN)

    def start(self):
        self._running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self._running = False
        os.write(self.wake_write, b'\n')

    def join(self):
        self.thread.join()

    def addRequest(self, request):
        """Submit a nodescan request"""
        request.setWorker(self)
        if len(self._active_requests) >= self.MAX_REQUESTS:
            self._pending_requests.append(request)
        else:
            self._active_requests.append(request)
            # If the poll is sleeping, wake it up for immediate action
            os.write(self.wake_write, b'\n')

    def removeRequest(self, request):
        """Remove the request and cleanup"""
        if request is None:
            return
        request.cleanup()
        try:
            self._active_requests.remove(request)
        except ValueError:
            pass
        try:
            self._pending_requests.remove(request)
        except ValueError:
            pass

    def registerDescriptor(self, fd):
        """Register the fd with the poll object"""
        # Oneshot means that once it triggers, it will automatically
        # be removed.  That's great for us since we only use this for
        # detecting when the initial connection is complete and have
        # no further use.
        self.poll.register(
            fd, select.EPOLLOUT | select.EPOLLERR |
            select.EPOLLHUP | select.EPOLLONESHOT)

    def unRegisterDescriptor(self, fd):
        """Unregister the fd with the poll object"""
        try:
            self.poll.unregister(fd)
        except Exception:
            pass

    def _advance(self, request, socket_ready):
        # A helper method to encapsulate the advance + log sequence
        old_state = request.state
        request.advance(socket_ready)
        if request.state != old_state:
            request.log.debug(
                "Nodescan request for %s advanced "
                "from %s to %s %s",
                request.node.id, old_state, request.state, request.iteration)

    def run(self):
        while self._running:
            # Set the poll timeout to 1 second so that we check all
            # requests for timeouts every second.  This could be
            # increased to a few seconds without significant impact.
            timeout = 1
            while (self._pending_requests and
                   len(self._active_requests) < self.MAX_REQUESTS):
                # If we have room for more requests, add them and set
                # the timeout to 0 so that we immediately start
                # advancing them.
                request = self._pending_requests.pop(0)
                self._active_requests.append(request)
                timeout = 0
            ready = self.poll.poll(timeout=timeout)
            ready = [x[0] for x in ready]
            if self.wake_read in ready:
                # Empty the wake pipe
                while True:
                    try:
                        os.read(self.wake_read, 1024)
                    except BlockingIOError:
                        break
            for request in self._active_requests:
                try:
                    socket_ready = (request.sock and
                                    request.sock.fileno() in ready)
                    self._advance(request, socket_ready)
                except Exception as e:
                    request.fail(e)
                if request.complete:
                    self.removeRequest(request)


class NodescanRequest:
    """A state machine for a nodescan request.

    When complete, use the result() method to obtain the keys or raise
    an exception if an errer was encountered during processing.

    """

    START = 'start'
    CONNECTING_INIT = 'connecting'
    NEGOTIATING_INIT = 'negotiating'
    CONNECTING_KEY = 'connecting key'
    NEGOTIATING_KEY = 'negotiating key'
    COMPLETE = 'complete'
    # For unit testing
    FAKE = False

    def __init__(self, node, host_key_checking, timeout, log):
        self.state = self.START
        self.iteration = 'init'
        self.node = node
        self.host_key_checking = host_key_checking
        self.timeout = timeout
        self.log = log
        self.complete = False
        self.keys = []
        if (node.connection_type == 'ssh' or
            node.connection_type == 'network_cli'):
            self.gather_hostkeys = True
        else:
            self.gather_hostkeys = False
        self.ip = node.interface_ip
        self.port = node.connection_port
        if 'fake' not in self.ip and not self.FAKE:
            addrinfo = socket.getaddrinfo(self.ip, self.port)[0]
            self.family = addrinfo[0]
            self.sockaddr = addrinfo[4]
        self.sock = None
        self.transport = None
        self.event = None
        self.key_types = None
        self.key_index = None
        self.key_type = None
        self.start_time = time.monotonic()
        self.worker = None
        self.exception = None
        self.connect_start_time = None

    def setWorker(self, worker):
        """Store a reference to the worker thread so we register and unregister
        the socket file descriptor from the polling object"""
        self.worker = worker

    def fail(self, exception):
        """Declare this request a failure and store the related exception"""
        self.exception = exception
        self.cleanup()
        self.complete = True
        self.state = self.COMPLETE

    def cleanup(self):
        """Try to close everything and unregister from the worker"""
        self._close()

    def result(self):
        """Return the resulting keys, or raise an exception"""
        if self.exception:
            raise self.exception
        return self.keys

    def _close(self):
        if self.transport:
            try:
                self.transport.close()
            except Exception:
                pass
            self.transport = None
            self.event = None
        if self.sock:
            self.worker.unRegisterDescriptor(self.sock)
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _checkTimeout(self):
        now = time.monotonic()
        if now - self.start_time > self.timeout:
            raise Exception(
                f"Timeout connecting to {self.ip} on port {self.port}")

    def _checkTransport(self):
        # This stanza is from
        # https://github.com/paramiko/paramiko/blob/main/paramiko/transport.py
        if not self.transport.active:
            e = self.transport.get_exception()
            if e is not None:
                raise e
            raise paramiko.exceptions.SSHException("Negotiation failed.")

    def _connect(self):
        if self.sock:
            self.worker.unRegisterDescriptor(self.sock)
        self.sock = socket.socket(self.family, socket.SOCK_STREAM)
        # Set nonblocking so we can poll for connection completion
        self.sock.setblocking(False)
        try:
            self.sock.connect(self.sockaddr)
        except BlockingIOError:
            self.state = self.CONNECTING_INIT
        self.connect_start_time = time.monotonic()
        self.worker.registerDescriptor(self.sock)

    def _start(self):
        # Use our Event subclass that will wake the worker when the
        # event is set.
        self.event = NodescanEvent(self.worker)
        # Return the socket to blocking mode as we hand it off to paramiko.
        self.sock.setblocking(True)
        self.transport = paramiko.transport.Transport(self.sock)
        if self.key_type is not None:
            opts = self.transport.get_security_options()
            opts.key_types = [self.key_type]
        # This starts a thread.
        self.transport.start_client(
            event=self.event, timeout=self.timeout)

    def _nextKey(self):
        self._close()
        self.key_index += 1
        if self.key_index >= len(self.key_types):
            self.state = self.COMPLETE
            return True
        self.key_type = self.key_types[self.key_index]
        self._connect()
        self.state = self.CONNECTING_KEY
        self.iteration = self.key_type

    def advance(self, socket_ready):
        if self.state == self.START:
            if self.worker is None:
                raise Exception("Request not registered with worker")
            if not self.host_key_checking:
                self.state = self.COMPLETE
            else:
                if 'fake' in self.ip or self.FAKE:
                    if self.gather_hostkeys:
                        self.keys = ['ssh-rsa FAKEKEY']
                    self.state = self.COMPLETE
                else:
                    self._connect()

        if self.state == self.CONNECTING_INIT:
            if not socket_ready:
                # Check the overall timeout
                self._checkTimeout()
                # If we're still here, then don't let any individual
                # connection attempt last more than 10 seconds:
                if time.monotonic() - self.connect_start_time >= 10:
                    self._close()
                    self.state = self.START
                return
            eno = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if eno:
                if eno not in [errno.ECONNREFUSED, errno.EHOSTUNREACH]:
                    self.log.exception(
                        f"Error {eno} connecting to {self.ip} "
                        f"on port {self.port}")
                # Try again.  Don't immediately start to reconnect
                # since econnrefused can happen very quickly, so we
                # could end up busy-waiting.
                self._close()
                self.state = self.START
                self._checkTimeout()
                return
            if self.gather_hostkeys:
                self._start()
                self.state = self.NEGOTIATING_INIT
            else:
                self.state = self.COMPLETE

        if self.state == self.NEGOTIATING_INIT:
            if not self.event.is_set():
                self._checkTimeout()
                return
            # This will raise an exception on ssh errors
            try:
                self._checkTransport()
            except Exception:
                self.log.exception(
                    f"SSH error connecting to {self.ip} on port {self.port}")
                # Try again
                self._close()
                self.state = self.START
                self._checkTimeout()
                self._connect()
                return
            # This is our first successful connection.  Now that
            # we've done it, start again specifying the first key
            # type.
            opts = self.transport.get_security_options()
            self.key_types = opts.key_types
            self.key_index = -1
            self._nextKey()

        if self.state == self.CONNECTING_KEY:
            if not socket_ready:
                self._checkTimeout()
                return
            eno = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if eno:
                self.log.error(
                    f"Error {eno} connecting to {self.ip} on port {self.port}")
                self._nextKey()
                return
            self._start()
            self.state = self.NEGOTIATING_KEY

        if self.state == self.NEGOTIATING_KEY:
            if not self.event.is_set():
                self._checkTimeout()
                return
            # This will raise an exception on ssh errors
            try:
                self._checkTransport()
            except Exception as e:
                msg = str(e)
                if 'no acceptable host key' not in msg:
                    # We expect some host keys to not be valid
                    # when scanning only log if the error isn't
                    # due to mismatched host key types.
                    self.log.exception(
                        f"SSH error connecting to {self.ip} "
                        f"on port {self.port}")
                self._nextKey()

        # Check if we're still in the same state
        if self.state == self.NEGOTIATING_KEY:
            key = self.transport.get_remote_server_key()
            if key:
                self.keys.append("%s %s" % (key.get_name(), key.get_base64()))
                self.log.debug('Added ssh host key: %s', key.get_name())
            self._nextKey()

        if self.state == self.COMPLETE:
            self._close()
            self.complete = True


class Adapter:
    """Cloud adapter for the State Machine Driver

    This class will be instantiated once for each Nodepool provider.
    It may be discarded and replaced if the configuration changes.

    You may establish a single long-lived connection to the cloud in
    the initializer if you wish.

    :param ProviderConfig provider_config: A config object
        representing the provider.

    """
    def __init__(self, provider_config):
        pass

    def stop(self):
        """Release any resources as this provider is being stopped"""
        pass

    def getCreateStateMachine(self, hostname, label,
                              image_external_id, metadata,
                              log):
        """Return a state machine suitable for creating an instance

        This method should return a new state machine object
        initialized to create the described node.

        :param str hostname: The hostname of the node.
        :param ProviderLabel label: A config object representing the
            provider-label for the node.
        :param str image_external_id: If provided, the external id of
            a previously uploaded image; if None, then the adapter should
            look up a cloud image based on the label.
        :param metadata dict: A dictionary of metadata that must be
            stored on the instance in the cloud.  The same data must be
            able to be returned later on :py:class:`Instance` objects
            returned from `listInstances`.
        :param log Logger: A logger instance for emitting annotated
            logs related to the request.

        :returns: A :py:class:`StateMachine` object.

        """
        raise NotImplementedError()

    def getDeleteStateMachine(self, external_id, log):
        """Return a state machine suitable for deleting an instance

        This method should return a new state machine object
        initialized to delete the described instance.

        :param str external_id: The external_id of the instance, as
            supplied by a creation StateMachine or an Instance.
        :param log Logger: A logger instance for emitting annotated
            logs related to the request.
        """
        raise NotImplementedError()

    def listInstances(self):
        """Return an iterator of instances accessible to this provider.

        The yielded values should represent all instances accessible
        to this provider, not only those under the control of this
        adapter, but all visible instances in order to achive accurate
        quota calculation.

        :returns: A generator of :py:class:`Instance` objects.
        """
        raise NotImplementedError()

    def listResources(self):
        """Return a list of resources accessible to this provider.

        The yielded values should represent all resources accessible
        to this provider, not only those under the control of this
        adapter, but all visible instances in order for the driver to
        identify leaked resources and instruct the adapter to remove
        them.

        :returns: A generator of :py:class:`Resource` objects.
        """
        raise NotImplementedError()

    def deleteResource(self, resource):
        """Delete the supplied resource

        The driver has identified a leaked resource and the adapter
        should delete it.

        :param Resource resource: A Resource object previously
            returned by 'listResources'.
        """
        raise NotImplementedError()

    def getQuotaLimits(self):
        """Return the quota limits for this provider

        The default implementation returns a simple QuotaInformation
        with no limits.  Override this to provide accurate
        information.

        :returns: A :py:class:`QuotaInformation` object.

        """
        return QuotaInformation(default=math.inf)

    def getQuotaForLabel(self, label):
        """Return information about the quota used for a label

        The default implementation returns a simple QuotaInformation
        for one instance; override this to return more detailed
        information including cores and RAM.

        :param ProviderLabel label: A config object describing
            a label for an instance.

        :returns: A :py:class:`QuotaInformation` object.
        """
        return QuotaInformation(instances=1)

    def getAZs(self):
        """Return a list of availability zones for this provider

        One of these will be selected at random and supplied to the
        create state machine.  If a request handler is building a node
        set from an existing ready node, then the AZ from that node
        will be used instead of the results of this method.

        :returns: A list of availability zone names.
        """
        return [None]

    def labelReady(self, label):
        """Indicate whether a label is ready in the provided cloud

        This is used by the launcher to determine whether it should
        consider a label to be in-service for a provider.  If this
        returns False, the label will be ignored for this provider.

        This does not need to consider whether a diskimage is ready;
        the launcher handles that itself.  Instead, this can be used
        to determine whether a cloud-image is available.

        :param ProviderLabel label: A config object describing a label
            for an instance.

        :returns: A bool indicating whether the label is ready.
        """
        return True

    # The following methods must be implemented only if image
    # management is supported:

    def uploadImage(self, provider_image, image_name, filename,
                    image_format=None, metadata=None, md5=None,
                    sha256=None):
        """Upload the image to the cloud

        :param provider_image ProviderImageConfig:
            The provider's config for this image
        :param image_name str: The name of the image
        :param filename str: The path to the local file to be uploaded
        :param image_format str: The format of the image (e.g., "qcow")
        :param metadata dict: A dictionary of metadata that must be
            stored on the image in the cloud.
        :param md5 str: The md5 hash of the image file
        :param sha256 str: The sha256 hash of the image file

        :return: The external id of the image in the cloud
        """
        raise NotImplementedError()

    def deleteImage(self, external_id):
        """Delete an image from the cloud

        :param external_id str: The external id of the image to delete
        """
        raise NotImplementedError()

    # The following methods are optional
    def getConsoleLog(self, label, external_id):
        """Return the console log from the specified server

        :param label ConfigLabel: The label config for the node
        :param external_id str: The external id of the server
        """
        raise NotImplementedError()

    def notifyNodescanFailure(self, label, external_id):
        """Notify the adapter of a nodescan failure

        :param label ConfigLabel: The label config for the node
        :param external_id str: The external id of the server
        """
        pass
