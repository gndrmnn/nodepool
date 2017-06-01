# Copyright (C) 2011-2014 OpenStack Foundation
# Copyright (C) 2017 Red Hat
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

from nodepool import zk


def get_provider_manager(provider, use_taskmanager):
    # TODO: uses a better drivers management
    # Dynamically import to avoid circular import issues
    from nodepool.provider_manager import OpenStackProviderManager
    from nodepool.provider_manager import FakeProviderManager


    if provider.name.startswith('fake'):
        return FakeProviderManager(provider, use_taskmanager)
    else:
        return OpenStackProviderManager(provider, use_taskmanager)


def get_node_request_handler(pw, request):
    # TODO: uses a better drivers management
    # Dynamically import to avoid circular import issues
    from nodepool.launcher import OpenStackNodeRequestHandler

    return OpenStackNodeRequestHandler(pw, request)


class ProviderManager(object):
    """The Provider Manager interface

    The class or instance attribute **name** must be provided as a string.

    """
    log = logging.getLogger("nodepool.ProviderManager")

    @staticmethod
    def reconfigure(old_config, new_config, use_taskmanager=True):
        stop_managers = []
        for p in new_config.providers.values():
            oldmanager = None
            if old_config:
                oldmanager = old_config.provider_managers.get(p.name)
            if oldmanager and p != oldmanager.provider:
                stop_managers.append(oldmanager)
                oldmanager = None
            if oldmanager:
                new_config.provider_managers[p.name] = oldmanager
            else:
                ProviderManager.log.debug("Creating new ProviderManager object"
                                          " for %s" % p.name)
                new_config.provider_managers[p.name] = \
                    get_provider_manager(p, use_taskmanager)
                new_config.provider_managers[p.name].start()

        for stop_manager in stop_managers:
            stop_manager.stop()

    @staticmethod
    def stopProviders(config):
        for m in config.provider_managers.values():
            m.stop()
            m.join()

    def start(self):
        raise NotImplemented()

    def stop(self):
        raise NotImplemented()

    def join(self):
        raise NotImplemented()

    def labelReady(self, name):
        raise NotImplemented()

    def cleanupNode(self, node_id):
        raise NotImplemented()

    def waitForNodeCleanup(self, node_id):
        raise NotImplemented()

    def listNodes(self):
        raise NotImplemented()


class NodeRequestHandler(object):
    '''
    Class to process a single node request.

    The PoolWorker thread will instantiate a class of this type for each
    node request that it pulls from ZooKeeper.

    Subclasses are required to implement the run_handler method and the
    NodeLaunchManager to kick off any threads needed to satisfy the request.
    '''

    def __init__(self, pw, request):
        '''
        :param PoolWorker pw: The parent PoolWorker object.
        :param NodeRequest request: The request to handle.
        '''
        self.pw = pw
        self.request = request
        self.launch_manager = None
        self.nodeset = []
        self.done = False
        self.paused = False

    def _setFromPoolWorker(self):
        '''
        Set values that we pull from the parent PoolWorker.

        We don't do this in __init__ because this class is re-entrant and we
        want the updated values.
        '''
        self.provider = self.pw.getProviderConfig()
        self.pool = self.pw.getPoolConfig()
        self.zk = self.pw.getZK()
        self.manager = self.pw.getProviderManager()
        self.launcher_id = self.pw.launcher_id

    @property
    def alive_thread_count(self):
        if not self.launch_manager:
            return 0
        return self.launch_manager.alive_thread_count

    #----------------------------------------------------------------
    # Public methods
    #----------------------------------------------------------------

    def unlockNodeSet(self, clear_allocation=False):
        '''
        Attempt unlocking all Nodes in the node set.

        :param bool clear_allocation: If true, clears the node allocated_to
            attribute.
        '''
        for node in self.nodeset:
            if not node.lock:
                continue

            if clear_allocation:
                node.allocated_to = None
                self.zk.storeNode(node)

            try:
                self.zk.unlockNode(node)
            except Exception:
                self.log.exception("Error unlocking node:")
            self.log.debug("Unlocked node %s for request %s",
                           node.id, self.request.id)

        self.nodeset = []

    def run(self):
        '''
        Execute node request handling.

        This code is designed to be re-entrant. Because we can't always
        satisfy a request immediately (due to lack of provider resources), we
        need to be able to call run() repeatedly until the request can be
        fulfilled. The node set is saved and added to between calls.
        '''
        try:
            self.run_handler()
        except Exception:
            self.log.exception("Exception in NodeRequestHandler:")
            self.unlockNodeSet(clear_allocation=True)
            self.request.state = zk.FAILED
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True

    def poll(self):
        '''
        Check if the request has been handled.

        Once the request has been handled, the 'nodeset' attribute will be
        filled with the list of nodes assigned to the request, or it will be
        empty if the request could not be fulfilled.

        :returns: True if we are done with the request, False otherwise.
        '''
        if self.paused:
            return False

        if self.done:
            return True

        if not self.launch_manager.poll():
            return False

        # If the request has been pulled, unallocate the node set so other
        # requests can use them.
        if not self.zk.getNodeRequest(self.request.id):
            self.log.info("Node request %s disappeared", self.request.id)
            for node in self.nodeset:
                node.allocated_to = None
                self.zk.storeNode(node)
            self.unlockNodeSet()
            self.zk.unlockNodeRequest(self.request)
            return True

        if self.launch_manager.failed_nodes:
            self.log.debug("Declining node request %s because nodes failed",
                           self.request.id)
            self.request.declined_by.append(self.launcher_id)
            launchers = set(self.zk.getRegisteredLaunchers())
            if launchers.issubset(set(self.request.declined_by)):
                # All launchers have declined it
                self.log.debug("Failing declined node request %s",
                               self.request.id)
                self.request.state = zk.FAILED
            else:
                self.request.state = zk.REQUESTED
        else:
            for node in self.nodeset:
                # Record node ID in the request
                self.request.nodes.append(node.id)
            self.log.debug("Fulfilled node request %s",
                           self.request.id)
            self.request.state = zk.FULFILLED

        self.unlockNodeSet()
        self.zk.storeNodeRequest(self.request)
        self.zk.unlockNodeRequest(self.request)
        return True

    def run_handler(self):
        raise NotImplemented()


class NodeLaunchManager(object):
    '''
    Handle launching multiple nodes in parallel.

    Subclasses are required to implement the launch method.
    '''
    def __init__(self, zk, pool, provider_manager,
                 requestor, retries):
        '''
        Initialize the launch manager.

        :param ZooKeeper zk: A ZooKeeper object.
        :param ProviderPool pool: A config ProviderPool object.
        :param ProviderManager provider_manager: The manager object used to
            interact with the selected provider.
        :param str requestor: Identifier for the request originator.
        :param int retries: Number of times to retry failed launches.
        '''
        self._retries = retries
        self._nodes = []
        self._failed_nodes = []
        self._ready_nodes = []
        self._threads = []
        self._zk = zk
        self._pool = pool
        self._manager = provider_manager
        self._requestor = requestor

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    @property
    def failed_nodes(self):
        return self._failed_nodes

    @property
    def ready_nodes(self):
        return self._ready_nodes

    def poll(self):
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

        node_states = [node.state for node in self._nodes]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        for node in self._nodes:
            if node.state == zk.READY:
                self._ready_nodes.append(node)
            else:
                self._failed_nodes.append(node)

        return True

    def launch(self, node):
        raise NotImplemented()
