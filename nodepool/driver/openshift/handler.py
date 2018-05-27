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
import json

from nodepool import zk
from nodepool.driver import NodeLauncher
from nodepool.driver import NodeRequestHandler


class PodLauncher(NodeLauncher):
    def __init__(self, handler, node):
        super().__init__(handler, node)

    def launch(self):
        self.log.debug("Creating project")
        project = self.handler.manager.createProject(
            self.node.id, self.handler.pool.name)

        self.node.state = zk.READY
        self.node.external_id = project.metadata.name
        # TODO: encrypt resource data using scheduler key
        self.node.connection_port = project.resource
        self.node.connection_type = "resource"
        self.storeNode()
        self.log.info("Project %s is ready" % project.metadata.name)


class OpenshiftNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.k8s."
                            "OpenshiftNodeRequestHandler")

    def launch(self, node):
        return PodLauncher(self, node)
