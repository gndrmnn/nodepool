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
import socket
import time

from nodepool import exceptions
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

        self.request.state = zk.PENDING
        self.zk.storeNodeRequest(self.request)

        ready_nodes = self.zk.getReadyNodesOfTypes(self.request.node_types)
        for label in self.request.node_types:
            # First try to grab from the list of already available nodes.
            got_a_node = False
            if self.request.reuse and label in ready_nodes:
                for node in ready_nodes[label]:
                    # Only interested in nodes from this provider and pool
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue
                    try:
                        self.zk.lockNode(node, blocking=False)
                    except exceptions.ZKLockException:
                        # It's already locked so skip it.
                        continue
                    self.log.debug("Locked existing node %s for request %s",
                                   node.id, self.request.id)
                    got_a_node = True
                    node.allocated_to = self.request.id
                    self.zk.storeNode(node)
                    self.nodeset.append(node)
                    break
            if got_a_node:
                continue

            self.log.debug("Starting %s container" % label)

            # TODO: find better tcp port generation...
            for retry in range(10):
                ssh_port = random.randint(22022, 52022)
                if ssh_port not in self.manager.ports:
                    break
            if retry == 9:
                return self.decline("no available tcp port")

            hostid = "%d-%s-%s" % (ssh_port, label, self.request.id)
            rootfs = pool.labels[label]['path']
            username = pool.labels[label]['username']
            homedir = pool.labels[label]['home-dir']
            if not homedir:
                homedir = '/home/%s' % username

            # TODO: better handle container creation errors
            for retry in range(10):
                key = self.manager.createContainer(pool, hostid, rootfs,
                                                   ssh_port, username, homedir)
                if key:
                    break
                time.sleep(1)
            if retry == 9 and not key:
                self.request.state = zk.FAILED
                self.unlockNodeSet(clear_allocation=True)
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
            node.pool = self.pool.name
            self.zk.lockNode(node, blocking=False)
            self.zk.storeNode(node)
            self.nodeset.append(node)
