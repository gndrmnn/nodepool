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

    def checkConcurrency(self, static_node):
        access_count = 0
        for node in self.zk.nodeIterator():
            if node.hostname != static_node["name"]:
                continue
            if node.state in ('ready', 'in-use'):
                access_count += 1
        if access_count >= static_node["max_concurrency"]:
            self.log.debug("%s: max concurrency reached (%d)" % (
                static_node["name"], access_count))
            return False
        return True

    def run_handler(self):
        '''
        Main body for the StaticNodeRequestHandler.
        '''
        self._setFromPoolWorker()
        static_node = None
        max_concurrency = False
        ntype = None
        available_nodes = self.manager.listNodes()
        # Randomize static nodes order
        random.shuffle(available_nodes)
        for node in available_nodes:
            for node_type in self.request.node_types:
                if node_type in node["labels"]:
                    max_concurrency = not self.checkConcurrency(node)
                    if max_concurrency:
                        continue
                    static_node = node
                    ntype = node_type
                    break

        if static_node:
            self.log.debug("Assigning static_node %s to %s" % (static_node,
                                                               self.request))
            node = zk.Node()
            node.state = zk.READY
            node.external_id = "static-%s" % self.request.id
            node.hostname = static_node["name"]
            node.interface_ip = static_node["name"]
            node.public_ipv4 = socket.gethostbyname(static_node["name"])
            node.ssh_port = static_node["ssh_port"]
            node.host_keys = [static_node["host-key"]]
            node.provider = self.provider.name
            node.launcher = self.launcher_id
            node.allocated_to = self.request.id
            node.type = ntype
            self.nodeset.append(node)
            self.zk.storeNode(node)
        else:
            self.log.warning("No static nodes can handle %s" % self.request)
            # When declined because of max_concurrency, don't clear allocation
            if not max_concurrency:
                self.request.declined_by.append(self.launcher_id)
                self.unlockNodeSet(clear_allocation=True)
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
