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

from nodepool.zk import zookeeper as zk

from nodepool.driver.openshift.handler import OpenshiftLauncher
from nodepool.driver.openshift.handler import OpenshiftNodeRequestHandler
from nodepool.nodeutils import nodescan

class OpenshiftVmLauncher(OpenshiftLauncher):
    def _launchLabel(self):
        self.log.debug("Creating resource")
        vm_name = "%s-%s" % (self.label.name, self.node.id)
        project = self.handler.pool.name
        self.handler.manager.createVm(project, vm_name, self.label)
        self.node.external_id = "%s-%s" % (project, vm_name)
        self.zk.storeNode(self.node)
        vm_node_id = self.handler.manager.waitForVm(project, vm_name)
        ssh_port, ssh_endpoint = self.handler.manager.createService(project, vm_name, self.label)
        self.node.state = zk.READY
        self.node.interface_ip = ssh_endpoint
        self.node.hostname = vm_node_id
        self.node.username = self.label.username
        self.node.connection_port = ssh_port
        self.node.host_keys = nodescan(ssh_endpoint, self.node.connection_port, timeout=100)
        self.node.python_path = self.label.python_path
        self.node.shell_type = self.label.shell_type
        self.node.connection_type = 'ssh'
        self.node.cloud = self.provider_config.context
        self.node.host_id = self.node.id
        self.zk.storeNode(self.node)
        self.log.info("Virtualmachine %s is ready" % self.node.external_id)


class OpenshiftVmRequestHandler(OpenshiftNodeRequestHandler):
    log = logging.getLogger("nodepool.driver.openshiftvms."
                            "OpenshiftVmRequestHandler")

    def hasRemainingQuota(self, node_types):
        if len(self.manager.listNodes()) + 1 > self.provider.max_resources:
            return False
        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = OpenshiftVmLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
