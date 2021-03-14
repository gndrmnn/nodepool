# Copyright 2021 Acme Gating, LLC
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

import math
import logging
import json

import cachetools.func

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine
from . import azul

class AzureInstance(statemachine.Instance):
    def __init__(self, vm, nic=None, pip4=None, pip6=None):
        self.external_id = vm['name']
        self.metadata = vm['tags'] or {}
        self.private_ipv4 = None
        self.private_ipv6 = None
        self.public_ipv4 = None
        self.public_ipv6 = None

        if nic:
            for ip_config_data in nic['properties']['ipConfigurations']:
                ip_config_prop = ip_config_data['properties']
                if ip_config_prop['privateIPAddressVersion'] == 'IPv4':
                    self.private_ipv4 = ip_config_prop['privateIPAddress']
                if ip_config_prop['privateIPAddressVersion'] == 'IPv6':
                    self.private_ipv6 = ip_config_prop['privateIPAddress']
        # public_ipv6

        if pip4:
            self.public_ipv4 = pip4['properties'].get('ipAddress')
        if pip6:
            self.public_ipv6 = pip6['properties'].get('ipAddress')

        self.interface_ip = (self.public_ipv4 or self.public_ipv6 or
                             self.private_ipv4 or self.private_ipv6)
        self.region = vm['location']
        self.az = ''


class AzureDeleteStateMachine(statemachine.StateMachine):
    VM_DELETING = 'deleting vm'
    NIC_DELETING = 'deleting nic'
    PIP_DELETING = 'deleting pip'
    DISK_DELETING = 'deleting disk'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id):
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id
        self.disk_names = []

    def advance(self):
        if self.state == self.START:
            self.vm = self.adapter._deleteVirtualMachine(
                self.external_id)
            if self.vm:
                self.disk_names.append(
                    self.vm['properties']['storageProfile']['osDisk']['name'])
            self.state = self.VM_DELETING

        if self.state == self.VM_DELETING:
            self.vm = self.adapter._refresh_delete(self.vm)
            if self.vm is None:
                self.nic = self.adapter._deleteNetworkInterface(
                    self.external_id + '-nic')
                self.state = self.NIC_DELETING

        if self.state == self.NIC_DELETING:
            self.nic = self.adapter._refresh_delete(self.nic)
            if self.nic is None:
                self.pip4 = self.adapter._deletePublicIPAddress(
                    self.external_id + '-pip-IPv4')
                self.pip6 = self.adapter._deletePublicIPAddress(
                    self.external_id + '-pip-IPv6')
                self.state = self.PIP_DELETING

        if self.state == self.PIP_DELETING:
            self.pip4 = self.adapter._refresh_delete(self.pip4)
            self.pip6 = self.adapter._refresh_delete(self.pip6)
            if self.pip4 is None and self.pip6 is None:
                self.disks = []
                for name in self.disk_names:
                    disk = self.adapter._deleteDisk(name)
                    self.disks.append(disk)
                self.state = self.DISK_DELETING

        if self.state == self.DISK_DELETING:
            all_deleted = True
            for disk in self.disks:
                disk = self.adapter._refresh_delete(disk)
                if disk:
                    all_deleted = False
            if all_deleted:
                self.state = self.COMPLETE
                self.complete = True


