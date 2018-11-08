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

from kazoo import exceptions as kze

from nodepool import zk
from nodepool.driver.utils import NodeLauncher
from nodepool.driver import NodeRequestHandler


class OpenShiftPodLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.handler = handler
        self.zk = handler.zk
        self.label = provider_label
        self._retries = provider_config.launch_retries

    def _launchLabel(self):
        self.log.debug("Creating resource")
        pod_name = "%s-%s" % (self.label.name, self.node.id)
        project = self.handler.pool.name
        self.node.external_id = self.handler.manager.createPod(
            project, pod_name, self.label)
        self.node.interface_ip = pod_name
        self.zk.storeNode(self.node)

        resource = self.handler.manager.waitForPod(
            project, pod_name)

        self.node.state = zk.READY
        # NOTE: resource access token may be encrypted here
        self.node.connection_port = resource
        self.node.connection_type = "kubectl"
        self.zk.storeNode(self.node)
        self.log.info("Pod %s is ready" % self.node.external_id)

    def launch(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchLabel()
                break
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our node lock
                # so there's no need to continue.
                raise
            except Exception:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self.node.id)
                # If we created an instance, delete it.
                if self.node.external_id:
                    self.handler.manager.cleanupNode(self.node.external_id)
                    self.handler.manager.waitForNodeCleanup(
                        self.node.external_id)
                    self.node.external_id = None
                    self.node.interface_ip = None
                    self.zk.storeNode(self.node)
                if attempts == self._retries:
                    raise
                attempts += 1


class OpenshiftPodRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.openshiftpods."
                            "OpenshiftPodRequestHandler")

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

    def hasRemainingQuota(self, node_types):
        if len(self.manager.listNodes()) + 1 > self.provider.max_pods:
            return False
        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = OpenShiftPodLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
