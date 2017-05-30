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

from nodepool import zk
from nodepool.driver import NodeRequestHandler


class OpenContainerNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.oci.handler."
                            "OpenContainerNodeRequestHandler")

    def run_handler(self):
        self._setFromPoolWorker()
        label = None
        for pool in self.provider.pools.values():
            for node_type in self.request.node_types:
                if node_type in pool.labels:
                    label = pool.labels[node_type]
                    break

        if label:
            self.log.debug("Starting container for %s" % self.request)
            hostid = "%s-%s" % (label.name, self.request.id)
            ssh_port, key = self.manager.createContainer(pool, hostid)

        if label and ssh_port:
            self.log.info("container %s ready" % hostid)
            node = zk.Node()
            node.state = zk.READY
            node.external_id = hostid
            node.hostname = self.provider.hypervisor
            node.interface_ip = socket.gethostbyname(self.provider.hypervisor)
            node.public_ipv4 = node.interface_ip
            node.host_keys = key
            node.ssh_port = ssh_port
            node.provider = self.provider.name
            node.launcher = self.launcher_id
            node.allocated_to = self.request.id
            node.type = label.name
            self.nodeset.append(node)
            self.zk.storeNode(node)
        else:
            self.log.warning("No containers can handle %s" % self.request)
            self.request.declined_by.append(self.launcher_id)
            self.unlockNodeSet(clear_allocation=True)
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
