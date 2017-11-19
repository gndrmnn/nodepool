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

from kubernetes import client, config

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.k8s.handler import KubernetesNodeRequestHandler


class KubernetesProvider(Provider):
    log = logging.getLogger("nodepool.driver.k8s.KubernetesProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.zuul_public_key = provider.zuul_public_key
        self.ready = False
        self.pods = {}
        config.load_kube_config(config_file=provider.config_file)
        self.client = client.CoreV1Api()

    # Kubernetes client interface
    def configmap(self, namespace, action, name=None):
        if action == 'create':
            self.client.create_namespaced_config_map(
                namespace,
                self.render_configmap(namespace, name, self.zuul_public_key))
        elif action == 'get':
            return self.client.list_namespaced_config_map(namespace)
        elif action == 'delete':
            self.client.delete_namespaced_config_map(name, namespace)

    def pod(self, namespace, action, name=None, port=None, label=None):
        if action == 'create':
            try:
                self.pods.setdefault(namespace, set()).add(name)
                self.client.create_namespaced_pod(namespace, self.render_pod(
                    namespace, name, port, label))
            except Exception:
                self.log.exception("Create namespaced pod failed")
                raise exceptions.LaunchStatusException(
                    "Couldn't create pod %s in %s" % (name, namespace))
            return name
        elif action == 'get':
            if name:
                return self.client.read_namespaced_pod_with_http_info(
                    name, namespace)[0]
            return self.client.list_namespaced_pod(namespace).items
        elif action == 'delete':
            try:
                self.client.delete_namespaced_pod(name, namespace, {})
            except Exception:
                self.log.exception("Delete namespaced pod failed")
                raise exceptions.ServerDeleteException(
                    "server %s deletion failed" % name)

    # Nodepool provider interface
    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.ready:
            return

        # Check config-map
        cm_name = "zuul-authorized-keys"
        for pool in self.provider.pools.values():
            configmaps = self.configmap(pool.namespace, 'get')
            if not [True for configmap in configmaps.items
                    if configmap.metadata.name == cm_name]:
                # Create config map
                self.configmap(pool.namespace, 'create', cm_name)
                self.log.info("Created configmap in %s" % pool.namespace)
            elif not [True for configmap in configmaps.items
                      if configmap.data.get(
                          'authorized_keys') == self.zuul_public_key]:
                # Update config map
                self.configmap(pool.namespace, 'delete', cm_name)
                self.configmap(pool.namespace, 'create', cm_name)
                self.log.info("Updated configmap in %s" % pool.namespace)

        # Check running pod
        for pool in self.provider.pools.values():
            for pod in self.pod(pool.namespace, 'get'):
                self.pods.setdefault(pool.namespace, set()).add(
                    pod.metadata.name)
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, name, provider):
                self.id = name
                self.name = name
                self.metadata = {
                    'nodepool_provider_name': provider.name,
                    'nodepool_node_id': self.id.split('-', 1)[0],
                }

            def get(self, name, default=None):
                return getattr(self, name, default)

        for pods in self.pods.values():
            for server_name in pods:
                servers.append(FakeServer(server_name, self.provider))
        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        # K8S Provider doesn't have sub thread
        pass

    def cleanupLeakedResources(self):
        pass

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        namespace = None
        for ns, pods in self.pods.items():
            if server_id in pods:
                namespace = ns
                break
        if not namespace:
            return
        self.pod(namespace, 'delete', server_id)
        self.pods[namespace].remove(server_id)

    def waitForNodeCleanup(self, server_id):
        # K8S cleanup is synchronous
        pass

    def createContainer(self, pool, server_id, port, label):
        if not self.ready:
            self.log.warning("Creating container when provider isn't ready")
            for retry in range(60):
                if self.ready:
                    break
                time.sleep(1)
            if retry == 59:
                self.log.warning("Fail to initialize manager")
                return None

        return self.pod(pool.namespace, 'create', server_id, port, label)

    def getContainer(self, pool, server_id):
        return self.pod(pool.namespace, 'get', server_id)

    def getRequestHandler(self, poolworker, request):
        return KubernetesNodeRequestHandler(poolworker, request)

    @staticmethod
    def render_pod(namespace, server_id, port, label):
        return {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                'name': server_id,
                'namespace': namespace,
            },
            'spec': {
                'containers': [{
                    'name': server_id,
                    'image': label.image,
                    'imagePullPolicy': label.image_pull,
                    'ports': [{
                        'containerPort': 22,
                        'hostPort': port,
                    }],
                    'volumeMounts': [{
                        'name': 'config-volume',
                        'mountPath': '/home/%s/.ssh' % label.username,
                    }],
                }],
                'volumes': [{
                    'name': 'config-volume',
                    'configMap': {
                        'name': 'zuul-authorized-keys',
                    }
                }],
            },
            'restartPolicy': 'Never',
        }

    @staticmethod
    def render_configmap(namespace, name, zuul_authorized_keys):
        return {
            'apiVersion': 'v1',
            'kind': 'ConfigMap',
            'metadata': {
                'name': name,
                'namespace': namespace,
            },
            'data': {
                'authorized_keys': zuul_authorized_keys,
            },
        }
