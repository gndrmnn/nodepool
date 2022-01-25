# Copyright (C) 2021 Acme Gating, LLC
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

import os
import logging

import testtools

from nodepool import tests
from nodepool import zk
from nodepool.driver.statemachine import StateMachineProvider
from nodepool.cmd.config_validator import ConfigValidator


class TestDriverMetastatic(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverMetastatic")

    def setUp(self):
        super().setUp()
        StateMachineProvider.MINIMUM_SLEEP = 0.1
        StateMachineProvider.MAXIMUM_SLEEP = 1

    def _requestNode(self):
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.tenant_name = 'tenant-1'
        req.node_types.append('user-label')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.provider, 'meta-provider')

        return node

    def _getNodes(self):
        nodes = [n for n in self.zk.nodeIterator()]
        nodes = sorted(nodes, key=lambda n: n.id)
        self.log.debug("Nodes:")
        for n in nodes:
            self.log.debug('  %s %s', n.id, n.provider)
        return nodes

    def test_metastatic_validator(self):
        # Test schema validation
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'metastatic_ok.yaml')
        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 0)

        # Test runtime value assertions
        configfile = self.setup_config('config_validate/metastatic_error.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        with testtools.ExpectedException(Exception, 'Multiple label def'):
            pool.loadConfig()

        configfile = self.setup_config('config_validate/metastatic_ok.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.loadConfig()

    def test_metastatic(self):
        configfile = self.setup_config('metastatic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="fake-image")

        # Request a node, verify that there is a backing node, and it
        # has the same connection info
        node1 = self._requestNode()
        nodes = self._getNodes()
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0], node1)
        self.assertNotEqual(nodes[1], node1)
        bn1 = nodes[1]
        self.assertEqual(bn1.provider, 'fake-provider')
        self.assertEqual(bn1.interface_ip, node1.interface_ip)
        self.assertEqual(bn1.python_path, node1.python_path)
        self.assertEqual('auto', node1.python_path)
        self.assertEqual(bn1.shell_type, node1.shell_type)
        self.assertEqual(None, node1.shell_type)
        self.assertEqual(bn1.host_keys, node1.host_keys)
        self.assertEqual(['ssh-rsa FAKEKEY'], node1.host_keys)
        self.assertEqual(bn1.id, node1.driver_data['backing_node'])

        # Allocate a second node, should have same backing node
        node2 = self._requestNode()
        nodes = self._getNodes()
        self.assertEqual(nodes, [node1, bn1, node2])
        self.assertEqual(bn1.id, node2.driver_data['backing_node'])

        # Allocate a third node, should have a second backing node
        node3 = self._requestNode()
        nodes = self._getNodes()
        self.assertNotEqual(nodes[4], node1)
        self.assertNotEqual(nodes[4], node2)
        self.assertNotEqual(nodes[4], node3)
        bn2 = nodes[4]
        self.assertEqual(nodes, [node1, bn1, node2, node3, bn2])
        self.assertEqual(bn2.id, node3.driver_data['backing_node'])
        self.assertNotEqual(bn1.id, bn2.id)

        # Delete node #2, verify that both backing nodes exist
        node2.state = zk.DELETING
        self.zk.storeNode(node2)
        self.waitForNodeDeletion(node2)

        # Allocate a replacement, verify it occupies slot 2
        node4 = self._requestNode()
        nodes = self._getNodes()
        self.assertEqual(nodes, [node1, bn1, node3, bn2, node4])
        self.assertEqual(bn1.id, node4.driver_data['backing_node'])

        # Delete #4 and #1.  verify backing node 1 is removed
        node4.state = zk.DELETING
        self.zk.storeNode(node4)
        node1.state = zk.DELETING
        self.zk.storeNode(node1)
        self.waitForNodeDeletion(node4)
        self.waitForNodeDeletion(node1)
        self.waitForNodeDeletion(bn1)
        nodes = self._getNodes()
        self.assertEqual(nodes, [node3, bn2])

    def test_metastatic_startup(self):
        configfile = self.setup_config('metastatic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="fake-image")

        # Request a node, verify that there is a backing node, and it
        # has the same connection info
        node1 = self._requestNode()
        nodes = self._getNodes()
        bn1 = nodes[1]
        self.assertEqual(nodes, [node1, bn1])

        # Restart the provider and make sure we load data correctly
        pool.stop()
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="fake-image")

        # Allocate a second node, should have same backing node
        node2 = self._requestNode()
        nodes = self._getNodes()
        self.assertEqual(nodes, [node1, bn1, node2])
        self.assertEqual(bn1.id, node2.driver_data['backing_node'])

        # Allocate a third node, should have a second backing node
        node3 = self._requestNode()
        nodes = self._getNodes()
        bn2 = nodes[4]
        self.assertEqual(nodes, [node1, bn1, node2, node3, bn2])
        self.assertEqual(bn2.id, node3.driver_data['backing_node'])
        self.assertNotEqual(bn1.id, bn2.id)
