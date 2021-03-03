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

from kazoo import exceptions as kze

from nodepool import zk
from nodepool.driver.simple import SimpleTaskManagerHandler
from nodepool.driver.utils import NodeLauncher


class K8SLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler, node, provider_config)
        self.label = provider_label
        self._retries = provider_config.launch_retries

    def _launchLabel(self):
        self.log.debug("Creating resource")
        if self.label.type == "namespace":
            resource = self.handler.manager.createNamespace(
                self.node, self.handler.pool.name)
        else:
            resource = self.handler.manager.createPod(
                self.node, self.handler.pool.name, self.label)

        self.node.state = zk.READY
        self.node.python_path = self.label.python_path
        self.node.shell_type = self.label.shell_type
        # NOTE: resource access token may be encrypted here
        self.node.connection_port = resource
        if self.label.type == "namespace":
            self.node.connection_type = "namespace"
        else:
            self.node.connection_type = "kubectl"
            self.node.interface_ip = resource['pod']
        self.zk.storeNode(self.node)
        self.log.info("Resource %s is ready" % resource['name'])

    def launch(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchLabel()
                break
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our node lock
                # so there's no need to continue.
                raise
            except Exception:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self.node.id)
                # If we created an instance, delete it.
                if self.node.external_id:
                    self.handler.manager.cleanupNode(self.node.external_id)
                    self.handler.manager.waitForNodeCleanup(
                        self.node.external_id)
                    self.node.external_id = None
                    self.node.interface_ip = None
                    self.zk.storeNode(self.node)
                if attempts == self._retries:
                    raise
                attempts += 1


class KubernetesNodeRequestHandler(SimpleTaskManagerHandler):
    log = logging.getLogger("nodepool.driver.kubernetes."
                            "KubernetesNodeRequestHandler")
    launcher = K8SLauncher
