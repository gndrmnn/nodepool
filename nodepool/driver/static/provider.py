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
import socket

import paramiko

from nodepool.driver import Provider
from nodepool import exceptions
from nodepool.nodeutils import keyscan


class StaticNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider):
        self.provider = provider
        self.pools = {}
        self.static_nodes = {}

    def checkHost(self, node):
        # Check node is reachable
        try:
            keys = keyscan(socket.gethostbyname(node["name"]),
                           port=node["ssh_port"],
                           timeout=node["timeout"])
        except exceptions.SSHTimeoutException:
            self.log.error("%s: SSHTimeoutException" % node["name"])
            return False

        # Check node host-key
        if node["host-key"] not in keys:
            self.log.error("%s: host key mismatches" % node["name"])
            self.log.debug("%s: Registered key '%s' not in %s" % (
                node["name"], node["host-key"], keys
            ))
            return False

        # Check node ssh access
        client = paramiko.client.SSHClient()
        try:
            # TODO: force paramiko to use the node['host-key']
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(node["name"], username=node["username"])
            client.exec_command('echo okay')
        except paramiko.AuthenticationException:
            self.log.error("%s: AuthenticationException" % node["name"])
            return False
        finally:
            client.close()
        return True

    def start(self):
        for pool in self.provider.pools.values():
            self.pools[pool.name] = {}
            for node in pool.nodes:
                if not self.checkHost(node):
                    continue
                node_name = "%s-%s" % (pool.name, node["name"])
                self.log.debug("%s: Registering static node" % node_name)
                self.static_nodes[node_name] = node

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
