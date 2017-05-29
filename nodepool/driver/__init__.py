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
    from nodepool.driver.openstack.provider import OpenStackProviderManager
    from nodepool.driver.static.provider import StaticNodeProviderManager
    from nodepool.driver.oci.provider import OpenContainerProviderManager
    from nodepool.driver.fake.provider import FakeProviderManager


    if provider.name.startswith('fake'):
        return FakeProviderManager(provider, use_taskmanager)
    elif provider.driver == 'openstack':
        return OpenStackProviderManager(provider, use_taskmanager)
    elif provider.driver == 'static':
        return StaticNodeProviderManager(provider)
    elif provider.driver == 'oci':
        return OpenContainerProviderManager(provider)


def get_node_request_handler(provider, pw, request):
    # TODO: uses a better drivers management
    # Dynamically import to avoid circular import issues
    from nodepool.driver.openstack.handler import OpenStackNodeRequestHandler
    from nodepool.driver.static.handler import StaticNodeRequestHandler
    from nodepool.driver.oci.handler import OpenContainerNodeRequestHandler

    if provider.driver == 'openstack':
        return OpenStackNodeRequestHandler(pw, request)
    elif provider.driver == 'static':
        return StaticNodeRequestHandler(pw, request)
    elif provider.driver == 'oci':
        return OpenContainerNodeRequestHandler(pw, request)


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

    def getImage(self, name, **kwarg):
        raise NotImplemented()

    def getAZs(self):
        raise NotImplemented()

    def createServer(self, name, **kwarg):
        raise NotImplemented()

    def cleanupServer(self, server_id):
        raise NotImplemented()

    def deleteServer(self, server_id):
        raise NotImplemented()

    def waitForServerDeletion(self, server_id):
        raise NotImplemented()

    def listServers(self):
        raise NotImplemented()


class NodeRequestHandler(object):
    '''
    Class to process a single node request.

    The PoolWorker thread will instantiate a class of this type for each
    node request that it pulls from ZooKeeper.
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

        if self.launch_manager and not self.launch_manager.poll():
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

        if self.launch_manager and self.launch_manager.failed_nodes:
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
        elif not self.nodeset:
            return False
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
