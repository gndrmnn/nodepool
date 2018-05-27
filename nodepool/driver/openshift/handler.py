# Copyright 2018 Red Hat
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

from nodepool import zk
from nodepool.driver.utils import NodeLauncher
from nodepool.driver import NodeRequestHandler


class PodLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.handler = handler
        self.zk = handler.zk

    def launch(self):
        self.log.debug("Creating project")
        project = self.handler.manager.createProject(
            self.node.id, self.handler.pool.name)

        self.node.state = zk.READY
        self.node.external_id = project.metadata.name
        # TODO: encrypt resource data using scheduler key
        self.node.connection_port = project.resource
        self.node.connection_type = "resource"
        self.zk.storeNode(self.node)
        self.log.info("Project %s is ready" % project.metadata.name)


class OpenshiftNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.openshift."
                            "OpenshiftNodeRequestHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    def imagesAvailable(self):
        return True

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = PodLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
