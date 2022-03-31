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
from nodepool import zk
from nodepool.driver import Provider
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.driver.static.handler import StaticNodeRequestHandler


class StaticNodeError(Exception):
    pass


Node = namedtuple("Node", ["hostname", "username", "port"])


def nodeTuple(node):
    """Return an unique identifier tuple for a static node"""
    if isinstance(node, dict):
        return Node(node["name"], node["username"], node["connection-port"])
    else:
        return Node(node.hostname, node.username, node.connection_port)


class StaticNodeProvider(Provider, QuotaSupport):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        # Lock to avoid data races when registering nodes from
        # multiple threads (e.g. cleanup and deleted node worker).
        self._register_lock = threading.Lock()

    def checkHost(self, node):
        '''Check node is reachable'''
        # only gather host keys if the connection type is ssh or network_cli
        gather_hostkeys = (
            node["connection-type"] == 'ssh' or
            node["connection-type"] == 'network_cli')
        if gather_hostkeys and not node.get('host-key-checking', True):
            return node['host-key']
        try:
            keys = nodeutils.nodescan(node["name"],
                                      port=node["connection-port"],
                                      timeout=node["timeout"],
                                      gather_hostkeys=gather_hostkeys)
        except exceptions.ConnectionTimeoutException:
            raise StaticNodeError(
                "{}: ConnectionTimeoutException".format(nodeTuple(node)))

        if not gather_hostkeys:
            return []

        # Check node host-key
        if set(node["host-key"]).issubset(set(keys)):
            return keys

        node_tuple = nodeTuple(node)
        self.log.debug("%s: Registered key '%s' not in %s",
                       node_tuple, node["host-key"], keys)
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
            self.deregisterNode(count=1, node_tuple=node_tuple)
        except Exception:
            self.log.exception("Couldn't deregister static node:")

        return False

    def getRegisteredNodes(self):
        '''
        Get node tuples for all registered static nodes.

        :note: We assume hostnames, username and port are unique across pools.

        :returns: A set of registered (hostnames, usernames, ports) tuple for
                  the static driver.
        '''
        registered = Counter()
        for node in self.zk.nodeIterator():
            if node.provider != self.provider.name:
                continue
            registered.update([nodeTuple(node)])
        return registered

    def registerNodeFromConfig(self, count, provider_name, pool,
                               static_node):
        '''
        Register a static node from the config with ZooKeeper.

        A node can be registered multiple times to support max-parallel-jobs.
        These nodes will share the same node tuple.

        :param int count: Number of times to register this node.
        :param str provider_name: Name of the provider.
        :param str pool: Config of the pool owning the node.
        :param dict static_node: The node definition from the config file.
        '''
        pool_name = pool.name
        host_keys = self.checkHost(static_node)
        node_tuple = nodeTuple(static_node)

        for i in range(0, count):
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
            static_node["connection-type"],
            static_node["shell-type"],
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

    def deregisterNode(self, count, node_tuple):
        '''
        Attempt to delete READY nodes.

        We can only delete unlocked READY nodes. If we cannot delete those,
        let them remain until they naturally are deleted (we won't re-register
        them after they are deleted).

        :param Node node_tuple: the namedtuple Node.
        '''
        self.log.debug("Deregistering %s node(s) matching %s",
                       count, node_tuple)

        nodes = self.getRegisteredReadyNodes(node_tuple)

        for node in nodes:
            if count <= 0:
                break

            try:
                self.zk.lockNode(node, blocking=False)
            except exceptions.ZKLockException:
                # It's already locked so skip it.
                continue

            # Double check the state now that we have a lock since it
            # may have changed on us. We keep using the original node
            # since it's holding the lock.
            _node = self.zk.getNode(node.id)
            if _node.state != zk.READY:
                # State changed so skip it.
                self.zk.unlockNode(node)
                continue

            node.state = zk.DELETING
            try:
                self.zk.storeNode(node)
                self.log.debug("Deregistered static node: id=%s, "
                               "node_tuple=%s", node.id, node_tuple)
                count = count - 1
            except Exception:
                self.log.exception("Error deregistering static node:")
            finally:
                self.zk.unlockNode(node)

    def syncNodeCount(self, registered, node, pool):
        current_count = registered[nodeTuple(node)]

        # Register nodes to synchronize with our configuration.
        if current_count < node["max-parallel-jobs"]:
            register_cnt = node["max-parallel-jobs"] - current_count
            self.registerNodeFromConfig(
                register_cnt, self.provider.name, pool, node)

        # De-register nodes to synchronize with our configuration.
        # This case covers an existing node, but with a decreased
        # max-parallel-jobs value.
        elif current_count > node["max-parallel-jobs"]:
            deregister_cnt = current_count - node["max-parallel-jobs"]
            try:
                self.deregisterNode(deregister_cnt, nodeTuple(node))
            except Exception:
                self.log.exception("Couldn't deregister static node:")

    def _start(self, zk_conn):
        self.zk = zk_conn
        registered = self.getRegisteredNodes()

        static_nodes = {}
        with ThreadPoolExecutor() as executor:
            for pool in self.provider.pools.values():
                synced_nodes = []
                for node in pool.nodes:
                    synced_nodes.append((node, executor.submit(
                        self.syncNodeCount, registered, node, pool)))

                for node, result in synced_nodes:
                    try:
                        result.result()
                    except StaticNodeError as exc:
                        self.log.warning("Couldn't sync node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't sync node %s:",
                                           nodeTuple(node))
                        continue

                    try:
                        self.updateNodeFromConfig(node)
                    except StaticNodeError as exc:
                        self.log.warning(
                            "Couldn't update static node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't update static node %s:",
                                           nodeTuple(node))
                        continue

                    static_nodes[nodeTuple(node)] = node

        # De-register nodes to synchronize with our configuration.
        # This case covers any registered nodes that no longer appear in
        # the config.
        for node in list(registered):
            if node not in static_nodes:
                try:
                    self.deregisterNode(registered[node], node)
                except Exception:
                    self.log.exception("Couldn't deregister static node:")
                    continue

    def start(self, zk_conn):
        try:
            self._start(zk_conn)
        except Exception:
            self.log.exception("Cannot start static provider:")

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        registered = self.getRegisteredNodes()
        servers = []
        for pool in self.provider.pools.values():
            for node in pool.nodes:
                if nodeTuple(node) in registered:
                    servers.append(node)
        return servers

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
        with self._register_lock:
            registered = self.getRegisteredNodes()
            for pool in self.provider.pools.values():
                for node in pool.nodes:
                    try:
                        self.syncNodeCount(registered, node, pool)
                    except StaticNodeError as exc:
                        self.log.warning("Couldn't sync node: %s", exc)
                        continue
                    except Exception:
                        self.log.exception("Couldn't sync node:")
                        continue

    def getRequestHandler(self, poolworker, request):
        return StaticNodeRequestHandler(poolworker, request)

    def nodeDeletedNotification(self, node):
        '''
        Re-register the deleted node.
        '''
        # It's possible a deleted node no longer exists in our config, so
        # don't bother to reregister.
        node_tuple = nodeTuple(node)
        static_node = self.poolNodes().get(node_tuple)
        if static_node is None:
            return

        with self._register_lock:
            try:
                registered = self.getRegisteredNodes()
            except Exception:
                self.log.exception(
                    "Cannot get registered nodes for re-registration:"
                )
                return
            current_count = registered[node_tuple]

            # It's possible we were not able to de-register nodes due to a
            # config change (because they were in use). In that case, don't
            # bother to reregister.
            if current_count >= static_node["max-parallel-jobs"]:
                return

            try:
                pool = self.provider.pools[node.pool]
                self.registerNodeFromConfig(
                    1, node.provider, pool, static_node)
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
