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
import time
import threading

from nodepool import zk
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class PodLauncher(threading.Thread):
    def __init__(self, handler, node, retries=3, boot_timeout=10):
        super().__init__(name="PodLauncher-%s" % node.id)
        self.log = logging.getLogger("nodepool.PodLauncher-%s" % node.id)
        self.handler = handler
        self.label = handler.pool.labels[node.type]
        self.node = node
        self.retries = retries
        self.boot_timeout = boot_timeout

    def _run(self):
        self.log.debug("Starting %s pod" % self.node.type)
        attempts = 1
        while attempts <= self.retries:

            # TODO: fix using Service NodePort or something...
            port = random.randint(22022, 52022)
            hostid = "%d-%s-%s" % (port,
                                   self.node.type,
                                   self.handler.request.id)
            try:
                pod = self.handler.manager.createContainer(
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

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            status = self.handler.manager.getContainer(self.handler.pool, pod)
            self.log.debug("Pod %s is %s" % (pod, status.get('phase')))
            if status.get('phase') == 'Running':
                break
            time.sleep(0.5)
        if status.get('phase') != 'Running':
            raise RuntimeError("Pod failed to start: %s" % status.get('phase'))

        server_ip = status.get('hostIP')
        if not server_ip:
            raise RuntimeError("Pod doesn't have hostIP")

        try:
            key = keyscan(server_ip, port=port, timeout=15)
        except Exception:
            raise RuntimeError("Can't scan container key")

        self.log.info("container %s ready" % hostid)
        self.node.state = zk.READY
        self.node.external_id = hostid
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = key
        self.node.connection_port = port
        self.node.username = self.label['username']
        self.handler.zk.storeNode(self.node)
        self.log.info("Pod id %s is ready", self.node.id)

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Launch failed for node %s:",
                               self.node.id)
            self.node.state = zk.FAILED
            self.handler.zk.storeNode(self.node)


class KubernetesNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.k8s."
                            "KubernetesNodeRequestHandler")

    def launch(self, node):
        return PodLauncher(self, node)
