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

from nodepool import exceptions
from nodepool import nodeutils
from nodepool import zk
from nodepool.driver import Provider


class StaticNodeError(Exception):
    pass


class StaticNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.pools = {}
        self.static_nodes = {}
        self.nodes_keys = {}

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
                "%s: ConnectionTimeoutException" % node["name"])

        # Check node host-key
        if set(node["host-key"]).issubset(set(keys)):
            return keys

        self.log.debug("%s: Registered key '%s' not in %s" % (
            node["name"], node["host-key"], keys
        ))
        raise StaticNodeError("%s: host key mismatches (%s)" %
                              (node["name"], keys))

    def start(self):
        for pool in self.provider.pools.values():
            self.pools[pool.name] = {}
            for node in pool.nodes:
                node_name = "%s-%s" % (pool.name, node["name"])
                self.log.debug("%s: Verifying static node" % node_name)
                try:
                    self.nodes_keys[node["name"]] = self.checkHost(node)
                except StaticNodeError as e:
                    self.log.error("Couldn't verify static node: %s" % e)
                    continue
                node_copy = node.copy()
                node_copy['_registered'] = False
                self.static_nodes[node_name] = node_copy

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

    def createNode(self, zk_conn, static_node):
        node = zk.Node()
        node.state = zk.READY
        node.provider = 'static'
        node.hostname = static_node['name']
        node.username = static_node['username']
        node.interface_ip = static_node['name']
        node.connection_port = static_node['connection-port']
        node.connection_type = static_node['connection-type']
        node.host_keys = self.nodes_keys[static_node['name']]
        nodeutils.set_node_ip(node)
        zk_conn.storeNode(node)
        self.log.debug('Registered static node %s', static_node['name'])

    def configReadNotification(self, zk_conn):
        '''
        Register any unregistered static nodes in ZooKeeper, and attempt to
        remove any static nodes that no longer exist in our config.
        '''
        for node in self.static_nodes.values():
            if node['_registered']:
                continue
            try:
                self.createNode(zk_conn, node)
            except Exception:
                self.log.exception("Failed to create static node %s:",
                                   node['name'])
            else:
                node['_registered'] = True

    def nodeDeletedNotification(self, zk_conn, node):
        '''
        Recreate the ZooKeeper znode in a READY state.
        '''
        node_name = "%s-%s" % (node.pool, node.hostname)
        if node_name not in self.static_nodes:
            # This node is no longer in our configuration, so ignore it.
            return
        try:
            self.createNode(zk_conn, self.static_nodes[node_name])
        except Exception:
            self.static_nodes[node_name]['_registered'] = False
            self.log.exception("Failed to recreate static node %s:", node_name)
