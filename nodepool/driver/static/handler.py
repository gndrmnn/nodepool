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

from nodepool.driver import NodeRequestHandler

import logging
import random
import socket

from nodepool import zk


class StaticNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeRequestHandler")

    def _checkConcurrency(self, static_node):
        access_count = 0
        for node in self.zk.nodeIterator():
            if node.hostname != static_node["name"]:
                continue
            if node.state in ('ready', 'in-use'):
                access_count += 1
        if access_count >= static_node["max-parallel-jobs"]:
            self.log.info("%s: max concurrency reached (%d)" % (
                static_node["name"], access_count))
            return False
        return True

    def launch(self, node):
        static_node = None
        available_nodes = self.manager.listNodes()
        # Randomize static nodes order
        random.shuffle(available_nodes)
        for available_node in available_nodes:
            if node.type in available_node["labels"]:
                if self._checkConcurrency(available_node):
                    static_node = available_node
                    break

        if static_node:
            self.log.debug("%s: Assigning static_node %s" % (
                self.request.id, static_node))
            node.state = zk.READY
            node.external_id = "static-%s" % self.request.id
            node.hostname = static_node["name"]
            node.username = static_node["username"]
            node.interface_ip = static_node["name"]
            node.public_ipv4 = socket.gethostbyname(static_node["name"])
            node.connection_port = static_node["ssh-port"]
            node.connection_type = "ssh"
            node.host_keys = self.manager.nodes_keys[static_node["name"]]
            self.zk.storeNode(node)

    def poll_launcher(self):
        waiting_node = False
        for node in self.nodeset:
            if node.state == zk.READY:
                continue
            self.launch(node)
            if node.state != zk.READY:
                waiting_node = True
        return not waiting_node
