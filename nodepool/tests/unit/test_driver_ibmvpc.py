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

import os
import logging

from nodepool import tests
from nodepool import zk
from nodepool.driver.statemachine import StateMachineProvider
from nodepool.driver.ibmvpc.adapter import IBMVPCAdapter

import fixtures

from . import fake_ibmvpc
from . import fake_ibmboto


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


class TestDriverIBMVPC(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverIBMVPC")

    def setUp(self):
        super().setUp()

        StateMachineProvider.MINIMUM_SLEEP = 0.1
        StateMachineProvider.MAXIMUM_SLEEP = 1
        IBMVPCAdapter.IMAGE_UPLOAD_SLEEP = 1
        os.environ['VPC_AUTH_TYPE'] = 'bearerToken'
        os.environ['VPC_BEARER_TOKEN'] = 'token'
        os.environ['RESOURCE_MANAGER_AUTH_TYPE'] = 'bearerToken'
        os.environ['RESOURCE_MANAGER_BEARER_TOKEN'] = 'token'
        os.environ['RESOURCE_CONTROLLER_AUTH_TYPE'] = 'bearerToken'
        os.environ['RESOURCE_CONTROLLER_BEARER_TOKEN'] = 'token'

        self.fake_ibmvpc = fake_ibmvpc.FakeIBMVPCFixture()
        self.useFixture(self.fake_ibmvpc)

    def test_ibmvpc_cloud_image_private(self):
        configfile = self.setup_config('ibmvpc.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('debian-private')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.host_keys, [])
        self.assertEqual(node.python_path, 'auto')

        node.state = zk.USED
        self.zk.storeNode(node)

        self.log.debug("Waiting for node to be deleted")
        self.waitForNodeDeletion(node)

    def test_ibmvpc_cloud_image_public(self):
        configfile = self.setup_config('ibmvpc.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('debian-public')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.python_path, 'auto')

        node.state = zk.USED
        self.zk.storeNode(node)

        self.log.debug("Waiting for node to be deleted")
        self.waitForNodeDeletion(node)

    def test_ibmvpc_cloud_image_filter(self):
        configfile = self.setup_config('ibmvpc.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('debian-filter')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.host_keys, [])

        node.state = zk.USED
        self.zk.storeNode(node)

        self.log.debug("Waiting for node to be deleted")
        self.waitForNodeDeletion(node)

    def test_ibmvpc_diskimage(self):
        self.useFixture(fixtures.MonkeyPatch(
            'ibm_boto3.client',
            fake_ibmboto.client))

        configfile = self.setup_config('ibmvpc.yaml')

        self.useBuilder(configfile)
        image = self.waitForImage('ibmvpc', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-image')

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
        self.assertEqual(node.username, 'zuul')
