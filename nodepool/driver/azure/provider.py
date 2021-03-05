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
import json

from nodepool.driver import Provider
from nodepool.driver.azure import handler
from nodepool import zk

from . import azul


class AzureProvider(Provider):
    log = logging.getLogger("nodepool.driver.azure.AzureProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.zuul_public_key = provider.zuul_public_key
        self.resource_group = provider.resource_group
        self.resource_group_location = provider.resource_group_location
        self._zk = None

    def start(self, zk_conn):
        self.log.debug("Starting")
        self._zk = zk_conn
        self.log.debug(
            "Using %s as auth_path for Azure auth" % self.provider.auth_path)
        with open(self.provider.auth_path) as f:
            self.azul = azul.AzureCloud(json.load(f))

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        return self.azul.virtual_machines.list(self.resource_group)

    def listNICs(self):
        return self.azul.network_interfaces.list(self.resource_group)

    def listPIPs(self):
        return self.azul.public_ip_addresses.list(self.resource_group)

    def listDisks(self):
        return self.azul.disks.list(self.resource_group)

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
            if disk['tags'] is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in disk['tags']:
                continue
            if disk['tags']['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(disk['tags']['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked Disk %s (%s) in %s "
                    "(unknown node id %s)",
                    disk['name'], disk['id'], self.provider.name,
                    disk['tags']['nodepool_id']
                )
                try:
                    self.azul.wait_for_async_operation(
                        self.azul.disks.delete(
                            self.resource_group,
                            disk['name']))
                except azul.AzureError as e:
                    self.log.warning(
                        "Failed to cleanup Disk %s (%s). Error: %r",
                        disk['name'], disk['id'], e
                    )

    def _cleanupLeakedNICs(self):
        for nic in self.listNICs():
            if nic['tags'] is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in nic['tags']:
                continue
            if nic['tags']['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(nic['tags']['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked NIC %s (%s) in %s "
                    "(unknown node id %s)",
                    nic['name'], nic['id'], self.provider.name,
                    nic['tags']['nodepool_id']
                )
                try:
                    self.azul.wait_for_async_operation(
                        self.azul.network_interfaces.delete(
                            self.resource_group,
                            nic['name']))
                except azul.AzureError as e:
                    self.log.warning(
                        "Failed to cleanup NIC %s (%s). Error: %r",
                        nic['name'], nic['id'], e
                    )

    def _cleanupLeakedPIPs(self):
        for pip in self.listPIPs():
            if pip['tags'] is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in pip['tags']:
                continue
            if pip['tags']['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if not self._zk.getNode(pip['tags']['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked PIP %s (%s) in %s "
                    "(unknown node id %s)",
                    pip['name'], pip['id'], self.provider.name,
                    pip['tags']['nodepool_id']
                )
                try:
                    self.azul.wait_for_async_operation(
                        self.azul.public_ip_addresses.delete(
                            self.resource_group,
                            pip['name']))
                except azul.AzureError as e:
                    self.log.warning(
                        "Failed to cleanup IP %s (%s). Error: %r",
                        pip['name'], pip['id'], e
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
            if n['tags'] is None:
                # Nothing to check ownership against, move on
                continue
            if 'nodepool_provider_name' not in n['tags']:
                continue
            if n['tags']['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue
            if (self.provider.name in deleting_nodes and
                n['id'] in deleting_nodes[self.provider.name]):
                # Already deleting this node
                continue
            if not self._zk.getNode(n['tags']['nodepool_id']):
                self.log.warning(
                    "Marking for delete leaked instance %s (%s) in %s "
                    "(unknown node id %s)",
                    n['name'], n['id'], self.provider.name,
                    n['tags']['nodepool_id']
                )
                node = zk.Node()
                node.external_id = n['name']
                node.provider = self.provider.name
                node.state = zk.DELETING
                self._zk.storeNode(node)

    def cleanupNode(self, server_id):
        self.log.debug('Server ID: %s' % server_id)
        try:
            vm = self.azul.virtual_machines.get(
                self.resource_group, server_id)
        except azul.AzureError as e:
            if e.status_code == 404:
                return
            self.log.warning(
                "Failed to cleanup node %s. Error: %r",
                server_id, e
            )

        self.azul.wait_for_async_operation(
            self.azul.virtual_machines.delete(
                self.resource_group, server_id))

        self.azul.wait_for_async_operation(
            self.azul.network_interfaces.delete(
                self.resource_group, "%s-nic" % server_id))

        self.azul.wait_for_async_operation(
            self.azul.public_ip_addresses.delete(
                self.resource_group,
                "%s-nic-pip" % server_id))

        if self.provider.ipv6:
            self.azul.wait_for_async_operation(
                self.azul.public_ip_addresses.delete(
                    self.resource_group,
                    "%s-nic-v6-pip" % server_id))

        disk_handle_list = []
        for disk in self.listDisks():
            if disk['tags'] is not None and \
                disk['tags'].get('nodepool_id') == vm['tags']['nodepool_id']:
                async_disk_delete = self.azul.disks.delete(
                    self.resource_group, disk['name'])
                disk_handle_list.append(async_disk_delete)
        for async_disk_delete in disk_handle_list:
            self.azul.wait_for_async_operation(
                async_disk_delete)

    def waitForNodeCleanup(self, server_id):
        # All async tasks are handled in cleanupNode
        return True

    def getInstance(self, server_id):
        return self.azul.virtual_machines.get(
            self.resource_group, server_id)

    def createInstance(
            self, hostname, label, nodepool_id, nodepool_node_label=None):

        self.log.debug("Create resouce group")

        tags = label.tags or {}
        tags['nodepool_provider_name'] = self.provider.name
        if nodepool_node_label:
            tags['nodepool_node_label'] = nodepool_node_label

        self.azul.resource_groups.create(
            self.resource_group, {
                'location': self.provider.resource_group_location,
                'tags': tags
            })
        tags['nodepool_id'] = nodepool_id
        v4_params_create = {
            'location': self.provider.location,
            'tags': tags,
            'properties': {
                'publicIpAllocationMethod': 'dynamic',
            },
        }
        v4_public_ip = self.azul.public_ip_addresses.create(
            self.resource_group,
            "%s-nic-pip" % hostname,
            v4_params_create,
        )

        nic_data = {
            'location': self.provider.location,
            'tags': tags,
            'properties': {
                'ipConfigurations': [{
                    'name': "nodepool-v4-ip-config",
                    'properties': {
                        'privateIpAddressVersion': 'IPv4',
                        'subnet': {
                            'id': self.provider.subnet_id
                        },
                        'publicIpAddress': {
                            'id': v4_public_ip['id']
                        }
                    }
                }]
            }
        }

        if self.provider.ipv6:
            nic_data['properties']['ipConfigurations'].append({
                'name': "zuul-v6-ip-config",
                'properties': {
                    'privateIpAddressVersion': 'IPv6',
                    'subnet': {
                        'id': self.provider.subnet_id
                    }
                }
            })

        nic = self.azul.network_interfaces.create(
            self.resource_group,
            "%s-nic" % hostname,
            nic_data
        )

        vm = self.azul.virtual_machines.create(
            self.resource_group, hostname, {
                'location': self.provider.location,
                'tags': tags,
                'properties': {
                    'osProfile': {
                        'computerName': hostname,
                        'adminUsername': label.cloud_image.username,
                        'linuxConfiguration': {
                            'ssh': {
                                'publicKeys': [{
                                    'path': "/home/%s/.ssh/authorized_keys" % (
                                        label.cloud_image.username),
                                    'keyData': label.cloud_image.key,
                                }]
                            },
                            "disablePasswordAuthentication": True,
                        }
                    },
                    'hardwareProfile': {
                        'vmSize': label.hardware_profile["vm-size"]
                    },
                    'storageProfile': {
                        'imageReference': label.cloud_image.image_reference
                    },
                    'networkProfile': {
                        'networkInterfaces': [{
                            'id': nic['id'],
                            'properties': {
                                'primary': True,
                            }
                        }]
                    },
                },
            })
        return vm

    def getIpaddress(self, instance):
        # Copied from https://github.com/Azure/azure-sdk-for-python/issues/897
        ni_reference = (instance['properties']['networkProfile']
                        ['networkInterfaces'][0])
        ni_reference = ni_reference['id'].split('/')
        ni_group = ni_reference[4]
        ni_name = ni_reference[8]

        net_interface = self.azul.network_interfaces.get(
            ni_group, ni_name)
        ip_reference = (net_interface['properties']['ipConfigurations'][0]
                        ['properties']['publicIPAddress'])
        ip_reference = ip_reference['id'].split('/')
        ip_group = ip_reference[4]
        ip_name = ip_reference[8]

        public_ip = self.azul.public_ip_addresses.get(
            ip_group, ip_name)
        public_ip = public_ip['properties']['ipAddress']
        return public_ip

    def getv6Ipaddress(self, instance):
        # Copied from https://github.com/Azure/azure-sdk-for-python/issues/897
        ni_reference = (instance['properties']['networkProfile']
                        ['networkInterfaces'][0])
        ni_reference = ni_reference['id'].split('/')
        ni_group = ni_reference[4]
        ni_name = ni_reference[8]

        net_interface = self.azul.network_interfaces.get(
            ni_group, ni_name)
        return (net_interface['properties']['ipConfigurations'][1]
                ['properties']['privateIPAddress'])
