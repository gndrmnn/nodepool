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

from nodepool import exceptions
from nodepool import zk
from nodepool.driver import NodeLauncher
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class AwsInstanceLauncher(NodeLauncher):
    def __init__(self, handler, node, retries=3, boot_timeout=120):
        super().__init__(handler, node)
        self.retries = retries
        self.boot_timeout = boot_timeout

    def launch(self):
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

        instance.create_tags(Tags=[{'Key': 'nodepool_id',
                                    'Value': str(self.node.id)}])
        instance_id = instance.id
        self.node.external_id = instance_id
        self.storeNode()

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            state = instance.state.get('Name')
            self.log.debug("Instance %s is %s" % (instance_id, state))
            if state == 'running':
                break
            time.sleep(0.5)
            instance.reload()
        if state != 'running':
            raise exceptions.LaunchStatusException(
                "Instance %s failed to start: %s" % (instance_id, state))

        server_ip = instance.public_ip_address
        if not server_ip:
            raise exceptions.LaunchStatusException(
                "Instance %s doesn't have a public ip" % instance_id)

        try:
            key = keyscan(server_ip, port=22, timeout=180)
        except Exception:
            raise exceptions.LaunchKeyscanException(
                "Can't scan instance %s key" % instance_id)

        self.log.info("Instance %s ready" % instance_id)
        self.node.state = zk.READY
        self.node.external_id = instance_id
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = key
        self.node.connection_port = 22
        self.node.username = self.label.username
        self.storeNode()
        self.log.info("Instance %s is ready", instance_id)


class AwsNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.aws."
                            "AwsNodeRequestHandler")

    def launch(self, node):
        return AwsInstanceLauncher(self, node)
