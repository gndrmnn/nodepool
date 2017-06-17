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
import threading

from nodepool import zk
from nodepool.driver import NodeRequestHandler


class OciLauncher(threading.Thread):
    def __init__(self, handler, node, retries=9):
        super().__init__(name="OciLauncher-%s" % node.id)
        self.log = logging.getLogger("nodepool.OciLauncher-%s" % node.id)
        self.handler = handler
        self.label = handler.pool.labels[node.type]
        self.node = node
        self.retries = retries

    def _run(self):
        self.log.debug("Starting %s container" % self.node.type)
        attempts = 1
        while attempts <= self.retries:
            port = random.randint(22022, 52022)
            hostid = "%d-%s-%s" % (port,
                                   self.node.type,
                                   self.handler.request.id)
            try:
                key = self.handler.manager.createContainer(
                    self.handler.pool, hostid, port, self.label)
                break
            except Exception:
                if attempts <= self.retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self.retries, self.node.id)
                else:
                    raise
                attempts += 1
            time.sleep(1)

        self.node.state = zk.READY
        self.node.external_id = hostid
        self.node.hostname = self.handler.provider.hypervisor
        self.node.interface_ip = socket.gethostbyname(self.node.hostname)
        self.node.public_ipv4 = self.node.interface_ip
        self.node.host_keys = key
        self.node.username = self.label['username']
        self.node.connection_port = port
        self.handler.zk.storeNode(self.node)
        self.log.info("Container id %s is ready", self.node.id)

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Launch failed for node %s:",
                               self.node.id)
            self.node.state = zk.FAILED
            self.handler.zk.storeNode(self.node)


class OpenContainerNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.oci."
                            "OpenContainerNodeRequestHandler")

    def launch(self, node):
        return OciLauncher(self, node)
