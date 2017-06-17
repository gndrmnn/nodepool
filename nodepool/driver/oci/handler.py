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
    log = logging.getLogger("nodepool.driver.oci."
                            "OpenContainerNodeRequestHandler")

    def decline(self, reason):
        self.log.warning("%s: Declined because %s" % (self.request.id, reason))
        self.request.declined_by.append(self.launcher_id)
        self.unlockNodeSet(clear_allocation=True)
        self.zk.storeNodeRequest(self.request)
        self.zk.unlockNodeRequest(self.request)
        self.done = True

    def run_handler(self):
        self._setFromPoolWorker()

        # Check for available labels
        for pool in self.provider.pools.values():
            missing_labels = set()
            for node_type in self.request.node_types:
                if node_type not in pool.labels:
                    missing_labels.add(node_type)
                    break
            if not missing_labels:
                break
        if missing_labels:
            return self.decline("missing label %s" % " ".join(missing_labels))

        # Check pool max-servers
        if (pool.max_servers and
            len(self.request.node_types) >= pool.max_servers):
            return self.decline("request will exceed max containers count")

        count = 0
        for label in self.request.node_types:
            # TODO: make this re-entrant and re-use ready node
            count += 1
            self.log.debug("Starting %s container" % label)
            hostid = "%s-%s" % (label, self.request.id)
            rootfs = pool.labels[label]['path']
            username = pool.labels[label]['username']
            workdir = pool.labels[label]['work-dir']
            if not workdir:
                workdir = '/home/%s/src' % username
            if count > 1:
                hostid = "%s-%d" % (hostid, count)
            ssh_port, key = self.manager.createContainer(pool, hostid, rootfs,
                                                         username, workdir)

            if not ssh_port:
                return self.decline("container creation failed")
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
            node.username = username
            node.type = label
            self.nodeset.append(node)
            self.zk.storeNode(node)
            self.zk.lockNode(node, blocking=False)
