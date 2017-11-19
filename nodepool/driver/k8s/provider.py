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

from nodepool.driver import Provider
from nodepool.nodeutils import keyscan


class KubernetesProvider(Provider):
    log = logging.getLogger("nodepool.driver.k8s.KubernetesProvider")

    def __init__(self, provider):
        self.provider = provider
        self.api_url = provider.apiserver_url
        self.zuul_public_key = provider.zuul_public_key
        self.ready = False

    # Low level api
    def api_get(self, namespace, objname, name=None):
        url = "%s/api/v1/namespaces/%s/%s" % (self.api_url, namespace, objname)
        if name is not None:
            url += "/%s" % name
        self.log.debug("HTTP GET request: %s" % url)
        with urllib.request.urlopen(url) as response:
            return json.loads(response.read().decode('utf-8'))

    def api_post(self, namespace, objname, data):
        url = "%s/api/v1/namespaces/%s/%s" % (self.api_url, namespace, objname)
        self.log.debug("HTTP POST request: %s" % url)
        req = urllib.request.Request(url, data=data.encode('utf-8'),
                                     method='POST', headers={
                                         'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            return response.code == 201

    def api_delete(self, namespace, objname, name):
        url = "%s/api/v1/namespaces/%s/%s/%s" % (
            self.api_url, namespace, objname, name)
        self.log.debug("HTTP DELETE request: %s" % url)
        req = urllib.request.Request(url, method='DELETE')
        with urllib.request.urlopen(req) as response:
            return response.code ==  200

    # High level api
    def configmap(self, namespace, action, name):
        if action == 'create':
            if not self.api_post(namespace, "configmaps", self.render_configmap(
                namespace, name, self.zuul_public_key)):
                self.log.error("Couldn't create configmap %s in %s" % (
                    name, namespace
                ))
        elif action == 'delete':
            if not self.api_delete(namespace, "configmaps", name):
                self.log.error("Couldn't delete configmap %s in %s" % (
                    name, namespace
                ))

    def pod(self, namespace, action, name, port=None, image=None, user=None):
        if action == 'create':
            if not self.api_post(namespace, "pods", self.render_pod(
                    namespace, name, port, image, user)):
                self.log.error("Couldn't create pod %s in %s" % (name,
                                                                 namespace))
                return None
            # TODO: refactor this logic to a request handler asynchronous code
            for retry in range(10):
                status = self.api_get(namespace, "pods", name).get('status', {})
                if status.get('phase') == 'Running':
                    break
                self.log.debug("Pod %s is %s" % (name, status.get('phase')))
                time.sleep(1)
            if retry == 9:
                self.log.error("Couldn't create pod %s in %s: %s" % (
                    name, namespace, status.get('phase')
                ))
                return None
            self.log.info("Created pod %s in %s: %s" % (
                name, namespace, status.get('hostIP')
            ))
            return status.get('hostIP')
        elif action == 'delete':
            if namespace is None:
                namespaces = [pool.namespace
                              for pool in self.provider.pools.values()]
            else:
                namespaces = [namespace]
            for namespace in namespaces:
                if not self.api_delete(namespace, "pods", name):
                    self.log.error("Couldn't delete pods %s in %s" % (
                        name, namespace))


    # Nodepool provider interface
    def start(self):
        if self.ready:
            return True
        self.log.debug("Starting")

        # Check config-map
        for pool in self.provider.pools.values():
            configmaps = self.api_get(pool.namespace, "configmaps")
            if not [True for configmap in configmaps.get('items', [])
                    if configmap.get('metadata', {}).get(
                            'name') == 'zuul-authorized-keys']:
                # Create config map
                self.configmap(pool.namespace, 'create', 'zuul-authorized-keys')
                self.log.info("Created configmap in %s" % pool.namespace)
            elif not [True for configmap in configmaps.get('items', [])
                      if configmap.get('data', {}).get(
                              'authorized_keys') == self.zuul_public_key]:
                # Update config map
                self.configmap(pool.namespace, 'delete', 'zuul-authorized-keys')
                self.configmap(pool.namespace, 'create', 'zuul-authorized-keys')
                self.log.info("Updated configmap in %s" % pool.namespace)

        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        # TODO: track pods
        return []

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: track pods and clean aliens
        pass

    def cleanupNode(self, server_id):
        if not self.ready:
            return False
        return self.pod(None, 'delete', server_id)

    def waitForNodeCleanup(self, server_id):
        # TODO: track pos deletion
        return True

    def createContainer(self, pool, server_id, port, image, username):
        if not self.ready:
            for retry in range(60):
                if self.ready:
                    break
                time.sleep(1)
            if retry == 59:
                self.log.warning("Fail to initialize manager")
                return None

        server_ip = self.pod(pool.namespace, 'create', server_id, port, image,
                             username)
        if not server_ip:
            return None
        try:
            key = keyscan(server_ip, port=port, timeout=15)
        except:
            self.log.exception("Can't scan container key")
            return None
        return (server_ip, key)

    @staticmethod
    def render_pod(namespace, server_id, port, image, username):
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
                    'image': image,
                    'ports': [{
                        'containerPort': 22,
                        'hostPort': port,
                    }],
                    'volumeMounts': [{
                        'name': 'config-volume',
                        'mountPath': '/home/%s/.ssh' % username,
                    }]
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
