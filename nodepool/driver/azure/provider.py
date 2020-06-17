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
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.compute import ComputeManagementClient
from msrestazure.azure_exceptions import CloudError

from nodepool.driver import Provider
from nodepool.driver.azure import handler
from nodepool import zk


class AzureProvider(Provider):
    log = logging.getLogger("nodepool.driver.azure.AzureProvider")

    API_VERSION_COMPUTE = "2019-12-01"
    API_VERSION_DISKS = "2019-11-01"
    API_VERSION_NETWORK = "2020-03-01"
    API_VERSION_RESOURCE = "2019-10-01"

    def __init__(self, provider, *args):
        self.provider = provider
        self.zuul_public_key = provider.zuul_public_key
        self.compute_client = None
        self.disks_client = None
        self.network_client = None
        self.resource_client = None
        self.resource_group = provider.resource_group
        self.resource_group_location = provider.resource_group_location
        self._zk = None

    def start(self, zk_conn):
        self.log.debug("Starting")
        self._zk = zk_conn
        self.log.debug(
            "Using %s as auth_path for Azure auth" % self.provider.auth_path)
        if self.compute_client is None:
            self.compute_client = self._get_compute_client()
        if self.disks_client is None:
            self.disks_client = self._get_disks_client()
        if self.network_client is None:
            self.network_client = self._get_network_client()
        if self.resource_client is None:
            self.resource_client = self._get_resource_client()

    def _get_compute_client(self):
        return get_client_from_auth_file(
            ComputeManagementClient,
            auth_path=self.provider.auth_path,
            api_version=self.API_VERSION_COMPUTE
        )

    def _get_disks_client(self):
        return get_client_from_auth_file(
            ComputeManagementClient,
            auth_path=self.provider.auth_path,
            api_version=self.API_VERSION_DISKS
        )

    def _get_network_client(self):
        return get_client_from_auth_file(
            NetworkManagementClient,
            auth_path=self.provider.auth_path,
            api_version=self.API_VERSION_NETWORK
        )

    def _get_resource_client(self):
        return get_client_from_auth_file(
            ResourceManagementClient,
            auth_path=self.provider.auth_path,
            api_version=self.API_VERSION_RESOURCE
        )

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        return self.compute_client.virtual_machines.list(self.resource_group)

    def listNICs(self):
        return self.network_client.network_interfaces.list(self.resource_group)

    def listPIPs(self):
        return self.network_client.public_ip_addresses.list(
            self.resource_group)

    def listDisks(self):
        return self.disks_client.disks.list_by_resource_group(
            self.resource_group)

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def getRequestHandler(self, poolworker, request):
        return handler.AzureNodeRequestHandler(poolworker, request)

    def cleanupLeakedResources(self):
        self._cleanupLeakedNodes()
        self._cleanupLeakedNICs()
        self._cleanupLeakedPIPs()
        self._cleanupLeakedDisks()

    def _cleanupLeakedDisks(self):
        for disk in self.listDisks():
            if disk.tags is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in disk.tags:
                continue
            if disk.tags['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(disk.tags['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked Disk %s (%s) in %s "
                    "(unknown node id %s)",
                    disk.name, disk.id, self.provider.name,
                    disk.tags['nodepool_id']
                )
                try:
                    self.disks_client.disks.delete(
                        self.resource_group,
                        disk.name).wait()
                except CloudError as e:
                    self.log.warning(
                        "Failed to cleanup Disk %s (%s). Error: %r",
                        disk.name, disk.id, e
                    )

    def _cleanupLeakedNICs(self):
        for nic in self.listNICs():
            if nic.tags is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in nic.tags:
                continue
            if nic.tags['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(nic.tags['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked NIC %s (%s) in %s "
                    "(unknown node id %s)",
                    nic.name, nic.id, self.provider.name,
                    nic.tags['nodepool_id']
                )
                try:
                    self.network_client.network_interfaces.delete(
                        self.resource_group,
                        nic.name).wait()
                except CloudError as e:
                    self.log.warning(
                        "Failed to cleanup NIC %s (%s). Error: %r",
                        nic.name, nic.id, e
                    )

    def _cleanupLeakedPIPs(self):
        for pip in self.listPIPs():
            if pip.tags is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in pip.tags:
                continue
            if pip.tags['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(pip.tags['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked PIP %s (%s) in %s "
                    "(unknown node id %s)",
                    pip.name, pip.id, self.provider.name,
                    pip.tags['nodepool_id']
                )
                try:
                    self.network_client.public_ip_addresses.delete(
                        self.resource_group,
                        pip.name).wait()
                except CloudError as e:
                    self.log.warning(
                        "Failed to cleanup IP %s (%s). Error: %r",
                        pip.name, pip.id, e
                    )

    def _cleanupLeakedNodes(self):

        deleting_nodes = {}

        for node in self._zk.nodeIterator():
            if node.state == zk.DELETING:
                if node.provider != self.provider.name:
                    continue
                if node.provider not in deleting_nodes:
                    deleting_nodes[node.provider] = []
                deleting_nodes[node.provider].append(node.external_id)

        for n in self.listNodes():
            if n.tags is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in n.tags:
                continue
            if n.tags['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if (self.provider.name in deleting_nodes and
                n.id in deleting_nodes[self.provider.name]):
                # Already deleting this node
                continue
            if not self._zk.getNode(n.tags['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked instance %s (%s) in %s "
                    "(unknown node id %s)",
                    n.name, n.id, self.provider.name,
                    n.tags['nodepool_id']
                )
                node = zk.Node()
                node.external_id = n.id
                node.provider = self.provider.name
                node.state = zk.DELETING
                self._zk.storeNode(node)

    def cleanupNode(self, server_id):
        self.log.debug('Server ID: %s' % server_id)
        try:
            vm = self.compute_client.virtual_machines.get(
                self.resource_group, server_id.rsplit('/', 1)[1])
        except CloudError as e:
            if e.status_code == 404:
                return
            self.log.warning(
                "Failed to cleanup node %s. Error: %r",
                server_id, e
            )

        self.compute_client.virtual_machines.delete(
            self.resource_group, server_id.rsplit('/', 1)[1]).wait()

        nic_deletion = self.network_client.network_interfaces.delete(
            self.resource_group, "%s-nic" % server_id.rsplit('/', 1)[1])
        nic_deletion.wait()

        pip_deletion = self.network_client.public_ip_addresses.delete(
            self.resource_group, "%s-nic-pip" % server_id.rsplit('/', 1)[1])
        pip_deletion.wait()

        if self.provider.ipv6:
            pip_deletion = self.network_client.public_ip_addresses.delete(
                self.resource_group,
                "%s-nic-v6-pip" % server_id.rsplit('/', 1)[1])
            pip_deletion.wait()

        disk_handle_list = []
        for disk in self.listDisks():
            if disk.tags is not None and \
                disk.tags.get('nodepool_id') == vm.tags['nodepool_id']:
                async_disk_delete = self.disks_client.disks.delete(
                    self.resource_group, disk.name)
                disk_handle_list.append(async_disk_delete)
        for async_disk_delete in disk_handle_list:
            async_disk_delete.wait()

    def waitForNodeCleanup(self, server_id):
        # All async tasks are handled in cleanupNode
        return True

    def getInstance(self, server_id):
        return self.compute_client.virtual_machines.get(
            self.resource_group, server_id, expand='instanceView')

    def createInstance(
            self, hostname, label, nodepool_id, nodepool_node_label=None):

        self.log.debug("Create resouce group")

        tags = label.tags or {}
        tags['nodepool_provider_name'] = self.provider.name
        if nodepool_node_label:
            tags['nodepool_node_label'] = nodepool_node_label

        self.resource_client.resource_groups.create_or_update(
            self.resource_group, {
                'location': self.provider.resource_group_location,
                'tags': tags
            })
        tags['nodepool_id'] = nodepool_id
        v4_params_create = {
            'location': self.provider.location,
            'public_ip_allocation_method': 'dynamic',
            'tags': tags,
        }
        v4_pip_poll = self.network_client.public_ip_addresses.create_or_update(
            self.resource_group,
            "%s-nic-pip" % hostname,
            v4_params_create,
        )
        v4_public_ip = v4_pip_poll.result()

        nic_data = {
            'location': self.provider.location,
            'tags': tags,
            'ip_configurations': [{
                'name': "zuul-v4-ip-config",
                'private_ip_address_version': 'IPv4',
                'subnet': {
                    'id': self.provider.subnet_id
                },
                'public_ip_address': {
                    'id': v4_public_ip.id
                }
            }]
        }

        if self.provider.ipv6:
            nic_data['ip_configurations'].append({
                'name': "zuul-v6-ip-config",
                'private_ip_address_version': 'IPv6',
                'subnet': {
                    'id': self.provider.subnet_id
                }
            })

        nic_creation = self.network_client.network_interfaces.create_or_update(
            self.resource_group,
            "%s-nic" % hostname,
            nic_data
        )

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
                'hardware_profile': {
                    'vmSize': label.hardwareProfile["vm-size"]
                },
                'storage_profile': {'image_reference': label.imageReference},
                'network_profile': {
                    'network_interfaces': [{
                        'id': nic.id,
                        'properties': {
                            'primary': True,
                        }
                    }]
                },
                'tags': tags,
            })
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

    def getv6Ipaddress(self, instance):
        # Copied from https://github.com/Azure/azure-sdk-for-python/issues/897
        ni_reference = instance.network_profile.network_interfaces[0]
        ni_reference = ni_reference.id.split('/')
        ni_group = ni_reference[4]
        ni_name = ni_reference[8]

        net_interface = self.network_client.network_interfaces.get(
            ni_group, ni_name)
        return net_interface.ip_configurations[1].private_ip_address
