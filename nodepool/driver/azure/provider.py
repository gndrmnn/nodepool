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

from azure.common.client_factory import get_client_from_auth_file
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

from nodepool.driver import Provider

from nodepool.driver.azure import handler


class AzureProvider(Provider):
    log = logging.getLogger("nodepool.driver.azure.AzureProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.zuul_public_key = provider.zuul_public_key
        self.compute_client = None
        self.network_client = None
        self.resource_group = "nodepool-test-instances"

    def start(self):
        self.log.debug("Starting")
        if self.client is None:
            # Set credencials.json path in AZURE_AUTH_LOCATION
            self.compute_client = get_client_from_auth_file(
                ComputeManagementClient)
            self.network_client = get_client_from_auth_file(
                NetworkManagementClient)

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        # TODO
        return []

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def getRequestHandler(self, poolworker, request):
        return handler.AzureNodeRequestHandler(poolworker, request)

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def cleanupNode(self, server_id):
        if self.client is None:
            return False
        vm_deletion = self.compute_client.virtual_machines.delete(
            self.resource_group, server_id)
        vm_deletion.wait()
        # TODO: remove nic

    def waitForNodeCleanup(self, server_id):
        # TODO: track instance deletion
        return True

    def getInstance(self, server_id):
        return self.compute_client.virtual_machines.get(
            self.resource_group, server_id, expand='instanceView')

    def createInstance(self, hostname, label, nodepool_id):
        self.client.resource_groups.create_or_update(
            self.resource_group, {'location': self.provider.location})

        nic_creation = self.network_client.network_interfaces.create_or_update(
            self.resource_group, "%s-nic" % hostname, {
                'location': self.provider.location,
                'ip_configurations': [{
                    'subnet': {self.provider.subnet_id}
                }]
            })
        nic_creation.wait()
        nic = nic_creation.result()

        vm_creation = self.compute_client.virtual_machines.create_or_update(
            self.resource_group, hostname, {
                'location': self.provider.location,
                'os_profile': {
                    'computer_name': hostname,
                    'admin_username': label.username,
                    'linux_configuration': {
                        'ssh': {
                            'public_keys': [{
                                'path': "/home/%s/.ssh/authorized_keys" % (
                                    label.username),
                                'key_data': self.provider.zuul_public_key,
                            }]
                        },
                        "disable_password_authentication": True,
                    }
                },
                'hardware_profile': label.hardwareProfile,
                'storage_profile': {'image_reference': label.imageReference},
                'network_profile': {
                    'network_interfaces': [{
                        'id': nic.id,
                        'properties': {
                            'primary': True,
                        }
                    }]
                },
                'tags': {
                    'nodepool_id': nodepool_id,
                },
            })
        vm_creation.wait()
        return vm_creation.result()

    def getIpaddress(self, instance):
        # Copied from https://github.com/Azure/azure-sdk-for-python/issues/897
        ni_reference = instance.network_profile.network_interfaces[0]
        ni_reference = ni_reference.id.split('/')
        ni_group = ni_reference[4]
        ni_name = ni_reference[8]

        net_interface = self.network_client.network_interfaces.get(
            ni_group, ni_name)
        ip_reference = net_interface.ip_configurations[0].public_ip_address
        ip_reference = ip_reference.id.split('/')
        ip_group = ip_reference[4]
        ip_name = ip_reference[8]

        public_ip = self.network_client.public_ip_addresses.get(
            ip_group, ip_name)
        public_ip = public_ip.ip_address
        return public_ip
