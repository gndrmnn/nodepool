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
from nodepool.driver import NodeLaunchManager
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class PodLauncher(threading.Thread):
    log = logging.getLogger("nodepool.driver.k8s.PodLauncher")

    def __init__(self, zk, manager, pool, node, retries=3, boot_timeout=10):
        threading.Thread.__init__(self, name="PodLauncher-%s" % node.id)
        self.log = logging.getLogger("nodepool.PodLauncher-%s" % node.id)
        self._zk = zk
        self._node = node
        self._label = pool.labels[node.type]
        self._retries = retries
        self._boot_timeout = boot_timeout
        self._pool = pool
        self._manager = manager

    def _run(self):
        hostid = self._node.external_id
        username = self._pool.labels[self._node.type]['username']

        attempts = 1
        while attempts <= self._retries:
            self.log.debug("Starting %s container" % self._node.type)

            # TODO: fix using Service NodePort or something...
            ssh_port = random.randint(22022, 52022)
            try:
                pod = self._manager.createContainer(self._pool, hostid,
                                                    ssh_port, self._label)
                break
            except Exception:
                if attempts == self._retries:
                    raise
                attempts += 1

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self._boot_timeout:
            status = self._manager.getContainer(self._pool, pod)
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
            key = keyscan(server_ip, port=ssh_port, timeout=15)
        except:
            raise RuntimeError("Can't scan container key")

        self.log.info("container %s ready" % hostid)
        self._node.state = zk.READY
        self._node.external_id = hostid
        self._node.hostname = server_ip
        self._node.interface_ip = server_ip
        self._node.public_ipv4 = server_ip
        self._node.host_keys = key
        self._node.ssh_port = ssh_port
        self._node.username = username
        self._zk.storeNode(self._node)

    def run(self):
        try:
            self._run()
        except Exception:
            self.log.exception("Launch failed for node %s:",
                               self._node.id)
            self._node.state = zk.FAILED
            self._zk.storeNode(self._node)


class KubernetesNodeLaunchManager(NodeLaunchManager):
    def launch(self, node):
        self._nodes.append(node)
        t = PodLauncher(self._zk, self._manager, self._pool, node)
        t.start()
        self._threads.append(t)


class KubernetesNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.k8s."
                            "KubernetesNodeRequestHandler")

    def set_node_metadata(self, node):
        node.external_id = "%s-%s" % (node.type, self.request.id)

    def launch(self, node):
        if not self.launch_manager:
            self.launch_manager = KubernetesNodeLaunchManager(
                self.zk, self.pool, self.manager,
                self.request.requestor, retries=3)
        self.launch_manager.launch(node)
