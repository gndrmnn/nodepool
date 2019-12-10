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

from nodepool.driver.taskmanager import BaseTaskManagerProvider, Task
from nodepool.driver import Driver, NodeRequestHandler
from nodepool.driver.utils import NodeLauncher
from nodepool.nodeutils import iterate_timeout
from nodepool import exceptions
from nodepool import zk

### Private support classes

class CreateInstanceTask(Task):
    name = 'create_instance'
    def main(self, manager):
        return self.args['adapter'].createInstance(
            manager, self.args['hostname'], self.args['node_id'],
            self.args['label_config'])

class DeleteInstanceTask(Task):
    name = 'delete_instance'
    def main(self, manager):
        return self.args['adapter'].deleteInstance(manager, self.args['node'])

class ListInstancesTask(Task):
    name = 'list_instances'
    def main(self, manager):
        return self.args['adapter'].listInstances(manager)

class GetInstancesTask(Task):
    name = 'get_instance'
    def main(self, manager):
        return self.args['adapter'].getInstance(manager, self.args['external_id'])


class SimpleTaskManagerLauncher(NodeLauncher):
    """The NodeLauncher implementation for the SimpleTaskManager driver
       framework"""
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.provider_name = provider_config.name
        self.retries = provider_config.launch_retries
        self.pool = provider_config.pools[provider_label.pool.name]
        self.handler = handler
        self.zk = handler.zk
        self.boot_timeout = provider_config.boot_timeout
        self.label = provider_label

    def launch(self):
        self.log.debug("Starting %s instance" % self.node.type)
        attempts = 1
        hostname = 'nodepool-'+self.node.id
        tm = self.handler.manager.task_manager
        adapter = self.handler.manager.adapter
        while attempts <= self.retries:
            try:
                t = tm.submitTask(CreateInstanceTask(
                    adapter=adapter, hostname=hostname,
                    node_id=self.node.id,
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

        instance = None
        for count in iterate_timeout(
                self.boot_timeout, exceptions.LaunchStatusException,
                "server %s creation" % external_id):
            for candidate in self.handler.manager.listNodes():
                self.log.debug('check %s', repr(instance))
                if (candidate.external_id == external_id and candidate.ready):
                    instance = candidate
                    self.log.debug("ready")
                    break
            if instance:
                break

        self.log.debug("Created instance %s", repr(instance))

        server_ip = instance.ip_address

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
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = keys
        self.node.username = self.label.cloud_image.username
        self.node.python_path = self.label.cloud_image.python_path
        self.zk.storeNode(self.node)
        self.log.info("Instance %s is ready", hostname)


class SimpleTaskManagerHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.simple."
                            "SimpleTaskManagerHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    def imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        :returns: True if it is available, False otherwise.
        '''
        if self.provider.manage_images:
            for label in self.request.node_types:
                if self.pool.labels[label].cloud_image:
                    if not self.manager.labelReady(self.pool.labels[label]):
                        return False
        return True

    def hasRemainingQuota(self, ntype):
        return True  # TODO

    def hasProviderQuota(self, node_types):
        return True  # TODO

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = SimpleTaskManagerLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)


class SimpleTaskManagerProvider(BaseTaskManagerProvider):
    """The Provider implementation for the SimpleTaskManager driver
       framework"""
    def __init__(self, adapter, provider):
        super().__init__(provider)
        self.adapter = adapter
        self.node_cache_time = 0
        self.node_cache = []

    def getRequestHandler(self, poolworker, request):
        return SimpleTaskManagerHandler(poolworker, request)

    def labelReady(self, label):
        print("LABEL READY")
        return True  # TODO

    def cleanupNode(self, external_id):
        # submit task and wait
        pass

    def waitForNodeCleanup(self, external_id, timeout=600):
        # submit task
        pass

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def listNodes(self):
        now = time.monotonic()
        if now - self.node_cache_time > 5:
            t = self.task_manager.submitTask(ListInstancesTask(
                adapter=self.adapter))
            nodes = t.wait()
            self.node_cache = nodes
            self.node_cache_time = time.monotonic()
        return self.node_cache

### Public interface below
class SimpleTaskManagerInstance:
    def __init__(self, data):
        self.ready = False
        self.deleted = False
        self.external_id = None
        self.ip_address = None
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
            klass=self.__class__,
            external_id=self.external_id,
            state=state)

    def load(self, data):
        raise NotImplementedError()

class SimpleTaskManagerAdapter:
    """Public interface for the simple TaskManager Provider

    Implement these methods as simple synchronous calls, and pass this
    class to the SimpleTaskManagerDriver class.

    """
    def __init__(self, provider):
        pass

    def createInstance(self, task_manager, hostname, node_id, label_config):
        pass

    def deleteInstance(self, task_manager, external_id):
        pass

    def listInstances(self, task_manager):
        """Return a list of SimpleTaskManagerInstance"""
        pass


class SimpleTaskManagerDriver(Driver):
    """Subclass this to make a simple driver"""

    def getProviderConfig(self, provider):
        raise NotImplementedError()

    def getProvider(self, provider_config):
        adapter = self.getAdapter(provider_config)
        return SimpleTaskManagerProvider(adapter, provider_config)

    # Public interface

    def getAdapter(self, provider_config):
        """Instantiate an adapter"""
        raise NotImplementedError()
