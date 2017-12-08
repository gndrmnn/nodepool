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

import abc
import collections
import inspect
import importlib
import logging
import os

import six

from nodepool import exceptions
from nodepool import zk


class Drivers:
    """The Drivers plugin interface"""

    log = logging.getLogger("nodepool.driver.Drivers")
    drivers = {}
    drivers_paths = None

    @staticmethod
    def _load_class(driver_name, path, parent_class):
        """Return a driver class that implements the parent_class"""
        spec = importlib.util.spec_from_file_location(driver_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        obj = inspect.getmembers(
            module, lambda x: inspect.isclass(x) and issubclass(x, parent_class) and
            x.__module__ == driver_name)
        error = None
        if len(obj) > 1:
            error = "multiple %s implementation" % parent_class
        if not obj:
            error = "no %s implementation found" % parent_class
        if error:
            Drivers.log.error("%s: %s" % (path, error))
            return False
        return obj[0][1]

    @staticmethod
    def load(drivers_paths = []):
        """Load drivers"""
        if drivers_paths == Drivers.drivers_paths:
            # Already loaded
            return
        Drivers.drivers.clear()
        for drivers_path in drivers_paths + [os.path.dirname(__file__)]:
            drivers = os.listdir(drivers_path)
            for driver in drivers:
                driver_path = os.path.join(drivers_path, driver)
                if driver in Drivers.drivers:
                    Drivers.log.warning("%s: duplicate driver" % driver_path)
                    continue
                if not os.path.isdir(driver_path) or \
                   "__init__.py" not in os.listdir(driver_path):
                    continue
                Drivers.log.debug("%s: loading driver" % driver_path)
                driver_obj = {}
                for name, parent_class in (
                        ("config", ProviderConfig),
                        ("handler", NodeRequestHandler),
                        ("provider", Provider),
                ):
                    driver_obj[name] = Drivers._load_class(
                        driver, os.path.join(driver_path, "%s.py" % name),
                        parent_class)
                    if not driver_obj[name]:
                        break
                if not driver_obj[name]:
                    Drivers.log.error("%s: skipping incorrect driver" %
                                      driver_path)
                    continue
                Drivers.drivers[driver] = driver_obj
        Drivers.drivers_paths = drivers_paths

    @staticmethod
    def get(name):
        if not Drivers.drivers:
            Drivers.load()
        try:
            return Drivers.drivers[name]
        except KeyError:
            raise RuntimeError("%s: unknown driver" % name)


@six.add_metaclass(abc.ABCMeta)
class Provider(object):
    """The Provider interface

    The class or instance attribute **name** must be provided as a string.

    """
    @abc.abstractmethod
    def start(self):
        pass

    @abc.abstractmethod
    def stop(self):
        pass

    @abc.abstractmethod
    def join(self):
        pass

    @abc.abstractmethod
    def labelReady(self, name):
        pass

    @abc.abstractmethod
    def cleanupNode(self, node_id):
        pass

    @abc.abstractmethod
    def waitForNodeCleanup(self, node_id):
        pass

    @abc.abstractmethod
    def cleanupLeakedResources(self):
        pass

    @abc.abstractmethod
    def listNodes(self):
        pass


@six.add_metaclass(abc.ABCMeta)
class NodeRequestHandler(object):
    '''
    Class to process a single nodeset request.

    The PoolWorker thread will instantiate a class of this type for each
    node request that it pulls from ZooKeeper.

    Subclasses are required to implement the launch method.
    '''

    def __init__(self, pw, request):
        '''
        :param PoolWorker pw: The parent PoolWorker object.
        :param NodeRequest request: The request to handle.
        '''
        self.pw = pw
        self.request = request
        self.nodeset = []
        self.done = False
        self.paused = False

        self._failed_nodes = []
        self._ready_nodes = []
        self.threads = []

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
    def _alive_thread_count(self):
        count = 0
        for t in self.threads:
            if t.isAlive():
                count += 1
        return count

    def _imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        ZooKeeper is queried for an image uploaded to the provider that is
        in the READY state.

        :returns: True if it is available, False otherwise.
        '''
        if self.provider.driver.manage_images:
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

        note:: This code is a bit racey in its calculation of the number of
            nodes in use for quota purposes. It is possible for multiple
            launchers to be doing this calculation at the same time. Since we
            currently have no locking mechanism around the "in use"
            calculation, if we are at the edge of the quota, one of the
            launchers could attempt to launch a new node after the other
            launcher has already started doing so. This would cause an
            expected failure from the underlying library, which is ok for now.
        '''
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
                    # Only interested in nodes from this provider and pool
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue
                    # Check this driver reuse requirements
                    if not self.check_reusable_node(node):
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
                        # Notify driver handler about node re-use
                        self.node_reused(node)
                        break

            # Could not grab an existing node, so launch a new one.
            if not got_a_node:
                # If we calculate that we're at capacity, pause until nodes
                # are released by Zuul and removed by the DeletedNodeWorker.
                if self.pool.max_servers is not None and \
                   self._countNodes() >= self.pool.max_servers:
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
                node.launcher = self.launcher_id
                node.allocated_to = self.request.id

                self.set_node_metadata(node)

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
                self.launch(node)

    def _run_handler(self):
        '''
        Main body for the node request handling.
        '''
        self._setFromPoolWorker()
        # We have the launcher_id attr after _setFromPoolWorker() is called.
        self.log = logging.getLogger(
            "nodepool.driver.NodeRequestHandler[%s]" % self.launcher_id)

        max_nodes = self.pool.max_servers
        declined_reasons = []
        invalid_types = self._invalidNodeTypes()
        if invalid_types:
            declined_reasons.append('node type(s) [%s] not available' %
                                    ','.join(invalid_types))
        elif not self._imagesAvailable():
            declined_reasons.append('images are not available')

        if max_nodes is not None and len(self.request.node_types) > max_nodes:
            declined_reasons.append('it would exceed quota')

        # For min-ready requests, which do not re-use READY nodes, let's
        # decline if this provider is already at capacity. Otherwise, we
        # could end up wedged until another request frees up a node.
        if max_nodes is not None and \
           self.request.requestor == "NodePool:min-ready":
            current_count = self.zk.countPoolNodes(self.provider.name,
                                                   self.pool.name)
            # Use >= because dynamic config changes to max-servers can leave
            # us with more than max-servers.
            if current_count >= max_nodes:
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
            self._run_handler()
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

        if not self.poll_launcher():
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

        if self._failed_nodes:
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
            # The assigned nodes must be added to the request in the order
            # in which they were requested.
            assigned = []
            for requested_type in self.request.node_types:
                for node in self.nodeset:
                    if node.id in assigned:
                        continue
                    if node.type == requested_type:
                        # Record node ID in the request
                        self.request.nodes.append(node.id)
                        assigned.append(node.id)

            self.log.debug("Fulfilled node request %s",
                           self.request.id)
            self.request.state = zk.FULFILLED

        self.unlockNodeSet()
        self.zk.storeNodeRequest(self.request)
        self.zk.unlockNodeRequest(self.request)
        return True


    #----------------------------------------------------------------
    # Driver Implementation
    #----------------------------------------------------------------

    def poll_launcher(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self.threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self._alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        for node in self.nodeset:
            if node.state == zk.READY:
                self._ready_nodes.append(node)
            else:
                self._failed_nodes.append(node)

        return True


    def node_reused(self, node):
        '''
        Handler may implement this to be notified when a node is re-used.
        The OpenStack handler uses this to set the choozen_az.
        '''
        pass

    def check_reusable_node(self, node):
        '''
        Handler may implement this to verify a node can be re-used.
        The OpenStack handler uses this to verify the node az is correct.
        '''
        return True

    def set_node_metadata(self, node):
        '''
        Handler may implement this to store metadata before building the node.
        The OpenStack handler uses this to set az, cloud and region.
        '''
        pass

    @abc.abstractmethod
    def launch(self, node):
        '''
        Handler needs to implement this to launch the node.
        '''
        pass


class ConfigValue(object):
    def __eq__(self, other):
        if isinstance(other, ConfigValue):
            if other.__dict__ == self.__dict__:
                return True
        return False

    def __ne__(self, other):
        return not self.__eq__(other)


class ConfigPool(ConfigValue):
    def __init__(self):
        self.max_servers = None
        self.labels = []


class Driver(ConfigValue):
    pass


@six.add_metaclass(abc.ABCMeta)
class ProviderConfig(ConfigValue):
    """The Provider config interface

    The class or instance attribute **name** must be provided as a string.

    """
    def __init__(self, provider):
        self.name = provider['name']
        self.provider = provider
        self.driver = Driver()
        self.driver.name = provider.get('driver', 'openstack')
        self.max_concurrency = provider.get('max-concurrency', -1)
        self.driver.manage_images = False

    def __repr__(self):
        return "<Provider %s>" % self.name

    @abc.abstractmethod
    def __eq__(self, other):
        pass

    @abc.abstractmethod
    def reset():
        pass

    @abc.abstractmethod
    def load(self, newconfig):
        pass

    @abc.abstractmethod
    def get_schema(self):
        pass
