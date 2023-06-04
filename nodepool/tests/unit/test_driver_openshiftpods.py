# Copyright (C) 2018 Red Hat
# Copyright 2023 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import fixtures
import logging
import time

from nodepool import tests
from nodepool.zk import zookeeper as zk


class FakeCoreClient(object):
    def __init__(self):
        self.pods = []

        class FakeApi:
            class configuration:
                host = "http://localhost:8080"
                verify_ssl = False
        self.api_client = FakeApi()

    def list_namespaced_pod(self, project):
        class FakePods:
            items = self.pods
        return FakePods

    def create_namespaced_pod(self, ns, pod_body):
        class FakePod:
            class metadata:
                name = pod_body['metadata']['name']
        self.pods.append(FakePod)
        return FakePod

    def read_namespaced_pod(self, name, ns):
        exist = False
        for pod in self.pods:
            if pod.metadata.name == name:
                exist = True
                break
        if not exist:
            raise RuntimeError("Pod doesn't exists")

        class FakePod:
            class status:
                phase = "Running"

            class spec:
                node_name = "k8s-default-pool-abcd-1234"
        return FakePod

    def delete_namespaced_pod(self, name, project):
        to_delete = None
        for pod in self.pods:
            if pod.metadata.name == name:
                to_delete = pod
                break
        if not to_delete:
            raise RuntimeError("Unknown pod %s" % name)
        self.pods.remove(to_delete)


class TestDriverOpenshiftPods(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverOpenshiftPods")

    def setUp(self):
        super().setUp()
        self.fake_k8s_client = FakeCoreClient()

        def fake_get_client(log, context, ctor=None):
            return "fake-token", None, self.fake_k8s_client, None

        self.useFixture(fixtures.MockPatch(
            'nodepool.driver.openshiftpods.provider.get_client',
            fake_get_client))

    def test_openshift_pod(self):
        configfile = self.setup_config('openshiftpods.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.startPool(pool)
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('pod-fedora')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'kubectl')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')
        self.assertIn('ca_crt', node.connection_port)
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.cloud, 'service-account.local')
        self.assertEqual(node.host_id, 'k8s-default-pool-abcd-1234')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)

    def test_openshift_pod_secrets(self):
        configfile = self.setup_config('openshiftpods.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.startPool(pool)
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('pod-fedora-secret')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'kubectl')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)

    def _test_openshift_pod_quota(self, config, pause=True):
        configfile = self.setup_config(config)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.startPool(pool)
        # Start two pods to hit max-server limit
        reqs = []
        for _ in [1, 2]:
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.tenant_name = 'tenant-1'
            req.node_types.append('pod-fedora')
            self.zk.storeNodeRequest(req)
            reqs.append(req)

        fulfilled_reqs = []
        for req in reqs:
            self.log.debug("Waiting for request %s", req.id)
            r = self.waitForNodeRequest(req)
            self.assertEqual(r.state, zk.FULFILLED)
            fulfilled_reqs.append(r)

        # Now request a third pod that will hit the limit
        max_req = zk.NodeRequest()
        max_req.state = zk.REQUESTED
        max_req.tenant_name = 'tenant-1'
        max_req.node_types.append('pod-fedora')
        self.zk.storeNodeRequest(max_req)

        # if at pool quota, the handler will get paused
        # but not if at tenant quota
        if pause:
            # The previous request should pause the handler
            pool_worker = pool.getPoolWorkers('openshift')
            while not pool_worker[0].paused_handlers:
                time.sleep(0.1)
        else:
            self.waitForNodeRequest(max_req, (zk.REQUESTED,))

        # Delete the earlier two pods freeing space for the third.
        for req in fulfilled_reqs:
            node = self.zk.getNode(req.nodes[0])
            node.state = zk.DELETING
            self.zk.storeNode(node)
            self.waitForNodeDeletion(node)

        # We should unpause and fulfill this now
        req = self.waitForNodeRequest(max_req, (zk.FULFILLED,))
        self.assertEqual(req.state, zk.FULFILLED)

    def test_openshift_pod_pool_quota_servers(self):
        # This is specified as max-projects, but named servers here for
        # parity with other driver tests.
        self._test_openshift_pod_quota('openshiftpods-pool-quota-servers.yaml')

    def test_openshift_pod_pool_quota_cores(self):
        self._test_openshift_pod_quota('openshiftpods-pool-quota-cores.yaml')

    def test_openshift_pod_pool_quota_ram(self):
        self._test_openshift_pod_quota('openshiftpods-pool-quota-ram.yaml')

    def test_openshift_pod_pool_quota_extra(self):
        self._test_openshift_pod_quota('openshiftpods-pool-quota-extra.yaml')

    def test_openshift_pod_tenant_quota_servers(self):
        self._test_openshift_pod_quota(
            'openshiftpods-tenant-quota-servers.yaml', pause=False)

    def test_openshift_pod_tenant_quota_cores(self):
        self._test_openshift_pod_quota(
            'openshiftpods-tenant-quota-cores.yaml', pause=False)

    def test_openshift_pod_tenant_quota_ram(self):
        self._test_openshift_pod_quota(
            'openshiftpods-tenant-quota-ram.yaml', pause=False)

    def test_openshift_pod_tenant_quota_extra(self):
        self._test_openshift_pod_quota(
            'openshiftpods-tenant-quota-extra.yaml', pause=False)
