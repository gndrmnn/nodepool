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
import os
import tempfile
from unittest.mock import MagicMock
import yaml

from nodepool import tests
from nodepool import zk
from nodepool import nodeutils as utils
from nodepool.driver.azure import provider, AzureProvider

from azure.common.client_factory import get_client_from_json_dict
from azure.mgmt.resource.resources.v2019_10_01.operations import ResourceGroupsOperations  # noqa
from azure.mgmt.network.v2020_03_01.operations import PublicIPAddressesOperations  # noqa
from azure.mgmt.network.v2020_03_01.operations import NetworkInterfacesOperations  # noqa
from azure.mgmt.compute.v2019_12_01.operations import VirtualMachinesOperations
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.compute import ComputeManagementClient

auth = {
    "clientId": "ad735158-65ca-11e7-ba4d-ecb1d756380e",
    "clientSecret": "b70bb224-65ca-11e7-810c-ecb1d756380e",
    "subscriptionId": "bfc42d3a-65ca-11e7-95cf-ecb1d756380e",
    "tenantId": "c81da1d8-65ca-11e7-b1d1-ecb1d756380e",
    "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
    "resourceManagerEndpointUrl": "https://management.azure.com/",
    "activeDirectoryGraphResourceId": "https://graph.windows.net/",
    "sqlManagementEndpointUrl": "https://management.core.windows.net:8443/",
    "galleryEndpointUrl": "https://gallery.azure.com/",
    "managementEndpointUrl": "https://management.core.windows.net/",
}


class FakeAzureResource:

    def __init__(self, id_, provisioning_state='Unknown'):
        self.id = id_
        self.provisioning_state = provisioning_state


class FakePIPResult:

    @staticmethod
    def result():
        return FakeAzureResource('fake_pip_id')


class FakeNICResult:

    @staticmethod
    def result():
        return FakeAzureResource('fake_nic_id')


class FakeVMResult:

    @staticmethod
    def result():
        return FakeAzureResource('fake_vm_id', provisioning_state='Succeeded')


class TestDriverAzure(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAzure")

    def setUp(self):
        super().setUp()

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, 'cleanupLeakedResources',
            MagicMock()))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, 'cleanupNode',
            MagicMock()))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, 'getIpaddress',
            MagicMock(return_value="127.0.0.1")))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, '_get_compute_client',
            MagicMock(
                return_value=get_client_from_json_dict(
                    ComputeManagementClient, auth, credentials={},
                    api_version=AzureProvider.API_VERSION_COMPUTE
                )
            )
        ))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, '_get_disks_client',
            MagicMock(
                return_value=get_client_from_json_dict(
                    ComputeManagementClient, auth, credentials={},
                    api_version=AzureProvider.API_VERSION_DISKS
                )
            )
        ))

        self.useFixture(fixtures.MockPatchObject(
            utils, 'nodescan',
            MagicMock(return_value="FAKE_KEY")))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, '_get_network_client',
            MagicMock(
                return_value=get_client_from_json_dict(
                    NetworkManagementClient, auth, credentials={},
                    api_version=AzureProvider.API_VERSION_NETWORK
                )
            )
        ))

        self.useFixture(fixtures.MockPatchObject(
            provider.AzureProvider, '_get_resource_client',
            MagicMock(
                return_value=get_client_from_json_dict(
                    ResourceManagementClient, auth, credentials={},
                    api_version=AzureProvider.API_VERSION_RESOURCE
                )
            )
        ))

        self.useFixture(fixtures.MockPatchObject(
            ResourceGroupsOperations, 'create_or_update',
            MagicMock(
                return_value=FakeAzureResource('fake_rg_id'))
        ))

        self.useFixture(fixtures.MockPatchObject(
            PublicIPAddressesOperations, 'create_or_update',
            MagicMock(return_value=FakePIPResult())
        ))

        self.useFixture(fixtures.MockPatchObject(
            NetworkInterfacesOperations, 'create_or_update',
            MagicMock(return_value=FakeNICResult())
        ))

        self.useFixture(fixtures.MockPatchObject(
            VirtualMachinesOperations, 'create_or_update',
            MagicMock(return_value=FakeVMResult())
        ))

    def test_azure_machine(self):
        az_template = os.path.join(
            os.path.dirname(__file__), '..', 'fixtures', 'azure.yaml')
        with open(az_template) as f:
            raw_config = yaml.safe_load(f)
        raw_config['zookeeper-servers'][0] = {
            'host': self.zookeeper_host,
            'port': self.zookeeper_port,
            'chroot': self.zookeeper_chroot,
        }
        raw_config['zookeeper-tls'] = {
            'ca': self.zookeeper_ca,
            'cert': self.zookeeper_cert,
            'key': self.zookeeper_key,
        }
        with tempfile.NamedTemporaryFile() as tf:
            tf.write(yaml.safe_dump(
                raw_config, default_flow_style=False).encode('utf-8'))
            tf.flush()
            configfile = self.setup_config(tf.name)
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
