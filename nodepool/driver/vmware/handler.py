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
import time

from nodepool import exceptions
from nodepool import zk
from nodepool.driver import NodeLauncher
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class VmwareInstanceLauncher(NodeLauncher):
    def __init__(self, handler, node, retries=3, boot_timeout=120):
        super().__init__(handler, node)
        self.retries = retries
        self.boot_timeout = boot_timeout

    def launch(self):
        self.log.debug("Starting %s instance" % self.node.type)
        attempts = 1
        hostname = "%s-%s" % (self.label, self.node.id)
        while attempts <= self.retries:
            try:
                instance = self.handler.manager.createInstance(
                    hostname, self.label, self.node.id)
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

        self.node.external_id = instance.summary.guest.guestId
        self.storeNode()

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            state = instance.guest.guestState
            self.log.debug("Instance %s is %s" % (instance.id, state))
            if state == 'running':
                break
            time.sleep(0.5)
        if state != 'running':
            raise exceptions.LaunchStatusException(
                "Instance %s failed to start: %s" % (instance.id, state))

        server_ip = instance.summary.guest.ipAddress
        if not server_ip:
            raise exceptions.LaunchStatusException(
                "Instance %s doesn't have a public ip" % instance.id)

        try:
            key = keyscan(server_ip, port=22, timeout=180)
        except Exception:
            raise exceptions.LaunchKeyscanException(
                "Can't scan instance %s key" % instance.id)

        self.log.info("Instance %s ready" % instance.id)
        self.node.state = zk.READY
        self.node.external_id = instance.id
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = key
        self.node.connection_port = 22
        self.node.connection_type = "ssh"
        self.node.username = self.label.username
        self.storeNode()
        self.log.info("Instance %s is ready", instance.id)


class VmwareNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.vmware."
                            "VmwareNodeRequestHandler")

    def launch(self, node):
        return VmwareInstanceLauncher(self, node)
