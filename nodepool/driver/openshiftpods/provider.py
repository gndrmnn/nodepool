# Copyright 2018 Red Hat
# Copyright 2023 Acme Gating, LLC
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
import urllib3
import time

from nodepool.driver.utils_k8s import get_client
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.openshift.provider import OpenshiftProvider
from nodepool.driver.openshiftpods import handler

urllib3.disable_warnings()


class OpenshiftPodsProvider(OpenshiftProvider):
    log = logging.getLogger("nodepool.driver.openshiftpods."
                            "OpenshiftPodsProvider")

    def __init__(self, provider, *args):
        super().__init__(provider, _skip_init=True)
        self.token, self.ca_crt, self.k8s_client, _ = get_client(
            self.log, provider.context)
        self.pod_names = set()
        for pool in provider.pools.values():
            self.pod_names.update(pool.labels.keys())

    def start(self, zk_conn):
        self.log.debug("Starting")
        self._zk = zk_conn
        if self.ready or not self.k8s_client:
            return
        self.ready = True

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, pool, pod, provider, valid_names):
                self.id = "%s-%s" % (pool, pod.metadata.name)
                self.name = self.id
                self.metadata = {}

                if [True for valid_name in valid_names
                    if pod.metadata.name.startswith("%s-" % valid_name)]:
                    node_id = pod.metadata.name.split('-')[-1]
                    try:
                        # Make sure last component of name is an id
                        int(node_id)
                        self.metadata['nodepool_provider_name'] = provider
                        self.metadata['nodepool_node_id'] = node_id
                    except Exception:
                        # Probably not a managed project, let's skip metadata
                        pass

            def get(self, name, default=None):
                return getattr(self, name, default)

        if self.ready:
            for pool in self.provider.pools.keys():
                for pod in self.k8s_client.list_namespaced_pod(pool).items:
                    servers.append(FakeServer(
                        pool, pod, self.provider.name, self.pod_names))
        return servers

    def getProjectPodName(self, server_id):
        for pool in self.provider.pools.keys():
            if server_id.startswith("%s-" % pool):
                pod_name = server_id[len(pool) + 1:]
                return pool, pod_name
        return None, None

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        # Look for pool name
        project_name, pod_name = self.getProjectPodName(server_id)
        if not project_name:
            self.log.exception("%s: unknown pool" % server_id)
            return
        self.log.debug("%s: removing pod" % pod_name)
        try:
            self.k8s_client.delete_namespaced_pod(pod_name, project_name)
            self.log.info("%s: pod removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove pod %s" % server_id)

    def waitForNodeCleanup(self, server_id):
        project_name, pod_name = self.getProjectPodName(server_id)
        for retry in range(300):
            try:
                self.k8s_client.read_namespaced_pod(pod_name, project_name)
            except Exception:
                break
            time.sleep(1)

    def getRequestHandler(self, poolworker, request):
        return handler.OpenshiftPodRequestHandler(poolworker, request)
