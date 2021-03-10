# Copyright 2019 Red Hat
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

from nodepool.driver.taskmanager import BaseTaskManagerProvider, Task
from nodepool.driver import Driver, NodeRequestHandler
from nodepool.driver.utils import NodeLauncher, QuotaInformation, QuotaSupport
from nodepool.driver.utils import NodeDeleter
from nodepool.nodeutils import iterate_timeout, nodescan
from nodepool import exceptions
from nodepool import zk


# Private support classes

class CreateInstanceTask(Task):
    name = 'create_instance'

    def main(self, manager):
        return self.args['adapter'].createInstance(
            manager, self.args['hostname'], self.args['metadata'],
            self.args['label_config'])


class DeleteInstanceTask(Task):
    name = 'delete_instance'

    def main(self, manager):
        return self.args['adapter'].deleteInstance(
            manager, self.args['external_id'])


class ListInstancesTask(Task):
    name = 'list_instances'

    def main(self, manager):
        return self.args['adapter'].listInstances(manager)


class GetQuotaLimitsTask(Task):
    name = 'get_quota_limits'

    def main(self, manager):
        return self.args['adapter'].getQuotaLimits(manager)


class GetQuotaForLabelTask(Task):
    name = 'get_quota_for_label'

    def main(self, manager):
        return self.args['adapter'].getQuotaForLabel(
            manager, self.args['label_config'])


class SimpleTaskManagerLauncher(NodeLauncher):
    """The NodeLauncher implementation for the SimpleTaskManager driver
       framework"""
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler, node, provider_config)
        self.provider_name = provider_config.name
        self.retries = provider_config.launch_retries
        self.pool = provider_config.pools[provider_label.pool.name]
        self.boot_timeout = provider_config.boot_timeout
        self.label = provider_label

    def launch(self):
        self.log.debug("Starting %s instance" % self.node.type)
        attempts = 1
        hostname = 'nodepool-' + self.node.id
        tm = self.handler.manager.task_manager
        adapter = self.handler.manager.adapter
        metadata = {'nodepool_node_id': self.node.id,
                    'nodepool_pool_name': self.pool.name,
                    'nodepool_provider_name': self.provider_name}
        if self.label.cloud_image.key:
            metadata['ssh-keys'] = '{}:{}'.format(
                self.label.cloud_image.username,
                self.label.cloud_image.key)
        while attempts <= self.retries:
            try:
                t = tm.submitTask(CreateInstanceTask(
                    adapter=adapter, hostname=hostname,
                    metadata=metadata,
                    label_config=self.label))
                external_id = t.wait()
                break
            except Exception:
                if attempts <= self.retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self.retries, self.node.id)
                if attempts == self.retries:
                    raise
                attempts += 1
            time.sleep(1)

        self.node.external_id = external_id
        self.zk.storeNode(self.node)

        for count in iterate_timeout(
                self.boot_timeout, exceptions.LaunchStatusException,
                "server %s creation" % external_id):
            instance = self.handler.manager.getInstance(external_id)
            if instance and instance.ready:
                break

        self.log.debug("Created instance %s", repr(instance))

        if self.pool.use_internal_ip:
            server_ip = instance.private_ipv4
        else:
            server_ip = instance.interface_ip

        self.node.connection_port = self.label.cloud_image.connection_port
        self.node.connection_type = self.label.cloud_image.connection_type
        keys = []
        if self.pool.host_key_checking:
            try:
                if (self.node.connection_type == 'ssh' or
                    self.node.connection_type == 'network_cli'):
                    gather_hostkeys = True
                else:
                    gather_hostkeys = False
                keys = nodescan(server_ip, port=self.node.connection_port,
                                timeout=180, gather_hostkeys=gather_hostkeys)
            except Exception:
                raise exceptions.LaunchKeyscanException(
                    "Can't scan instance %s key" % hostname)

        self.log.info("Instance %s ready" % hostname)
        self.node.state = zk.READY
        self.node.external_id = hostname
        self.node.hostname = hostname
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = instance.public_ipv4
        self.node.private_ipv4 = instance.private_ipv4
        self.node.public_ipv6 = instance.public_ipv6
        self.node.region = instance.region
        self.node.az = instance.az
        self.node.host_keys = keys
        self.node.username = self.label.cloud_image.username
        self.node.python_path = self.label.cloud_image.python_path
        self.node.shell_type = self.label.cloud_image.shell_type
        self.zk.storeNode(self.node)
        self.log.info("Instance %s is ready", hostname)


class SimpleTaskManagerHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.simple."
                            "SimpleTaskManagerHandler")
    launcher = SimpleTaskManagerLauncher

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.is_alive():
                count += 1
        return count

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
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It is very important that NodeLauncher always sets one
        # of these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED, zk.ABORTED)
                   for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = self.launcher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)


class SimpleTaskManagerProvider(BaseTaskManagerProvider, QuotaSupport):
    """The Provider implementation for the SimpleTaskManager driver
       framework"""
    def __init__(self, adapter, provider):
        super().__init__(provider)
        self.adapter = adapter
        self.node_cache_time = 0
        self.node_cache = []
        self._zk = None

    def start(self, zk_conn):
        super().start(zk_conn)
        self._zk = zk_conn

    def getRequestHandler(self, poolworker, request):
        return SimpleTaskManagerHandler(poolworker, request)

    def labelReady(self, label):
        return True

    def getProviderLimits(self):
        try:
            t = self.task_manager.submitTask(GetQuotaLimitsTask(
                adapter=self.adapter))
            return t.wait()
        except NotImplementedError:
            return QuotaInformation(
                cores=math.inf,
                instances=math.inf,
                ram=math.inf,
                default=math.inf)

    def quotaNeededByLabel(self, ntype, pool):
        provider_label = pool.labels[ntype]
        try:
            t = self.task_manager.submitTask(GetQuotaForLabelTask(
                adapter=self.adapter, label_config=provider_label))
            return t.wait()
        except NotImplementedError:
            return QuotaInformation()

    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        used_quota = QuotaInformation()

        node_ids = set([n.id for n in self._zk.nodeIterator()])

        for server in self.listNodes():
            meta = server.metadata
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
                qi = server.getQuotaInformation()
            except NotImplementedError:
                qi = QuotaInformation()
            used_quota.add(qi)

        return used_quota

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, external_id):
        instance = self.getInstance(external_id)
        if (not instance) or instance.deleted:
            raise exceptions.NotFound()
        t = self.task_manager.submitTask(DeleteInstanceTask(
            adapter=self.adapter, external_id=external_id))
        t.wait()

    def waitForNodeCleanup(self, external_id, timeout=600):
        for count in iterate_timeout(
                timeout, exceptions.ServerDeleteException,
                "server %s deletion" % external_id):
            instance = self.getInstance(external_id)
            if (not instance) or instance.deleted:
                return

    def cleanupLeakedResources(self):
        deleting_nodes = {}

        for node in self._zk.nodeIterator():
            if node.state == zk.DELETING:
                if node.provider != self.provider.name:
                    continue
                if node.provider not in deleting_nodes:
                    deleting_nodes[node.provider] = []
                deleting_nodes[node.provider].append(node.external_id)

        for server in self.listNodes():
            meta = server.metadata
            if meta.get('nodepool_provider_name') != self.provider.name:
                # Not our responsibility
                continue

            if (server.external_id in
                deleting_nodes.get(self.provider.name, [])):
                # Already deleting this node
                continue

            if not self._zk.getNode(meta['nodepool_node_id']):
                self.log.warning(
                    "Marking for delete leaked instance %s in %s "
                    "(unknown node id %s)",
                    server.external_id, self.provider.name,
                    meta['nodepool_node_id']
                )
                # Create an artifical node to use for deleting the server.
                node = zk.Node()
                node.external_id = server.external_id
                node.provider = self.provider.name
                node.state = zk.DELETING
                self._zk.storeNode(node)

    def listNodes(self):
        now = time.monotonic()
        if now - self.node_cache_time > 5:
            t = self.task_manager.submitTask(ListInstancesTask(
                adapter=self.adapter))
            nodes = t.wait()
            self.node_cache = nodes
            self.node_cache_time = time.monotonic()
        return self.node_cache

    def countNodes(self, provider_name, pool_name):
        return len(
            [n for n in self.listNodes() if
             n.metadata.get('nodepool_provider_name') == provider_name and
             n.metadata.get('nodepool_pool_name') == pool_name])

    def getInstance(self, external_id):
        for candidate in self.listNodes():
            if (candidate.external_id == external_id):
                return candidate
        return None


