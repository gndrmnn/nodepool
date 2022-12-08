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

import itertools
import logging
import math
import threading
from concurrent.futures.thread import ThreadPoolExecutor
from collections import Counter, namedtuple

from nodepool import exceptions
from nodepool import nodeutils
from nodepool.zk import zookeeper as zk
from nodepool.driver import Provider
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.driver.static.handler import StaticNodeRequestHandler


class StaticNodeError(Exception):
    pass


NodeTuple = namedtuple("NodeTuple", ["hostname", "username", "port"])


def nodeTuple(node):
    """Return an unique identifier tuple for a static node"""
    if isinstance(node, dict):
        return NodeTuple(node["name"], node["username"],
                         node["connection-port"])
    else:
        return NodeTuple(node.hostname, node.username, node.connection_port)


class StaticNodeProvider(Provider, QuotaSupport):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    # Do not pause the provider when we are at quota so that requests
    # for different labels do not interfere with each other (resources
    # are not fungible in the static proivder).
    PAUSE_AT_QUOTA = False

    def __init__(self, provider, *args):
        self.provider = provider
        # Lock to avoid data races when registering nodes from
        # multiple threads (e.g. cleanup and deleted node worker).
        self._register_lock = threading.Lock()
        self._node_slots = {}  # nodeTuple -> [node]
        # Flag to indicates we need to stop processing state that could
        # interfere with a newer versions of ourselves running.
        self._idle = False

    def _getSlot(self, node):
        return self._node_slots[nodeTuple(node)].index(node)

    def checkHost(self, static_node):
        '''Check node is reachable'''
        # only gather host keys if the connection type is ssh or network_cli
        gather_hostkeys = (
            static_node["connection-type"] == 'ssh' or
            static_node["connection-type"] == 'network_cli')
        if gather_hostkeys and not static_node.get('host-key-checking', True):
            return static_node['host-key']
        try:
            keys = nodeutils.nodescan(static_node["name"],
                                      port=static_node["connection-port"],
                                      timeout=static_node["timeout"],
                                      gather_hostkeys=gather_hostkeys)
        except exceptions.ConnectionTimeoutException:
            raise StaticNodeError(
                "{}: ConnectionTimeoutException".format(
                    nodeTuple(static_node)))

        if not gather_hostkeys:
            return []

        # Check node host-key
        if set(static_node["host-key"]).issubset(set(keys)):
            return keys

        node_tuple = nodeTuple(static_node)
        self.log.debug("%s: Registered key '%s' not in %s",
                       node_tuple, static_node["host-key"], keys)
        raise StaticNodeError(
            "{}: host key mismatches ({})".format(node_tuple, keys))

    def getRegisteredReadyNodes(self, node_tuple):
        '''
        Get all registered nodes with the given identifier that are READY.

        :param Node node_tuple: the namedtuple Node.
        :returns: A list of matching Node objects.
        '''
        nodes = []
        for node in self.zk.nodeIterator():
            if (node.provider != self.provider.name or
                node.state != zk.READY or
                node.allocated_to is not None or
                nodeTuple(node) != node_tuple
            ):
                continue
            nodes.append(node)
        return nodes

    def checkNodeLiveness(self, node):
        node_tuple = nodeTuple(node)
        static_node = self.poolNodes().get(node_tuple)
        if static_node is None:
            return False

        if not static_node.get('host-key-checking', True):
            # When host-key-checking is disabled, assume the node is live
            return True

        try:
            nodeutils.nodescan(static_node["name"],
                               port=static_node["connection-port"],
                               timeout=static_node["timeout"],
                               gather_hostkeys=False)
            return True
        except Exception as exc:
            self.log.warning("Failed to connect to node %s: %s",
                             node_tuple, exc)

        try:
            self.deregisterNode(node)
        except Exception:
            self.log.exception("Couldn't deregister static node:")

        return False

    def _debugSlots(self, node_slots, unslotted_nodes=None):
        for k, nodes in node_slots.items():
            self.log.debug("Slot status for %s:", k)
            for i, node in enumerate(nodes):
                self.log.debug("Slot %i: %s", i, getattr(node, 'id', None))
        if unslotted_nodes is not None:
            self.log.debug("Unslotted nodes: %s", unslotted_nodes)

    def getRegisteredNodes(self):
        '''
        Get node tuples for all registered static nodes.

        :note: We assume hostnames, username and port are unique across pools.

        :returns: A set of registered (hostnames, usernames, ports) tuple for
                  the static driver.
        '''
        unslotted_nodes = []
        node_slots = {}

        # Initialize our slot counters for each node tuple.
        for pool in self.provider.pools.values():
            for static_node in pool.nodes:
                node_slots[nodeTuple(static_node)] = [
                    None for x in range(static_node["max-parallel-jobs"])]

        # Find all nodes with slot ids and store them in node_slots.
        for node in self.zk.nodeIterator():
            if node.provider != self.provider.name:
                continue
            if node.state in {zk.BUILDING, zk.DELETING}:
                continue
            if nodeTuple(node) in node_slots:
                if (node.slot is not None and
                    len(node_slots[nodeTuple(node)]) > node.slot and
                    node_slots[nodeTuple(node)][node.slot] is None):
                    node_slots[nodeTuple(node)][node.slot] = node
                else:
                    # We have more registered nodes of this type than
                    # slots; there may have been a reduction; track in
                    # order to delete.
                    unslotted_nodes.append(node)
            else:
                # We don't know anything about this node; it may have
                # been removed from the config.  We still need to
                # track it in order to decide to delete it.
                node_slots[nodeTuple(node)] = []
                unslotted_nodes.append(node)

        # This can be very chatty, so we don't normally log it.  It
        # can be helpful when debugging tests.
        # self._debugSlots(node_slots, unslotted_nodes)

        # Find all nodes without slot ids, store each in first available slot
        for node in unslotted_nodes:
            if None in node_slots[nodeTuple(node)]:
                # This is a backwards-compat case; we have room for
                # the node, it's just that the node metadata doesn't
                # have a slot number.
                idx = node_slots[nodeTuple(node)].index(None)
                node_slots[nodeTuple(node)][idx] = node
            else:
                # We have more nodes than expected.
                self.log.warning("Tracking excess node %s as %s slot %s",
                                 node, nodeTuple(node),
                                 len(node_slots[nodeTuple(node)]))
                node_slots[nodeTuple(node)].append(node)
        self._node_slots = node_slots

    def registerNodeFromConfig(self, provider_name, pool, static_node,
                               slot):
        '''Register a static node from the config with ZooKeeper.

        A node can be registered multiple times to support
        max-parallel-jobs.  These nodes will share the same node tuple
        but have distinct slot numbers.

        :param str provider_name: Name of the provider.
        :param str pool: Config of the pool owning the node.
        :param dict static_node: The node definition from the config file.
        :param int slot: The slot number for this node.

        '''
        pool_name = pool.name
        host_keys = self.checkHost(static_node)
        node_tuple = nodeTuple(static_node)

        node = zk.Node()
        node.state = zk.READY
        node.provider = provider_name
        node.pool = pool_name
        node.launcher = "static driver"
        node.type = static_node["labels"]
        node.external_id = static_node["name"]
        node.hostname = static_node["name"]
        node.username = static_node["username"]
        node.interface_ip = static_node["name"]
        node.connection_port = static_node["connection-port"]
        node.connection_type = static_node["connection-type"]
        node.python_path = static_node["python-path"]
        node.shell_type = static_node["shell-type"]
        nodeutils.set_node_ip(node)
        node.host_keys = host_keys
        node.attributes = pool.node_attributes
        node.slot = slot
        self.zk.storeNode(node)
        self.log.debug("Registered static node %s", node_tuple)

    def updateNodeFromConfig(self, static_node):
        '''
        Update a static node in ZooKeeper according to config.

        The node is only updated if one of the relevant config items
        changed. Name changes of nodes are handled via the
        register/deregister flow.

        :param dict static_node: The node definition from the config file.
        '''
        host_keys = self.checkHost(static_node)
        node_tuple = nodeTuple(static_node)
        nodes = self.getRegisteredReadyNodes(node_tuple)
        new_attrs = (
            static_node["labels"],
            static_node["username"],
            static_node["connection-port"],
            static_node["shell-type"],
            static_node["connection-type"],
            static_node["python-path"],
            host_keys,
        )

        for node in nodes:
            original_attrs = (node.type, node.username, node.connection_port,
                              node.shell_type, node.connection_type,
                              node.python_path, node.host_keys)

            if original_attrs == new_attrs:
                continue

            try:
                self.zk.lockNode(node, blocking=False)
                node.type = static_node["labels"]
                node.username = static_node["username"]
                node.connection_port = static_node["connection-port"]
                node.connection_type = static_node["connection-type"]
                node.shell_type = static_node["shell-type"]
                node.python_path = static_node["python-path"]
                nodeutils.set_node_ip(node)
                node.host_keys = host_keys
            except exceptions.ZKLockException:
                self.log.warning("Unable to lock node %s for update", node.id)
                continue

            try:
                self.zk.storeNode(node)
                self.log.debug("Updated static node %s (id=%s)",
                               node_tuple, node.id)
            finally:
                self.zk.unlockNode(node)

    def deregisterNode(self, node):
        '''
        Attempt to delete READY nodes.

        We can only delete unlocked READY nodes. If we cannot delete those,
        let them remain until they naturally are deleted (we won't re-register
        them after they are deleted).

        :param Node node: the zk Node object.
        '''
        node_tuple = nodeTuple(node)
        self.log.debug("Deregistering node %s", node)

        try:
            self.zk.lockNode(node, blocking=False)
        except exceptions.ZKLockException:
            # It's already locked so skip it.
            return

        # Double check the state now that we have a lock since it
        # may have changed on us. We keep using the original node
        # since it's holding the lock.
        _node = self.zk.getNode(node.id)
        if _node and _node.state != zk.READY:
            # State changed so skip it.
            self.zk.unlockNode(node)
            return

        node.state = zk.DELETING
        try:
            self.zk.storeNode(node)
            self.log.debug("Deregistered static node: id=%s, "
                           "node_tuple=%s", node.id, node_tuple)
        except Exception:
            self.log.exception("Error deregistering static node:")
        finally:
            self.zk.unlockNode(node)

    def syncNodeCount(self, static_node, pool):
        self.log.debug("Provider %s pool %s syncing nodes with config",
                       self.provider.name, pool.name)
        for slot, node in enumerate(self._node_slots[nodeTuple(static_node)]):
            if node is None:
                # Register nodes to synchronize with our configuration.
                self.registerNodeFromConfig(self.provider.name, pool,
                                            static_node, slot)
            elif slot >= static_node["max-parallel-jobs"]:
                # De-register nodes to synchronize with our configuration.
                # This case covers an existing node, but with a decreased
                # max-parallel-jobs value.
                try:
                    self.deregisterNode(node)
                except Exception:
                    self.log.exception("Couldn't deregister static node:")

    def _start(self, zk_conn):
        self.zk = zk_conn
        self.getRegisteredNodes()

        static_nodes = {}
        with ThreadPoolExecutor() as executor:
            for pool in self.provider.pools.values():
                synced_nodes = []
                for static_node in pool.nodes:
                    synced_nodes.append((static_node, executor.submit(
                        self.syncNodeCount, static_node, pool)))

                for static_node, result in synced_nodes:
                    try:
                        result.result()
                    except StaticNodeError as exc:
                        self.log.warning("Couldn't sync node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't sync node %s:",
                                           nodeTuple(static_node))
                        continue

                    try:
                        self.updateNodeFromConfig(static_node)
                    except StaticNodeError as exc:
                        self.log.warning(
                            "Couldn't update static node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't update static node %s:",
                                           nodeTuple(static_node))
                        continue

                    static_nodes[nodeTuple(static_node)] = static_node

        # De-register nodes to synchronize with our configuration.
        # This case covers any registered nodes that no longer appear in
        # the config.
        for node_tuple, nodes in self._node_slots.items():
            if node_tuple not in static_nodes:
                for node in nodes:
                    try:
                        self.deregisterNode(node)
                    except Exception:
                        self.log.exception("Couldn't deregister static node:")
                        continue

    def start(self, zk_conn):
        try:
            self.log.debug("Starting static provider %s", self.provider.name)
            self._start(zk_conn)
        except Exception:
            self.log.exception("Cannot start static provider:")

    def stop(self):
        self.log.debug("Stopping")

    def idle(self):
        self._idle = True

    def poolNodes(self):
        return {
            nodeTuple(n): n
            for p in self.provider.pools.values()
            for n in p.nodes
        }

    def startNodeCleanup(self, node):
        t = NodeDeleter(self.zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        return True

    def waitForNodeCleanup(self, server_id):
        return True

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        if self._idle:
            return
        with self._register_lock:
            self.getRegisteredNodes()
            for pool in self.provider.pools.values():
                for static_node in pool.nodes:
                    try:
                        self.syncNodeCount(static_node, pool)
                    except StaticNodeError as exc:
                        self.log.warning("Couldn't sync node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't sync node %s:",
                                           nodeTuple(static_node))
                        continue

    def getRequestHandler(self, poolworker, request):
        return StaticNodeRequestHandler(poolworker, request)

    def nodeDeletedNotification(self, node):
        '''
        Re-register the deleted node.
        '''
        if self._idle:
            return

        # It's possible a deleted node no longer exists in our config, so
        # don't bother to reregister.
        node_tuple = nodeTuple(node)
        static_node = self.poolNodes().get(node_tuple)
        if static_node is None:
            return

        with self._register_lock:
            try:
                self.getRegisteredNodes()
            except Exception:
                self.log.exception(
                    "Cannot get registered nodes for re-registration:"
                )
                return

            slot = node.slot
            if slot is None:
                return
            # It's possible we were not able to de-register nodes due to a
            # config change (because they were in use). In that case, don't
            # bother to reregister.
            if slot >= static_node["max-parallel-jobs"]:
                return

            # The periodic cleanup process may have just reregistered
            # this node. When that happens the node in the node slot is
            # different than the one we are processing and we can short
            # circuit.
            try:
                existing_node_slot = self._getSlot(node)
            except Exception:
                # We'll let config synchronization correct any slots changes
                return
            if node != self._node_slots[node_tuple][existing_node_slot]:
                return

            self.log.debug("Re-registering deleted node: %s", node_tuple)
            try:
                pool = self.provider.pools[node.pool]
                self.registerNodeFromConfig(
                    node.provider, pool, static_node, slot)
            except StaticNodeError as exc:
                self.log.warning("Cannot re-register deleted node: %s", exc)
            except Exception:
                self.log.exception("Cannot re-register deleted node %s:",
                                   node_tuple)

    def getProviderLimits(self):
        return QuotaInformation(
            cores=math.inf,
            instances=math.inf,
            ram=math.inf,
            default=math.inf)

    def quotaNeededByLabel(self, ntype, pool):
        return QuotaInformation(cores=0, instances=1, ram=0, default=1)

    def unmanagedQuotaUsed(self):
        return QuotaInformation()

    def getLabelQuota(self):
        label_quota = Counter()
        for pool in self.provider.pools.values():
            for label in pool.labels:
                label_quota[label] = 0
        label_quota.update(
            itertools.chain.from_iterable(
                n.type for n in self.zk.nodeIterator()
                if n.state == zk.READY and n.allocated_to is None))
        return label_quota
