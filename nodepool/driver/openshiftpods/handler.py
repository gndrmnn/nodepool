# Copyright 2018 Red Hat
# Copyright 2023 Acme Gating, LLC
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

from nodepool.zk import zookeeper as zk

from nodepool.driver.openshift.handler import OpenshiftLauncher
from nodepool.driver.openshift.handler import OpenshiftNodeRequestHandler


class OpenshiftPodLauncher(OpenshiftLauncher):
    def _launchLabel(self):
        self.log.debug("Creating resource")
        pod_name = "%s-%s" % (self.label.name, self.node.id)
        project = self.handler.pool.name
        self.handler.manager.createPod(self.node, self.handler.pool.name,
                                       project, pod_name, self.label,
                                       self.handler.request)
        self.node.external_id = "%s-%s" % (project, pod_name)
        self.node.interface_ip = pod_name
        self.zk.storeNode(self.node)

        pod_node_id = self.handler.manager.waitForPod(project, pod_name)

        self.node.state = zk.READY
        self.node.python_path = self.label.python_path
        self.node.shell_type = self.label.shell_type
        # NOTE: resource access token may be encrypted here
        k8s = self.handler.manager.k8s_client
        self.node.connection_port = {
            'pod': pod_name,
            'namespace': project,
            'host': k8s.api_client.configuration.host,
            'ca_crt': self.handler.manager.ca_crt,
            'skiptls': not k8s.api_client.configuration.verify_ssl,
            'token': self.handler.manager.token,
            'user': 'zuul-worker',
        }
        self.node.connection_type = "kubectl"
        pool = self.handler.provider.pools.get(self.node.pool)
        self.node.resources = self.handler.manager.quotaNeededByLabel(
            self.node.type[0], pool).get_resources()
        self.node.cloud = self.provider_config.context
        self.node.host_id = pod_node_id
        self.zk.storeNode(self.node)
        self.log.info("Pod %s is ready" % self.node.external_id)


class OpenshiftPodRequestHandler(OpenshiftNodeRequestHandler):
    log = logging.getLogger("nodepool.driver.openshiftpods."
                            "OpenshiftPodRequestHandler")

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = OpenshiftPodLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