class AzureCreateStateMachine(statemachine.StateMachine):
    PIP_CREATING = 'creating pip'
    NIC_CREATING = 'creating nic'
    VM_CREATING = 'creating vm'
    NIC_QUERY = 'querying nic'
    PIP_QUERY = 'querying pip'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, metadata, retries):
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.metadata = metadata
        self.tags = label.tags.copy() or {}
        self.tags.update(metadata)
        self.hostname = hostname
        self.label = label
        self.pip4 = None
        self.pip6 = None
        self.nic = None
        self.vm = None
        # There are two parameters for IP addresses: SKU and
        # allocation method.  SKU is "basic" or "standard".
        # Allocation method is "static" or "dynamic".  Between IPv4
        # and v6, SKUs cannot be mixed (the same sku must be used for
        # both protocols).  The standard SKU only supports static
        # allocation.  Static is cheaper than dynamic, but basic is
        # cheaper than standard.  Also, dynamic is faster than static.
        # Therefore, if IPv6 is used at all, standard+static for
        # everything; otherwise basic+dynamic in an IPv4-only
        # situation.
        if label.pool.ipv6:
            self.ip_sku = 'Standard'
            self.ip_method = 'static'
        else:
            self.ip_sku = 'Basic'
            self.ip_method = 'dynamic'

    def advance(self):
        if self.state == self.START:
            self.external_id = self.hostname
            if self.label.pool.public_ipv4:
                self.pip4 = self.adapter._createPublicIPAddress(
                    self.tags, self.hostname, self.ip_sku, 'IPv4',
                    self.ip_method)
            if self.label.pool.public_ipv6:
                self.pip6 = self.adapter._createPublicIPAddress(
                    self.tags, self.hostname, self.ip_sku, 'IPv6',
                    self.ip_method)
            self.state = self.PIP_CREATING

        if self.state == self.PIP_CREATING:
            if self.pip4:
                self.pip4 = self.adapter._refresh(self.pip4)
                if not self.adapter._succeeded(self.pip4):
                    return
            if self.pip6:
                self.pip6 = self.adapter._refresh(self.pip6)
                if not self.adapter._succeeded(self.pip6):
                    return
            # At this point, every pip we have has succeeded (we may
            # have 0, 1, or 2).
            self.nic = self.adapter._createNetworkInterface(
                self.tags, self.hostname,
                self.label.pool.ipv4, self.label.pool.ipv6,
                self.pip4, self.pip6)
            self.state = self.NIC_CREATING

        if self.state == self.NIC_CREATING:
            self.nic = self.adapter._refresh(self.nic)
            if self.adapter._succeeded(self.nic):
                self.vm = self.adapter._createVirtualMachine(
                    self.label, self.tags, self.hostname, self.nic)
                self.state = self.VM_CREATING
            else:
                return

        if self.state == self.VM_CREATING:
            self.vm = self.adapter._refresh(self.vm)
            # if 404:
            #   increment retries
            #   state = self.NIC_CREATING
            # if error:
            #   if retries too big: raise error
            #   delete vm
            if self.adapter._succeeded(self.vm):
                self.state = self.NIC_QUERY
            else:
                return

        if self.state == self.NIC_QUERY:
            self.nic = self.adapter._refresh(self.nic, force=True)
            all_found = True
            for ip_config_data in self.nic['properties']['ipConfigurations']:
                ip_config_prop = ip_config_data['properties']
                if 'privateIPAddress' not in ip_config_prop:
                    all_found = False
            if all_found:
                self.state = self.PIP_QUERY

        if self.state == self.PIP_QUERY:
            all_found = True
            if self.pip4:
                self.pip4 = self.adapter._refresh(self.pip4, force=True)
                if 'ipAddress' not in self.pip4['properties']:
                    all_found = False
            if self.pip6:
                self.pip6 = self.adapter._refresh(self.pip6, force=True)
                if 'ipAddress' not in self.pip6['properties']:
                    all_found = False
            if all_found:
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            return AzureInstance(self.vm, self.nic, self.pip4, self.pip6)

class AzureAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.azure.AzureAdapter")

    def __init__(self, provider_config):
        self.provider = provider_config
        self.resource_group = self.provider.resource_group
        self.resource_group_location = self.provider.resource_group_location
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate_limit)
        with open(self.provider.auth_path) as f:
            self.azul = azul.AzureCloud(json.load(f))
        if provider_config.subnet_id:
            self.subnet_id = provider_config.subnet_id
        else:
            if isinstance(provider_config.network, str):
                net_info = {'network': provider_config.network}
            else:
                net_info = provider_config.network
            subnet = self.azul.subnets.get(
                net_info.get('resource-group', self.provider.resource_group),
                net_info['network'],
                net_info.get('subnet', 'default'))
            self.subnet_id = subnet['id']

    def getCreateStateMachine(self, hostname, label, metadata, retries):
        return AzureCreateStateMachine(self, hostname, label, metadata, retries)

    def getDeleteStateMachine(self, external_id):
        return AzureDeleteStateMachine(self, external_id)

    def cleanupLeakedResources(self, known_nodes, metadata):
        for vm in self._listVirtualMachines():
            node_id = self._metadataMatches(vm, metadata)
            if (node_id and node_id not in known_nodes):
                self.log.info(f"Deleting leaked vm: {vm['name']}")
                self.azul.virtual_machines.delete(self.resource_group, vm['name'])
        for nic in self._listNetworkInterfaces():
            node_id = self._metadataMatches(nic, metadata)
            if (node_id and node_id not in known_nodes):
                self.log.info(f"Deleting leaked nic: {nic['name']}")
                self.azul.network_interfaces.delete(self.resource_group, nic['name'])
        for pip in self._listPublicIPAddresses():
            node_id = self._metadataMatches(pip, metadata)
            if (node_id and node_id not in known_nodes):
                self.log.info(f"Deleting leaked pip: {pip['name']}")
                self.azul.public_ip_addresses.delete(self.resource_group, pip['name'])
        for disk in self._listDisks():
            node_id = self._metadataMatches(disk, metadata)
            if (node_id and node_id not in known_nodes):
                self.log.info(f"Deleting leaked disk: {disk['name']}")
                self.azul.disks.delete(self.resource_group, disk['name'])

    def listInstances(self):
        for vm in self._listVirtualMachines():
            yield AzureInstance(vm)

    def getQuotaLimits(self):
        r = self.azul.compute_usages.list(self.provider.location)
        cores = instances = math.inf
        for item in r:
            if item['name']['value'] == 'cores':
                cores = item['limit']
            elif item['name']['value'] == 'virtualMachines':
                instances = item['limit']
        return QuotaInformation(cores=cores,
                                instances=instances,
                                default=math.inf)

    def getQuotaForLabel(self, label):
        for sku in self._listSKUs():
            if (sku['name'] == label.hardware_profile["vm-size"] and
                self.provider.location in sku['locations']):
                cores = None
                ram = None
                for cap in sku['capabilities']:
                    if cap['name'] == 'vCPUs':
                        cores = int(cap['value'])
                    if cap['name'] == 'MemoryGB':
                        ram = int(float(cap['value']) * 1024)
                return QuotaInformation(
                    cores=cores,
                    ram=ram,
                    instances=1)
        return QuotaInformation(instances=1)

    # Local implementation below

    def _metadataMatches(self, obj, metadata):
        if not 'tags' in obj:
            return None
        for k, v in metadata.items():
            if obj['tags'].get(k) != v:
                return None
        return obj['tags']['nodepool_node_id']

    @staticmethod
    def _succeeded(obj):
        return obj['properties']['provisioningState'] == 'Succeeded'

    def _refresh(self, obj, force=False):
        if self._succeeded(obj) and not force:
            return obj

        if obj['type'] == 'Microsoft.Network/publicIPAddresses':
            l = self._listPublicIPAddresses()
        if obj['type'] == 'Microsoft.Network/networkInterfaces':
            l = self._listNetworkInterfaces()
        if obj['type'] == 'Microsoft.Compute/virtualMachines':
            l = self._listVirtualMachines()

        for new_obj in l:
            if new_obj['id'] == obj['id']:
                return new_obj
        return obj

    def _refresh_delete(self, obj):
        if obj is None:
            return obj

        if obj['type'] == 'Microsoft.Network/publicIPAddresses':
            l = self._listPublicIPAddresses()
        if obj['type'] == 'Microsoft.Network/networkInterfaces':
            l = self._listNetworkInterfaces()
        if obj['type'] == 'Microsoft.Compute/virtualMachines':
            l = self._listVirtualMachines()

        for new_obj in l:
            if new_obj['id'] == obj['id']:
                return new_obj
        return None

    @cachetools.func.lru_cache(maxsize=1)
    def _listSKUs(self):
        return self.azul.compute_skus.list()

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listPublicIPAddresses(self):
        return self.azul.public_ip_addresses.list(self.resource_group)

    def _createPublicIPAddress(self, tags, hostname, sku, version,
                               allocation_method):
        v4_params_create = {
            'location': self.provider.location,
            'tags': tags,
            'sku': {
                'name': sku,
            },
            'properties': {
                'publicIpAddressVersion': version,
                'publicIpAllocationMethod': allocation_method,
            },
        }
        return self.azul.public_ip_addresses.create(
            self.resource_group,
            "%s-pip-%s" % (hostname, version),
            v4_params_create,
        )

    def _deletePublicIPAddress(self, name):
        for pip in self._listPublicIPAddresses():
            if pip['name'] == name:
                break
        else:
            return None
        self.azul.public_ip_addresses.delete(self.resource_group, name)
        return pip

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listNetworkInterfaces(self):
        return self.azul.network_interfaces.list(self.resource_group)

    def _createNetworkInterface(self, tags, hostname, ipv4, ipv6, pip4, pip6):

        def make_ip_config(name, version, subnet_id, pip):
            ip_config = {
                'name': name,
                'properties': {
                    'privateIpAddressVersion': version,
                    'subnet': {
                        'id': subnet_id
                    },
                }
            }
            if pip:
                ip_config['properties']['publicIpAddress'] = {
                    'id': pip['id']
                }
            return ip_config

        ip_configs = []

        if ipv4:
            ip_configs.append(make_ip_config('nodepool-v4-ip-config',
                                             'IPv4', self.subnet_id,
                                             pip4))
        if ipv6:
            ip_configs.append(make_ip_config('nodepool-v6-ip-config',
                                             'IPv6', self.subnet_id,
                                             pip6))

        nic_data = {
            'location': self.provider.location,
            'tags': tags,
            'properties': {
                'ipConfigurations': ip_configs
            }
        }

        return self.azul.network_interfaces.create(
            self.resource_group,
            "%s-nic" % hostname,
            nic_data
        )

    def _deleteNetworkInterface(self, name):
        for nic in self._listNetworkInterfaces():
            if nic['name'] == name:
                break
        else:
            return None
        self.azul.network_interfaces.delete(self.resource_group, name)
        return nic

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listVirtualMachines(self):
        return self.azul.virtual_machines.list(self.resource_group)

    def _createVirtualMachine(self, label, tags, hostname, nic):
        return self.azul.virtual_machines.create(
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

    def _deleteVirtualMachine(self, name):
        for vm in self._listVirtualMachines():
            if vm['name'] == name:
                break
        else:
            return None
        self.azul.virtual_machines.delete(self.resource_group, name)
        return vm

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listDisks(self):
        return self.azul.disks.list(self.resource_group)

    def _deleteDisk(self, name):
        # Because the disk listing is unreliable (there is up to a 30
        # minute delay between disks being created and appearing in
        # the listing) we can't use the listing to efficiently
        # determine if the deletion is complete.  We could fall back
        # on the asynchronous operation record, but since disks are
        # the last thing we delete anyway, let's just fire and forget.
        self.azul.disks.delete(self.resource_group, name)
        return None
