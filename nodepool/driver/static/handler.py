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
import random

from nodepool import zk
from nodepool.driver import NodeRequestHandler


class StaticNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeRequestHandler")

    def _checkConcurrency(self, static_node):
        access_count = 0

        unavailable_states = [zk.IN_USE]
        if not self.request.reuse:
            # When re-use is disabled (e.g. for Min-Ready request), we need
            # to consider 'ready' node as in-use.
            unavailable_states.append(zk.READY)

        for node in self.zk.nodeIterator():
            if node.hostname != static_node["name"]:
                continue
            if node.state in unavailable_states:
                access_count += 1

        if access_count >= static_node["max-parallel-jobs"]:
            self.log.info("%s: max concurrency reached (%d)" % (
                static_node["name"], access_count))
            return False
        return True

    def launch(self, node):
        # NOTE: We do not expect this to be called since hasRemainingQuota()
        # returning False should prevent the call.
        raise Exception("Node launching not supported by static driver.")

    def hasRemainingQuota(self, ntype):
        # We are always at quota since we cannot launch a new node.
        return False

    def _launch(self, node):
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
            node.external_id = "static-%s" % self.request.id
            self.zk.storeNode(node)

    def pollLauncher(self):
        '''
        We don't wait on a launch since we never actually launch.
        '''
        return True

    def _pollLauncher(self):
        waiting_node = False
        for node in self.nodeset:
            if node.state == zk.READY:
                continue
            self.launch(node)
            if node.state != zk.READY:
                waiting_node = True
        return not waiting_node