# Public interface below

class SimpleTaskManagerInstance:
    """Represents a cloud instance

    This class is used by the Simple Task Manager Driver classes to
    represent a standardized version of a remote cloud instance.
    Implement this class in your driver, override the :py:meth:`load`
    method, and supply as many of the fields as possible.

    :param data: An opaque data object to be passed to the load method.
    """

    def __init__(self, data):
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
        self.load(data)

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

    def load(self, data):
        """Parse data and update this object's attributes

        :param data: An opaque data object which was passed to the
            constructor.

        Override this method and extract data from the `data`
        parameter.

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
        raise NotImplementedError()

    def getQuotaInformation(self):
        """Return quota information about this instance.

        :returns: A :py:class:`QuotaInformation` object.
        """
        raise NotImplementedError()


class SimpleTaskManagerAdapter:
    """Public interface for the simple TaskManager Provider

    Implement these methods as simple synchronous calls, and pass this
    class to the SimpleTaskManagerDriver class.

    You can establish a single long-lived connection in the
    initializer.  The provider will call methods on this object from a
    single thread.

    All methods accept a task_manager argument.  Use this to control
    rate limiting:

    .. code:: python

        with task_manager.rateLimit():
            <execute API call>
    """
    def __init__(self, provider):
        pass

    def createInstance(self, task_manager, hostname, metadata, label_config):
        """Create an instance

        :param TaskManager task_manager: An instance of
            :py:class:`~nodepool.driver.taskmananger.TaskManager`.
        :param str hostname: The intended hostname for the instance.
        :param dict metadata: A dictionary of key/value pairs that
            must be stored on the instance.
        :param ProviderLabel label_config: A LabelConfig object describing
            the instance which should be created.
        """
        raise NotImplementedError()

    def deleteInstance(self, task_manager, external_id):
        """Delete an instance

        :param TaskManager task_manager: An instance of
            :py:class:`~nodepool.driver.taskmananger.TaskManager`.
        :param str external_id: The id of the cloud instance.
        """
        raise NotImplementedError()

    def listInstances(self, task_manager):
        """Return a list of instances

        :param TaskManager task_manager: An instance of
            :py:class:`~nodepool.driver.taskmananger.TaskManager`.
        :returns: A list of :py:class:`SimpleTaskManagerInstance` objects.
        """
        raise NotImplementedError()

    def getQuotaLimits(self, task_manager):
        """Return the quota limits for this provider

        The default implementation returns a simple QuotaInformation
        with no limits.  Override this to provide accurate
        information.

        :param TaskManager task_manager: An instance of
            :py:class:`~nodepool.driver.taskmananger.TaskManager`.
        :returns: A :py:class:`QuotaInformation` object.

        """
        return QuotaInformation(default=math.inf)

    def getQuotaForLabel(self, task_manager, label_config):
        """Return information about the quota used for a label

        The default implementation returns a simple QuotaInformation
        for one instance; override this to return more detailed
        information including cores and RAM.

        :param TaskManager task_manager: An instance of
            :py:class:`~nodepool.driver.taskmananger.TaskManager`.
        :param ProviderLabel label_config: A LabelConfig object describing
            a label for an instance.
        :returns: A :py:class:`QuotaInformation` object.

        """
        return QuotaInformation(instances=1)


class SimpleTaskManagerDriver(Driver):
    """Subclass this to make a simple driver"""

    def getProvider(self, provider_config):
        """Return a provider.

        Usually this method does not need to be overridden.
        """
        adapter = self.getAdapter(provider_config)
        return SimpleTaskManagerProvider(adapter, provider_config)

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
