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

from nodepool import zk
from nodepool.driver import NodeRequestHandler


class OpenContainerNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.oci."
                            "OpenContainerNodeRequestHandler")
    def check_capacity(self, label):
        # TODO: find better tcp port generation...
        for retry in range(10):
            ssh_port = random.randint(22022, 52022)
            if ssh_port not in self.manager.ports:
                break
        if retry < 9:
            self.ssh_port = ssh_port
            return True
        self.log.error("No TCP port available")

    def set_node_metadata(self, node):
        node.ssh_port = self.ssh_port

    def launch(self, node):
        self.log.debug("Starting %s container" % node.type)
        hostid = "%d-%s-%s" % (node.ssh_port, node.type, self.request.id)
        rootfs = self.pool.labels[node.type]['path']
        username = self.pool.labels[node.type]['username']
        homedir = self.pool.labels[node.type]['home-dir']
        if not homedir:
            homedir = '/home/%s' % username

        # TODO: better handle container creation errors
        for retry in range(10):
            key = self.manager.createContainer(self.pool, hostid, rootfs,
                                               node.ssh_port, username, homedir)
            if key:
                break
            time.sleep(1)
        if retry == 9 and not key:
            node.state = zk.FAILED
            return
        self.log.info("container %s ready" % hostid)
        node.state = zk.READY
        node.external_id = hostid
        node.hostname = self.provider.hypervisor
        node.interface_ip = socket.gethostbyname(self.provider.hypervisor)
        node.public_ipv4 = node.interface_ip
        node.host_keys = key
        node.username = username
