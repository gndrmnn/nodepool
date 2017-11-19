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
import json
import time
import urllib.request

from nodepool import exceptions
from nodepool.driver import Provider


class KubernetesProvider(Provider):
    log = logging.getLogger("nodepool.driver.k8s.KubernetesProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.api_url = provider.apiserver_url
        self.api_headers = {'Content-Type': 'application/json'}
        if provider.token:
            self.api_headers['Authorization'] = 'Bearer %s' % provider.token
        self.zuul_public_key = provider.zuul_public_key
        self.ready = False
        self.pods = set()

    # Low level api
    def api_get(self, namespace, objname, name=None):
        url = "%s/api/v1/namespaces/%s/%s" % (self.api_url, namespace, objname)
        if name is not None:
            url += "/%s" % name
        self.log.debug("HTTP GET request: %s" % url)
        req = urllib.request.Request(url, headers=self.api_headers)
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))

    def api_post(self, namespace, objname, data):
        url = "%s/api/v1/namespaces/%s/%s" % (self.api_url, namespace, objname)
        self.log.debug("HTTP POST request: %s" % url)
        req = urllib.request.Request(url, data=data.encode('utf-8'),
                                     method='POST', headers=self.api_headers)
        with urllib.request.urlopen(req) as response:
            return response.code == 201

    def api_delete(self, namespace, objname, name):
        url = "%s/api/v1/namespaces/%s/%s/%s" % (
            self.api_url, namespace, objname, name)
        self.log.debug("HTTP DELETE request: %s" % url)
        req = urllib.request.Request(
            url, method='DELETE', headers=self.api_headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.code == 200
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    # High level api
    def configmap(self, namespace, action, name='zuul-authorized-key'):
        if action == 'create':
            if not self.api_post(
                    namespace, "configmaps", self.render_configmap(
                        namespace, name, self.zuul_public_key)):
                self.log.error("Couldn't create configmap %s in %s" % (
                    name, namespace
                ))
        elif action == 'delete':
            if not self.api_delete(namespace, "configmaps", name):
                self.log.error("Couldn't delete configmap %s in %s" % (
                    name, namespace
                ))

    def pod(self, namespace, action, name=None, port=None, label=None):
        if action == 'create':
            if not self.api_post(namespace, "pods", self.render_pod(
                    namespace, name, port, label)):
                raise exceptions.LaunchStatusException(
                    "Couldn't create pod %s in %s" % (name, namespace))
            return name
        elif action == 'get':
            if name:
                return self.api_get(namespace, "pods", name).get('status', {})
            return self.api_get(namespace, "pods").get('items', [])
        elif action == 'delete':
            if namespace is None:
                namespaces = [pool.namespace
                              for pool in self.provider.pools.values()]
            else:
                namespaces = [namespace]
            delete_failures = 0
            for namespace in namespaces:
                if not self.api_delete(namespace, "pods", name):
                    delete_failures += 1
            if delete_failures == len(namespaces):
                raise exceptions.ServerDeleteException(
                    "server %s deletion failed" % name)

    # Nodepool provider interface
    def start(self):
        self.log.debug("Starting")
        if self.ready:
            return

        # Check config-map
        for pool in self.provider.pools.values():
            configmaps = self.api_get(pool.namespace, "configmaps")
            if not [True for configmap in configmaps.get('items', [])
                    if configmap.get('metadata', {}).get(
                        'name') == 'zuul-authorized-keys']:
                # Create config map
                self.configmap(pool.namespace, 'create')
                self.log.info("Created configmap in %s" % pool.namespace)
            elif not [True for configmap in configmaps.get('items', [])
                      if configmap.get('data', {}).get(
                          'authorized_keys') == self.zuul_public_key]:
                # Update config map
                self.configmap(pool.namespace, 'delete')
                self.configmap(pool.namespace, 'create')
                self.log.info("Updated configmap in %s" % pool.namespace)

        # Check running pod
        for pool in self.provider.pools.values():
            for pod in self.pod(pool.namespace, "get"):
                self.pods.add(pod["metadata"]["name"])
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

        for server_name in self.pods:
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
        if server_id not in self.pods:
            return
        self.pod(None, 'delete', server_id)
        self.pods.remove(server_id)

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

    @staticmethod
    def render_pod(namespace, server_id, port, label):
        return json.dumps({
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
        })

    @staticmethod
    def render_configmap(namespace, name, zuul_authorized_keys):
        return json.dumps({
            'apiVersion': 'v1',
            'kind': 'ConfigMap',
            'metadata': {
                'name': name,
                'namespace': namespace,
            },
            'data': {
                'authorized_keys': zuul_authorized_keys,
            },
        })
