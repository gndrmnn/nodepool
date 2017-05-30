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

from nodepool import zk


class StaticNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.static.handler."
                            "StaticNodeRequestHandler")

    def run_handler(self):
        '''
        Main body for the StaticNodeRequestHandler.
        '''
        self._setFromPoolWorker()
        static_node = None
        ntype = None
        for pool in self.provider.pools.values():
            for node in pool.nodes:
                for node_type in self.request.node_types:
                    if node_type in node["labels"]:
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
            node.host_keys = [static_node["host-key"]]
            node.provider = self.provider.name
            node.type = ntype
            self.nodeset.append(node)
            self.zk.storeNode(node)
        else:
            self.log.warning("No static nodes can handle %s" % self.request)
            self.request.declined_by.append(self.launcher_id)
            self.unlockNodeSet(clear_allocation=True)
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
