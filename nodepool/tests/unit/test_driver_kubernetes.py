# Copyright (C) 2018 Red Hat
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
        self.namespaces = []

        class FakeApi:
            class configuration:
                host = "http://localhost:8080"
                verify_ssl = False
        self.api_client = FakeApi()

    def list_namespace(self):
        class FakeNamespaces:
            items = self.namespaces
        return FakeNamespaces

    def create_namespace(self, ns_body):
        class FakeNamespace:
            class metadata:
                name = ns_body['metadata']['name']
                labels = ns_body['metadata']['labels']
        self.namespaces.append(FakeNamespace)
        return FakeNamespace

    def delete_namespace(self, name, body):
        to_delete = None
        for namespace in self.namespaces:
            if namespace.metadata.name == name:
                to_delete = namespace
                break
        if not to_delete:
            raise RuntimeError("Unknown namespace %s" % name)
        self.namespaces.remove(to_delete)

    def create_namespaced_service_account(self, ns, sa_body):
        return

    def read_namespaced_service_account(self, user, ns):
        class FakeSA:
            class secret:
                name = "fake"
        FakeSA.secrets = [FakeSA.secret]
        return FakeSA

    def create_namespaced_secret(self, ns, secret_body):
        return

    def read_namespaced_secret(self, name, ns):
        class FakeSecret:
            data = {'ca.crt': 'ZmFrZS1jYQ==', 'token': 'ZmFrZS10b2tlbg=='}
        return FakeSecret

    def create_namespaced_pod(self, ns, pod_body):
        return

    def read_namespaced_pod(self, name, ns):
        class FakePod:
            class status:
                phase = "Running"

            class spec:
                node_name = "k8s-default-pool-abcd-1234"
        return FakePod


class FakeRbacClient(object):
    def create_namespaced_role(self, ns, role_body):
        return

    def create_namespaced_role_binding(self, ns, role_binding_body):
        return


class TestDriverKubernetes(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverKubernetes")

    def setUp(self):
        super().setUp()
        self.fake_k8s_client = FakeCoreClient()
        self.fake_rbac_client = FakeRbacClient()

        def fake_get_client(log, context, ctor=None):
            return None, None, self.fake_k8s_client, self.fake_rbac_client

        self.useFixture(fixtures.MockPatch(
            'nodepool.driver.kubernetes.provider.get_client',
            fake_get_client))

    def test_kubernetes_machine(self):
        configfile = self.setup_config('kubernetes.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.tenant_name = 'tenant-1'
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
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.cloud, 'admin-cluster.local')
        self.assertEqual(node.host_id, 'k8s-default-pool-abcd-1234')

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)

    def test_kubernetes_native(self):
        configfile = self.setup_config('kubernetes.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('kubernetes-namespace')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'namespace')
        self.assertEqual(node.connection_port.get('token'), 'fake-token')
        self.assertEqual(node.cloud, 'admin-cluster.local')
        self.assertIsNone(node.host_id)

        node.state = zk.DELETING
        self.zk.storeNode(node)

        self.waitForNodeDeletion(node)

    def test_kubernetes_default_label_resources(self):
        configfile = self.setup_config('kubernetes-default-limits.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('pod-default')
        req.node_types.append('pod-custom-cpu')
        req.node_types.append('pod-custom-mem')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        node_default = self.zk.getNode(req.nodes[0])
        node_cust_cpu = self.zk.getNode(req.nodes[1])
        node_cust_mem = self.zk.getNode(req.nodes[2])

        resources_default = {
            'instances': 1,
            'cores': 2,
            'ram': 1024,
        }
        resources_cust_cpu = {
            'instances': 1,
            'cores': 4,
            'ram': 1024,
        }
        resources_cust_mem = {
            'instances': 1,
            'cores': 2,
            'ram': 2048,
        }

        self.assertDictEqual(resources_default, node_default.resources)
        self.assertDictEqual(resources_cust_cpu, node_cust_cpu.resources)
        self.assertDictEqual(resources_cust_mem, node_cust_mem.resources)

        for node in (node_default, node_cust_cpu, node_cust_mem):
            node.state = zk.DELETING
            self.zk.storeNode(node)
            self.waitForNodeDeletion(node)

    def test_kubernetes_pool_quota_servers(self):
        self._test_kubernetes_quota('kubernetes-pool-quota-servers.yaml')

    def test_kubernetes_pool_quota_cores(self):
        self._test_kubernetes_quota('kubernetes-pool-quota-cores.yaml')

    def test_kubernetes_pool_quota_ram(self):
        self._test_kubernetes_quota('kubernetes-pool-quota-ram.yaml')

    def test_kubernetes_tenant_quota_servers(self):
        self._test_kubernetes_quota(
            'kubernetes-tenant-quota-servers.yaml', pause=False)

    def test_kubernetes_tenant_quota_cores(self):
        self._test_kubernetes_quota(
            'kubernetes-tenant-quota-cores.yaml', pause=False)

    def test_kubernetes_tenant_quota_ram(self):
        self._test_kubernetes_quota(
            'kubernetes-tenant-quota-ram.yaml', pause=False)

    def test_kubernetes_leaked_node(self):
        conf = self.setup_config('kubernetes-leaked-node.yaml')
        pool = self.useNodepool(conf, watermark_sleep=1)
        pool.cleanup_interval = 1
        pool.start()

        # wait for min-ready node to be available
        nodes = self.waitForNodes('pod-fedora')
        self.assertEqual(len(nodes), 1)
        manager = pool.getProviderManager('kubespray')
        servers = manager.listNodes()
        self.assertEqual(len(servers), 1)

        # delete node from zk so it becomes 'leaked'
        self.zk.deleteNode(nodes[0])

        # node gets replaced, wait for that
        new_nodes = self.waitForNodes('pod-fedora')
        self.assertEqual(len(new_nodes), 1)

        # original node should get deleted eventually
        self.waitForInstanceDeletion(manager, nodes[0].external_id)

        servers = manager.listNodes()
        self.assertEqual(len(servers), 1)

    def _test_kubernetes_quota(self, config, pause=True):
        configfile = self.setup_config(config)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
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
            pool_worker = pool.getPoolWorkers('kubespray')
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
