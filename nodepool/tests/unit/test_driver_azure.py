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


def make_image(name, tags):
    return {
        'name': name,
        'id': ('/subscriptions/c35cf7df-ed75-4c85-be00-535409a85120/'
               'resourceGroups/nodepool/providers/Microsoft.Compute/'
               f'images/{name}'),
        'type': 'Microsoft.Compute/images',
        'location': 'eastus',
        'tags': tags,
        'properties': {
            'storageProfile': {
                'osDisk': {
                    'osType': 'Linux',
                    'osState': 'Generalized',
                    'diskSizeGB': 1,
                    'blobUri': 'https://example.net/nodepoolstorage/img.vhd',
                    'caching': 'ReadWrite',
                    'storageAccountType': 'Standard_LRS'
                },
                'dataDisks': [],
                'zoneResilient': False
            },
            'provisioningState': 'Succeeded',
            'hyperVGeneration': 'V1'
        }
    }


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
        req.tenant_name = 'tenant-1'
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
        self.assertEqual(node.shell_type, 'sh')
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.python_path, 'auto')
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            items[0]['properties']['osProfile']['customData'],
            'VGhpcyBpcyB0aGUgY3VzdG9tIGRhdGE=')  # This is the custom data
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['userData'],
            'VGhpcyBpcyB0aGUgdXNlciBkYXRh')  # This is the user data

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
        self.assertEqual(node.shell_type, None)
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.python_path, 'auto')

    def test_azure_external_image(self):
        configfile = self.setup_config(
            'azure-external-image.yaml',
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
        self.assertEqual(node.shell_type, 'sh')
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            items[0]['properties']['osProfile']['customData'],
            'VGhpcyBpcyB0aGUgY3VzdG9tIGRhdGE=')  # This is the custom data
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['userData'],
            'VGhpcyBpcyB0aGUgdXNlciBkYXRh')  # This is the user data

        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['storageProfile']
            ['imageReference']['id'],
            "/subscriptions/c35cf7df-ed75-4c85-be00-535409a85120"
            "/resourceGroups/nodepool/providers/Microsoft.Compute"
            "/images/test-image-1234")

    def test_azure_image_filter_name(self):
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test1', {'foo': 'bar'}))
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test2', {}))
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test3', {'foo': 'bar'}))

        configfile = self.setup_config(
            'azure.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('image-by-name')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['storageProfile']
            ['imageReference']['id'],
            "/subscriptions/c35cf7df-ed75-4c85-be00-535409a85120"
            "/resourceGroups/nodepool/providers/Microsoft.Compute"
            "/images/test1")

    def test_azure_image_filter_tag(self):
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test1', {'foo': 'bar'}))
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test2', {}))
        self.fake_azure.crud['Microsoft.Compute/images'].items.append(
            make_image('test3', {'foo': 'bar'}))

        configfile = self.setup_config(
            'azure.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('image-by-tag')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['storageProfile']
            ['imageReference']['id'],
            "/subscriptions/c35cf7df-ed75-4c85-be00-535409a85120"
            "/resourceGroups/nodepool/providers/Microsoft.Compute"
            "/images/test3")

    def test_azure_windows_image_password(self):
        configfile = self.setup_config(
            'azure.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('windows-password')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['osProfile']['adminUsername'],
            'foobar')
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['osProfile']['adminPassword'],
            'reallybadpassword123')

    def test_azure_windows_image_generate(self):
        configfile = self.setup_config(
            'azure.yaml',
            auth_path=self.fake_azure.auth_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('windows-generate')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(
            self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
            requests[0]['properties']['osProfile']['adminUsername'],
            'foobar')
        self.assertEqual(
            len(self.fake_azure.crud['Microsoft.Compute/virtualMachines'].
                requests[0]['properties']['osProfile']['adminPassword']),
            64)
