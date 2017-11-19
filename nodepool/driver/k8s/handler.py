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
import random
import time

from nodepool import exceptions
from nodepool import zk
from nodepool.driver.utils import NodeLauncher
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import nodescan


class PodLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.handler = handler
        self.zk = handler.zk
        self.retries = 3
        self.boot_timeout = 300
        self.label = provider_label

    def launch(self):
        self.log.debug("Starting %s pod" % self.node.type[0])
        attempts = 1
        while attempts <= self.retries:
            # TODO: fix using Service NodePort or something...
            port = random.randint(22022, 52022)
            hostid = "%s-%s-%s" % (self.node.id,
                                   self.node.type[0],
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

        if attempts == self.retries:
            raise exceptions.LaunchStatusException(
                "Pod %s failed to create" % hostid)

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            status = self.handler.manager.getContainer(
                self.handler.pool, pod).status
            self.log.debug("Pod %s is %s" % (pod, status.phase))
            if status.phase == 'Running':
                break
            time.sleep(0.5)
        if status.phase != 'Running':
            raise exceptions.LaunchStatusException(
                "Pod %s failed to start: %s" % (hostid, status.phase))

        server_ip = status.host_ip
        if not server_ip:
            raise exceptions.LaunchStatusException(
                "Pod %s doesn't have hostIP" % hostid)

        try:
            key = nodescan(server_ip, port=port, timeout=15)
        except Exception:
            raise exceptions.LaunchKeyscanException(
                "Can't scan container %s key" % hostid)

        self.node.state = zk.READY
        self.node.external_id = hostid
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = key
        self.node.connection_port = port
        self.node.connection_type = "ssh"
        self.node.username = self.label.username
        self.zk.storeNode(self.node)
        self.log.info("Pod %s is ready" % hostid)


class KubernetesNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.k8s."
                            "KubernetesNodeRequestHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    def imagesAvailable(self):
        return True

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = PodLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
