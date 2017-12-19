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
import time
import threading

from nodepool import zk
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class AwsInstanceLauncher(threading.Thread):
    def __init__(self, handler, node, retries=3, boot_timeout=120):
        super().__init__(name="InstanceLauncher-%s" % node.id)
        self.log = logging.getLogger("nodepool.InstanceLauncher-%s" % node.id)
        self.handler = handler
        self.label = handler.pool.labels[node.type]
        self.node = node
        self.retries = retries
        self.boot_timeout = boot_timeout

    def _run(self):
        self.log.debug("Starting %s instance" % self.node.type)
        attempts = 1
        while attempts <= self.retries:
            try:
                instance = self.handler.manager.createInstance(self.label)
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

        instance_id = instance.id
        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            state = instance.state.get('Name')
            self.log.debug("Instance %s is %s" % (instance_id, state))
            if state == 'running':
                break
            time.sleep(0.5)
            instance.reload()
        if state != 'running':
            raise RuntimeError("Instance failed to start: %s" % state)

        server_ip = instance.public_ip_address
        if not server_ip:
            raise RuntimeError("Instance doesn't have hostIP")

        try:
            key = keyscan(server_ip, port=22, timeout=180)
        except Exception:
            raise RuntimeError("Can't scan instance key")

        self.log.info("Instance %s ready" % instance_id)
        self.node.state = zk.READY
        self.node.external_id = instance_id
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = key
        self.node.connection_port = instance_id
        self.node.username = self.label['username']
        self.handler.zk.storeNode(self.node)
        self.log.info("Instance id %s is ready", self.node.id)

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Launch failed for node %s:",
                               self.node.id)
            self.node.state = zk.FAILED
            self.handler.zk.storeNode(self.node)


class AwsNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.aws."
                            "AwsNodeRequestHandler")

    def launch(self, node):
        return AwsInstanceLauncher(self, node)
