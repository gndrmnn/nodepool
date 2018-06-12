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

import logging

from collections import Counter

from nodepool import exceptions
from nodepool import nodeutils
from nodepool import zk
from nodepool.driver import Provider
from nodepool.driver.static.handler import StaticNodeRequestHandler


class StaticNodeError(Exception):
    pass


class StaticNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.static_nodes = {}

    def checkHost(self, node):
        # Check node is reachable
        if node["connection-type"] != "ssh":
            return
        try:
            keys = nodeutils.nodescan(node["name"],
                                      port=node["connection-port"],
                                      timeout=node["timeout"])
        except exceptions.ConnectionTimeoutException:
            raise StaticNodeError(
                "%s:%s: ConnectionTimeoutException" % (
                    node["name"], node["connection-port"]))

        # Check node host-key
        if set(node["host-key"]).issubset(set(keys)):
            return keys

        self.log.debug("%s: Registered key '%s' not in %s" % (
            node["name"], node["host-key"], keys
        ))
        raise StaticNodeError("%s: host key mismatches (%s)" %
                              (node["name"], keys))

    def getRegisteredNodes(self):
        '''
        Get hostnames for all registered static nodes.

        :note: We assume hostnames are unique across pools.

        :returns: A set of registered hostnames for the static driver.
        '''
        registered = Counter()
        for node in self.zk.nodeIterator():
            if node.provider != self.provider.name:
                continue
            registered.update([node.hostname])
        return registered

    def registerNodeFromConfig(self, provider_name, pool_name, static_node):
        '''
        Register a static node from the config with ZooKeeper.

        A node can be registered multiple times to support max-parallel-jobs.
        These nodes will share a hostname.

        :param str pool_name: Name of the pool owning the node.
        :param dict static_node: The node definition from the config file.
        '''
        host_keys = self.checkHost(static_node)

        current_count = self.registered[static_node["name"]]
        if current_count >= static_node["max-parallel-jobs"]:
            return

        for i in range(current_count, static_node["max-parallel-jobs"]):
            node = zk.Node()
            node.state = zk.READY
            node.provider = provider_name
            node.pool = pool_name
            node.launcher = "static driver"
            node.type = static_node["labels"]
            node.hostname = static_node["name"]
            node.username = static_node["username"]
            node.interface_ip = static_node["name"]
            node.connection_port = static_node["connection-port"]
            node.connection_type = static_node["connection-type"]
            nodeutils.set_node_ip(node)
            node.host_keys = host_keys
            self.zk.storeNode(node)
            self.log.debug("Registered static node %s", node.hostname)

    def _start(self, zk_conn):
        # TODO(Shrews): Deregister nodes when they are removed from the config
        # or when max-parallel-jobs is decreased.

        self.zk = zk_conn
        self.registered = self.getRegisteredNodes()

        for pool in self.provider.pools.values():
            for node in pool.nodes:
                node_name = "%s-%s" % (pool.name, node["name"])

                try:
                    self.registerNodeFromConfig(
                        self.provider.name, pool.name, node)
                except Exception:
                    self.log.exception("Couldn't register static node:")
                    continue

                self.static_nodes[node_name] = node

    def start(self, zk_conn):
        try:
            self._start(zk_conn)
        except Exception:
            self.log.exception("Cannot start static provider:")

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []
        for node in self.static_nodes.values():
            servers.append(node)
        return servers

    def cleanupNode(self, server_id):
        return True

    def waitForNodeCleanup(self, server_id):
        return True

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        pass

    def getRequestHandler(self, poolworker, request):
        return StaticNodeRequestHandler(poolworker, request)

    def nodeDeletedNotification(self, node):
        '''
        Re-register the deleted node.
        '''
        node_name = "%s-%s" % (node.pool, node.hostname)
        try:
            self.registerNodeFromConfig(
                node.provider, node.pool, self.static_nodes[node_name])
        except Exception:
            self.log.exception("Cannot re-register deleted node %s", node)
