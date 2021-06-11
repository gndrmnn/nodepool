# Copyright 2019 Red Hat
# Copyright 2021 Acme Gating, LLC
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


import time
import logging
import math
import threading
from concurrent.futures.thread import ThreadPoolExecutor

from nodepool.driver import Driver, NodeRequestHandler, Provider
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.nodeutils import nodescan
from nodepool.logconfig import get_annotated_logger
from nodepool import stats
from nodepool import exceptions
from nodepool import zk
from kazoo import exceptions as kze


def keyscan(node_id, interface_ip,
            connection_type, connection_port):
    """A standalone function for scanning keys to pass to a thread/process
    pool executor
    """

    keys = []
    try:
        if (connection_type == 'ssh' or
            connection_type == 'network_cli'):
            gather_hostkeys = True
        else:
            gather_hostkeys = False
        keys = nodescan(interface_ip, port=connection_port,
                        timeout=180, gather_hostkeys=gather_hostkeys)
    except Exception:
        raise exceptions.LaunchKeyscanException(
            "Can't scan instance %s key" % node_id)
    return keys


class StateMachineNodeLauncher(stats.StatsReporter):
    """The state of the state machine.

    This driver collects state machines from the underlying cloud
    adapter implementations; those state machines handle building the
    node.  But we still have our own accounting and a little bit of
    work to do on either side of that process, so this data structure
    holds the extra information we need for that.
    """

    def __init__(self, handler, node, provider_config):
        super().__init__()
        # Based on utils.NodeLauncher
        logger = logging.getLogger("nodepool.StateMachineNodeLauncher")
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
        self.state_machine = None
        self.keyscan_future = None
        self.manager = handler.manager
        self.start_time = None

    @property
    def complete(self):
        if self.node.state != zk.BUILDING:
            return True

    def launch(self):
        label = self.handler.pool.labels[self.node.type[0]]
        hostname = 'nodepool-' + self.node.id
        retries = self.manager.provider.launch_retries
        metadata = {'nodepool_node_id': self.node.id,
                    'nodepool_pool_name': self.handler.pool.name,
                    'nodepool_provider_name': self.manager.provider.name}
        self.state_machine = self.manager.adapter.getCreateStateMachine(
            hostname, label, metadata, retries)

    def updateNodeFromInstance(self, instance):
        if instance is None:
            return

        node = self.node
        pool = self.handler.pool
        label = pool.labels[self.node.type[0]]

        if pool.use_internal_ip and instance.private_ipv4:
            server_ip = instance.private_ipv4
        else:
            server_ip = instance.interface_ip

        node.external_id = instance.external_id
        node.interface_ip = server_ip
        node.public_ipv4 = instance.public_ipv4
        node.private_ipv4 = instance.private_ipv4
        node.public_ipv6 = instance.public_ipv6
        node.region = instance.region
        node.az = instance.az
        node.username = label.cloud_image.username
        node.python_path = label.cloud_image.python_path
        node.connection_port = label.cloud_image.connection_port
        node.connection_type = label.cloud_image.connection_type
        self.zk.storeNode(node)

    def runStateMachine(self):
        instance = None
        state_machine = self.state_machine
        node = self.node
        statsd_key = 'ready'

        if self.start_time is None:
            self.start_time = time.monotonic()

        try:
            if (self.state_machine.complete and self.keyscan_future
                and self.keyscan_future.done()):
                keys = self.keyscan_future.result()
                node.keys = keys
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
                self.manager.provider.boot_timeout):
                raise Exception("Timeout waiting for instance creation")
            instance = state_machine.advance()
            self.log.debug(f"State machine for {node.id} at "
                           f"{state_machine.state}")
            if not node.external_id:
                if not state_machine.external_id:
                    raise Exception("Driver implementation error: state "
                                    "machine must produce external ID "
                                    "after first advancement")
                self.updateNodeFromInstance(instance)
            if state_machine.complete:
                self.log.debug("Submitting keyscan request")
                self.updateNodeFromInstance(instance)
                future = self.manager.keyscan_worker.submit(
                    keyscan,
                    node.id, node.interface_ip,
                    node.connection_type, node.connection_port)
                self.keyscan_future = future
        except kze.SessionExpiredError:
            # Our node lock is gone, leaving the node state as BUILDING.
            # This will get cleaned up in ZooKeeper automatically, but we
            # must still set our cached node state to FAILED for the
            # NodeLaunchManager's poll() method.
            self.log.error(
                "Lost ZooKeeper session trying to launch for node %s",
                node.id)
            node.state = zk.FAILED
            node.external_id = state_machine.external_id
            statsd_key = 'error.zksession'
        except exceptions.QuotaException:
            self.log.info("Aborting node %s due to quota failure" % node.id)
            node.state = zk.ABORTED
            node.external_id = state_machine.external_id
            self.zk.storeNode(node)
            statsd_key = 'error.quota'
        except Exception as e:
            self.log.exception(
                "Launch failed for node %s:", node.id)
            node.state = zk.FAILED
            node.external_id = state_machine.external_id
            self.zk.storeNode(node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

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
        self.log = logging.getLogger("nodepool.StateMachineNodeDeleter")
        self.manager = provider_manager
        self.zk = zk
        # Note: the node is locked
        self.node = node
        # Local additions:
        self.start_time = time.monotonic()
        self.state_machine = self.manager.adapter.getDeleteStateMachine(
            node.external_id)

    @property
    def complete(self):
        return self.state_machine.complete

    def runStateMachine(self):
        state_machine = self.state_machine
        node = self.node
        node_exists = (node.id is not None)

        if self.state_machine.complete:
            return True

        try:
            now = time.monotonic()
            if now - state_machine.start_time > self.DELETE_TIMEOUT:
                raise Exception("Timeout waiting for instance deletion")

            if state_machine.state == state_machine.START:
                node.state = zk.DELETING
                self.zk.storeNode(node)

            if node.external_id:
                state_machine.advance()
                self.log.debug(f"State machine for {node.id} at "
                               "{state_machine.state}")

            if not self.state_machine.complete:
                return

        except exceptions.NotFound:
            self.log.info(f"Instance {node.external_id} not found in "
                          "provider {node.provider}")
        except Exception:
            self.log.exception("Exception deleting instance "
                               f"{node.external_id} from {node.provider}:")
            # Don't delete the ZK node in this case, but do unlock it
            if node_exists:
                self.zk.unlockNode(node)
            self.state_machine.complete = True
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
    log = logging.getLogger("nodepool.driver.simple."
                            "StateMachineHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self.launchers = []

    @property
    def alive_thread_count(self):
        return len([nl for nl in self.launchers if nl.complete])

    def imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        :returns: True if it is available, False otherwise.
        '''
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

        if hasattr(self.pool, 'ignore_provider_quota'):
            if not self.pool.ignore_provider_quota:
                cloud_quota = self.manager.estimatedNodepoolQuota()
                cloud_quota.subtract(needed_quota)

                if not cloud_quota.non_negative():
                    return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
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
        pool_quota = QuotaInformation(
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
        pool_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed(self.pool))
        self.log.debug("Current pool quota: %s" % pool_quota)
        pool_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining pool quota: %s", pool_quota)

        return pool_quota.non_negative()

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
    log = logging.getLogger("nodepool.driver.statemachine."
                            "StateMachineProvider")

    def __init__(self, adapter, provider):
        super().__init__()
        self.provider = provider
        self.adapter = adapter
        # State machines
        self.deleters = []
        self.launchers = []
        self._zk = None
        self.keyscan_worker = None
        self.state_machine_thread = None
        self.running = False

    def start(self, zk_conn):
        super().start(zk_conn)
        self.running = True
        self._zk = zk_conn
        self.keyscan_worker = ThreadPoolExecutor()
        self.state_machine_thread = threading.Thread(
            target=self._runStateMachines)
        self.state_machine_thread.start()

    def stop(self):
        self.running = False
        if self.keyscan_worker:
            self.keyscan_worker.shutdown()
        if self.state_machine_thread:
            self.running = False

    def join(self):
        if self.state_machine_thread:
            self.state_machine_thread.join()

    def _runStateMachines(self):
        while self.running:
            to_remove = []
            loop_start = time.monotonic()
            for sm in self.deleters + self.launchers:
                try:
                    sm.runStateMachine()
                    if sm.complete:
                        self.log.debug("Removing state machine from runner")
                        to_remove.append(sm)
                except Exception:
                    self.log.exception("Error running state machine:")
            for sm in to_remove:
                if sm in self.deleters:
                    self.deleters.remove(sm)
                if sm in self.launchers:
                    self.launchers.remove(sm)
            loop_end = time.monotonic()
            if self.launchers or self.deleters:
                time.sleep(max(0, 10 - (loop_end - loop_start)))
            else:
                time.sleep(1)

    def getRequestHandler(self, poolworker, request):
        return StateMachineHandler(poolworker, request)

    def labelReady(self, label):
        return True

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
        try:
            return self.adapter.getQuotaForLabel(provider_label)
        except NotImplementedError:
            return QuotaInformation()

    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        used_quota = QuotaInformation()

        node_ids = set([n.id for n in self._zk.nodeIterator()])

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

        for node in self._zk.nodeIterator():
            if node.provider != self.provider.name:
                continue
            known_nodes.add(node.id)

        metadata = {'nodepool_provider_name': self.provider.name}
        self.adapter.cleanupLeakedResources(known_nodes, metadata)


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
        :returns: An instance of :py:class:`SimpleTaskManagerAdapter`
        """
        raise NotImplementedError()


# Adapter public interface

class Instance:
    """Represents a cloud instance

    This class is used by the Simple Task Manager Driver classes to
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
    * az: str
    * region: str
    """
    def __init__(self):
        self.ready = False
        self.deleted = False
        self.external_id = None
        self.public_ipv4 = None
        self.public_ipv6 = None
        self.private_ipv4 = None
        self.interface_ip = None
        self.az = None
        self.region = None
        self.metadata = {}

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


class StateMachine:
    START = 'start'

    def __init__(self):
        self.state = self.START
        self.external_id = None
        self.complete = False
        self.start_time = time.monotonic()

    def advance(self):
        pass


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

    def getCreateStateMachine(self, hostname, label, metadata, retries):
        """Return a state machine suitable for creating an instance

        This method should return a new state machine object
        initialized to create the described node.

        :param str hostname: The hostname of the node.
        :param ProviderLabel label: A config object representing the
            provider-label for the node.
        :param metadata dict: A dictionary of metadata that must be
            stored on the instance in the cloud.  The same data must be
            able to be returned later on :py:class:`Instance` objects
            returned from `listInstances`.
        :param retries int: The number of attempts which should be
            made to launch the node.

        :returns: A :py:class:`StateMachine` object.

        """
        raise NotImplementedError()

    def getDeleteStateMachine(self, external_id):
        """Return a state machine suitable for deleting an instance

        This method should return a new state machine object
        initialized to delete the described instance.

        :param str external_id: The external_id of the instance, as
            supplied by a creation StateMachine or an Instance.
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

    def cleanupLeakedResources(self, known_nodes, metadata):
        """Delete any leaked resources

        Use the supplied metadata to delete any leaked resources.
        Typically you would list all of the instances in the provider,
        then list any other resources (floating IPs, etc).  Any
        resources with matching metadata not associated with an
        instance should be deleted, and any instances with matching
        metadata and whose node id is not in known_nodes should also
        be deleted.

        Look up the `nodepool_node_id` metadata attribute on cloud
        instances to compare to known_nodes.

        :param set known_nodes: A set of string values representing
            known node ids.
        :param dict metadata: Metadata values to compare with cloud
            instances.

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
