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
from nodepool import nodeutils as utils
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

        if label:
            self.log.debug("Starting container for %s" % self.request)
            hostid = "%s-%s" % (label.name, self.request.id)
            ssh_port = self.provider.start_container(hostid)
            node = zk.Node()
            node.state = zk.READY
            node.external_id = hostid
            node.hostname = self.provider.hypervisor
            node.interface_ip = socket.gethostbyname(self.provider.hypervisor)
            node.public_ipv4 = node.interface_ip
            node.host_keys = utils.keyscan(node.interface_ip, port=ssh_port)
            if not node.host_keys:
                raise RuntimeError("Couldn't get host key")
            node.ssh_port = ssh_port
            node.provider = self.provider.name
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
