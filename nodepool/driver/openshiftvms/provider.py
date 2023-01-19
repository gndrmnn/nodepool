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
import urllib3
import time

from kubernetes.client.api import CustomObjectsApi

from nodepool.driver.utils_k8s import get_client
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.openshift.provider import OpenshiftProvider
from nodepool.driver.openshiftvms import handler

urllib3.disable_warnings()


class OpenshiftVmsProvider(OpenshiftProvider):
    log = logging.getLogger("nodepool.driver.openshiftvms."
                            "OpenshiftVmsProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        self.token, self.ca_crt, self.k8s_client, self.custom_client = get_client(
            self.log, provider.context, CustomObjectsApi)
        self.vm_names = set()
        for pool in provider.pools.values():
            self.vm_names.update(pool.labels.keys())

    def start(self, zk_conn):
        self.log.debug("Starting")
        self._zk = zk_conn
        if self.ready or not self.custom_client:
            return
        self.ready = True

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, pool, vm, provider, valid_names):
                # self.id = "%s-%s" % (pool, vm['metadata']['name'])
                self.id = vm['metadata']['name']
                self.name = self.id
                self.metadata = {}

                if [True for valid_name in valid_names
                    if vm['metadata']['name'].startswith("%s-" % valid_name)]:
                    node_id = vm['metadata']['name'].split('-')[-1]
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
                for vm in self.custom_client.list_namespaced_custom_object(group='kubevirt.io', plural='virtualmachines', version='v1', namespace=pool)['items']:
                    servers.append(FakeServer(
                        pool, vm, self.provider.name, self.vm_names))
        return servers

    def getProjectVmName(self, server_id):
        for pool in self.provider.pools.keys():
            if server_id.startswith("%s-" % pool):
                vm_name = server_id[len(pool) + 1:]
                return pool, vm_name
        return None, None

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        # Look for pool name
        project_name, vm_name = self.getProjectVmName(server_id)
        if not project_name:
            self.log.exception("%s: unknown pool" % server_id)
            return
        self.log.debug("%s: removing vm" % vm_name)
        try:
            self.custom_client.delete_namespaced_custom_object(group='kubevirt.io', plural='virtualmachines', version='v1', name=vm_name, namespace=project_name)
            self.log.info("%s: vm removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove vm %s" % server_id)
        self.log.debug("%s: removing service" % vm_name)
        try:
            service = self.k8s_client.list_namespaced_service(project_name, label_selector="vm={}".format(vm_name)).items[0]
            service_name = service.metadata.name
            self.k8s_client.delete_namespaced_service(service_name, project_name)
            self.log.info("%s: service removed" % service_name)
        except Exception:
            self.log.exception("Couldn't remove service %s" % service_name)

    def waitForNodeCleanup(self, server_id):
        project_name, vm_name = self.getProjectVmName(server_id)
        for retry in range(300):
            try:
                self.custom_client.get_namespaced_custom_object(group='kubevirt.io', plural='virtualmachines', version='v1', name=vm_name, namespace=project_name)
            except Exception:
                break
            time.sleep(1)

    def getRequestHandler(self, poolworker, request):
        return handler.OpenshiftVmRequestHandler(poolworker, request)
