# Copyright (C) 2018 Red Hat
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

import logging

from nodepool import tests
from nodepool import zk
from nodepool.driver.statemachine import StateMachineProvider

from . import fake_azure


class TestDriverAzure(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAzure")

    def setUp(self):
        super().setUp()

        StateMachineProvider.MINIMUM_SLEEP = 0.1
        StateMachineProvider.MAXIMUM_SLEEP = 1
        self.fake_azure = fake_azure.FakeAzureFixture()
        self.useFixture(self.fake_azure)

    def test_azure_cloud_image(self):
        configfile = self.setup_config(
            'azure.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('bionic')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')

    def test_azure_diskimage(self):
        configfile = self.setup_config(
            'azure-diskimage.yaml',
            auth_path=self.fake_azure.auth_file.name)

        self.useBuilder(configfile)
        image = self.waitForImage('azure', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        configfile = self.setup_config(
            'azure-diskimage.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('bionic')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
