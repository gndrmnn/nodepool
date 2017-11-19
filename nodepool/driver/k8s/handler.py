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

from nodepool import exceptions
from nodepool import zk
from nodepool.driver import NodeLauncher
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import keyscan


class PodLauncher(NodeLauncher):
    def __init__(self, handler, node, retries=3, boot_timeout=10):
        super().__init__(handler, node)
        self.retries = retries
        self.boot_timeout = boot_timeout

    def launch(self):
        self.log.debug("Starting %s pod" % self.node.type)
        attempts = 1
        while attempts <= self.retries:
            # TODO: fix using Service NodePort or something...
            port = random.randint(22022, 52022)
            hostid = "%s-%s-%s" % (self.node.id,
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
            raise exceptions.LaunchStatusException(
                "Pod %s failed to start: %s" % (hostid, status.get('phase')))

        server_ip = status.get('hostIP')
        if not server_ip:
            raise exceptions.LaunchStatusException(
                "Pod %s doesn't have hostIP" % hostid)

        try:
            key = keyscan(server_ip, port=port, timeout=15)
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
        self.node.username = self.label.username
        self.storeNode()
        self.log.info("Pod %s is ready" % hostid)


class KubernetesNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.k8s."
                            "KubernetesNodeRequestHandler")

    def hasRemainingQuota(self, ntype):
        if self.pool.max_servers is None or \
           len(self.manager.pods) + 1 <= self.pool.max_servers:
            return True
        return False

    def hasProviderQuota(self, ntypes):
        if self.pool.max_servers is None or \
           len(self.manager.pods) + len(ntypes) <= self.pool.max_servers:
            return True
        return False

    def launch(self, node):
        return PodLauncher(self, node)
