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

import json
import logging
import math
import os
import random
import string

import cachetools.func

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine
from . import azul


def quota_info_from_sku(sku):
    if not sku:
        return QuotaInformation(instances=1)

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


def generate_password():
    while True:
        chars = random.choices(string.ascii_lowercase +
                               string.ascii_uppercase +
                               string.digits,
                               k=64)
        if ((set(string.ascii_lowercase) & set(chars)) and
            (set(string.ascii_uppercase) & set(chars)) and
            (set(string.digits) & set(chars))):
            return(''.join(chars))


class AzureInstance(statemachine.Instance):
    def __init__(self, vm, nic=None, public_ipv4=None,
                 public_ipv6=None, sku=None):
        super().__init__()
        self.external_id = vm['name']
        self.metadata = vm.get('tags', {})
        self.private_ipv4 = None
        self.private_ipv6 = None
        self.public_ipv4 = None
        self.public_ipv6 = None
        self.sku = sku

        if nic:
            for ip_config_data in nic['properties']['ipConfigurations']:
                ip_config_prop = ip_config_data['properties']
                if ip_config_prop['privateIPAddressVersion'] == 'IPv4':
                    self.private_ipv4 = ip_config_prop['privateIPAddress']
                if ip_config_prop['privateIPAddressVersion'] == 'IPv6':
                    self.private_ipv6 = ip_config_prop['privateIPAddress']
        # public_ipv6

        if public_ipv4:
            self.public_ipv4 = public_ipv4['properties'].get('ipAddress')
        if public_ipv6:
            self.public_ipv6 = public_ipv6['properties'].get('ipAddress')

        self.interface_ip = (self.public_ipv4 or self.public_ipv6 or
                             self.private_ipv4 or self.private_ipv6)
        self.region = vm['location']
        self.az = ''

    def getQuotaInformation(self):
        return quota_info_from_sku(self.sku)


class AzureResource(statemachine.Resource):
    def __init__(self, metadata, type, name):
        super().__init__(metadata)
        self.type = type
        self.name = name


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
        self.disks = []
        self.public_ipv4 = None
        self.public_ipv6 = None

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
                self.public_ipv4 = self.adapter._deletePublicIPAddress(
                    self.external_id + '-pip-IPv4')
                self.public_ipv6 = self.adapter._deletePublicIPAddress(
                    self.external_id + '-pip-IPv6')
                self.state = self.PIP_DELETING

        if self.state == self.PIP_DELETING:
            self.public_ipv4 = self.adapter._refresh_delete(self.public_ipv4)
            self.public_ipv6 = self.adapter._refresh_delete(self.public_ipv6)
            if self.public_ipv4 is None and self.public_ipv6 is None:
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
    VM_RETRY = 'retrying vm creation'
    NIC_QUERY = 'querying nic'
    PIP_QUERY = 'querying pip'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries):
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.attempts = 0
        self.image_external_id = image_external_id
        self.image_reference = None
        self.metadata = metadata
        self.tags = label.tags.copy() or {}
        self.tags.update(metadata)
        self.hostname = hostname
        self.label = label
        self.public_ipv4 = None
        self.public_ipv6 = None
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

            # Find an appropriate image if filters were provided
            if self.label.cloud_image and self.label.cloud_image.image_filter:
                self.image_reference = self.adapter._getImageFromFilter(
                    self.label.cloud_image.image_filter)

            if self.label.pool.public_ipv4:
                self.public_ipv4 = self.adapter._createPublicIPAddress(
                    self.tags, self.hostname, self.ip_sku, 'IPv4',
                    self.ip_method)
            if self.label.pool.public_ipv6:
                self.public_ipv6 = self.adapter._createPublicIPAddress(
                    self.tags, self.hostname, self.ip_sku, 'IPv6',
                    self.ip_method)
            self.state = self.PIP_CREATING

        if self.state == self.PIP_CREATING:
            if self.public_ipv4:
                self.public_ipv4 = self.adapter._refresh(self.public_ipv4)
                if not self.adapter._succeeded(self.public_ipv4):
                    return
            if self.public_ipv6:
                self.public_ipv6 = self.adapter._refresh(self.public_ipv6)
                if not self.adapter._succeeded(self.public_ipv6):
                    return
            # At this point, every pip we have has succeeded (we may
            # have 0, 1, or 2).
            self.nic = self.adapter._createNetworkInterface(
                self.tags, self.hostname,
                self.label.pool.ipv4, self.label.pool.ipv6,
                self.public_ipv4, self.public_ipv6)
            self.state = self.NIC_CREATING

        if self.state == self.NIC_CREATING:
            self.nic = self.adapter._refresh(self.nic)
            if self.adapter._succeeded(self.nic):
                self.vm = self.adapter._createVirtualMachine(
                    self.label, self.image_external_id,
                    self.image_reference, self.tags, self.hostname,
                    self.nic)
                self.state = self.VM_CREATING
            else:
                return

        if self.state == self.VM_CREATING:
            self.vm = self.adapter._refresh(self.vm)
            if self.adapter._succeeded(self.vm):
                self.state = self.NIC_QUERY
            elif self.adapter._failed(self.vm):
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                self.vm = self.adapter._deleteVirtualMachine(
                    self.external_id)
                self.state = self.VM_RETRY
            else:
                return

        if self.state == self.VM_RETRY:
            self.vm = self.adapter._refresh_delete(self.vm)
            if self.vm is None:
                self.state = self.NIC_CREATING
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
            if self.public_ipv4:
                self.public_ipv4 = self.adapter._refresh(
                    self.public_ipv4, force=True)
                if 'ipAddress' not in self.public_ipv4['properties']:
                    all_found = False
            if self.public_ipv6:
                self.public_ipv6 = self.adapter._refresh(
                    self.public_ipv6, force=True)
                if 'ipAddress' not in self.public_ipv6['properties']:
                    all_found = False
            if all_found:
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            return AzureInstance(self.vm, self.nic,
                                 self.public_ipv4, self.public_ipv6)


class AzureAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.azure.AzureAdapter")

    def __init__(self, provider_config):
        self.provider = provider_config
        self.resource_group = self.provider.resource_group
        self.resource_group_location = self.provider.resource_group_location
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)
        with open(self.provider.auth_path) as f:
            self.azul = azul.AzureCloud(json.load(f))
        if provider_config.subnet_id:
            self.subnet_id = provider_config.subnet_id
        else:
            if isinstance(provider_config.network, str):
                net_info = {'network': provider_config.network}
            else:
                net_info = provider_config.network
            with self.rate_limiter:
                subnet = self.azul.subnets.get(
                    net_info.get('resource-group',
                                 self.provider.resource_group),
                    net_info['network'],
                    net_info.get('subnet', 'default'))
            self.subnet_id = subnet['id']
        self.skus = {}
        self._getSKUs()

    def getCreateStateMachine(self, hostname, label,
                              image_external_id, metadata, retries, log):
        return AzureCreateStateMachine(self, hostname, label,
                                       image_external_id, metadata, retries)

    def getDeleteStateMachine(self, external_id, log):
        return AzureDeleteStateMachine(self, external_id)

    def listResources(self):
        for vm in self._listVirtualMachines():
            yield AzureResource(vm.get('tags', {}), 'vm', vm['name'])
        for nic in self._listNetworkInterfaces():
            yield AzureResource(nic.get('tags', {}), 'nic', nic['name'])
        for pip in self._listPublicIPAddresses():
            yield AzureResource(pip.get('tags', {}), 'pip', pip['name'])
        for disk in self._listDisks():
            yield AzureResource(disk.get('tags', {}), 'disk', disk['name'])
        for image in self._listImages():
            yield AzureResource(image.get('tags', {}), 'image', image['name'])

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.name}")
        if resource.type == 'vm':
            crud = self.azul.virtual_machines
        elif resource.type == 'nic':
            crud = self.azul.network_interfaces
        elif resource.type == 'pip':
            crud = self.azul.public_ip_addresses
        elif resource.type == 'disk':
            crud = self.azul.disks
        elif resource.type == 'image':
            crud = self.azul.images
        with self.rate_limiter:
            crud.delete(self.resource_group, resource.name)

    def listInstances(self):
        for vm in self._listVirtualMachines():
            sku = self.skus.get((vm['properties']['hardwareProfile']['vmSize'],
                                 vm['location']))
            yield AzureInstance(vm, sku=sku)

    def getQuotaLimits(self):
        with self.rate_limiter:
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
        sku = self.skus.get((label.hardware_profile["vm-size"],
                             self.provider.location))
        return quota_info_from_sku(sku)

    def uploadImage(self, provider_image, image_name, filename,
                    image_format, metadata, md5, sha256):
        self.log.debug(f"Uploading image {image_name}")
        file_sz = os.path.getsize(filename)
        disk_info = {
            "location": self.provider.location,
            "tags": metadata,
            "properties": {
                "creationData": {
                    "createOption": "Upload",
                    "uploadSizeBytes": file_sz
                }
            }
        }
        self.log.debug("Creating disk for image upload")
        with self.rate_limiter:
            r = self.azul.disks.create(self.resource_group,
                                       image_name, disk_info)
        r = self.azul.wait_for_async_operation(r)

        if r['status'] != 'Succeeded':
            raise Exception("Unable to create disk for image upload")
        disk_id = r['properties']['output']['id']

        disk_grant = {
            "access": "Write",
            "durationInSeconds": 24 * 60 * 60,
        }
        self.log.debug("Enabling write access to disk for image upload")
        with self.rate_limiter:
            r = self.azul.disks.post(self.resource_group, image_name,
                                     'beginGetAccess', disk_grant)
        r = self.azul.wait_for_async_operation(r)

        if r['status'] != 'Succeeded':
            raise Exception("Unable to begin write access on disk")
        sas = r['properties']['output']['accessSAS']

        self.log.debug("Uploading image")
        with open(filename, "rb") as fobj:
            self.azul.upload_page_blob_to_sas_url(sas, fobj)

        disk_grant = {}
        self.log.debug("Disabling write access to disk for image upload")
        with self.rate_limiter:
            r = self.azul.disks.post(self.resource_group, image_name,
                                     'endGetAccess', disk_grant)
        r = self.azul.wait_for_async_operation(r)

        if r['status'] != 'Succeeded':
            raise Exception("Unable to end write access on disk")

        image_info = {
            "location": self.provider.location,
            "tags": metadata,
            "properties": {
                "hyperVGeneration": "V2",
                "storageProfile": {
                    "osDisk": {
                        "osType": "Linux",
                        "managedDisk": {
                            "id": disk_id,
                        },
                        "osState": "Generalized"
                    },
                    "zoneResilient": True
                }
            }
        }
        self.log.debug("Creating image from disk")
        with self.rate_limiter:
            r = self.azul.images.create(self.resource_group, image_name,
                                        image_info)
        r = self.azul.wait_for_async_operation(r)

        if r['status'] != 'Succeeded':
            raise Exception("Unable to create image from disk")

        self.log.debug("Deleting disk for image upload")
        with self.rate_limiter:
            r = self.azul.disks.delete(self.resource_group, image_name)
        r = self.azul.wait_for_async_operation(r)

        if r['status'] != 'Succeeded':
            raise Exception("Unable to delete disk for image upload")

        self.log.info(f"Uploaded image {image_name}")
        return image_name

    def deleteImage(self, external_id):
        self.log.debug(f"Deleting image {external_id}")
        with self.rate_limiter:
            r = self.azul.images.delete(self.resource_group, external_id)
        r = self.azul.wait_for_async_operation(r)

        self.log.info(f"Deleted image {external_id}")
        if r['status'] != 'Succeeded':
            raise Exception("Unable to delete image")

    # Local implementation below

    def _metadataMatches(self, obj, metadata):
        if 'tags' not in obj:
            return None
        for k, v in metadata.items():
            if obj['tags'].get(k) != v:
                return None
        return (obj['tags'].get('nodepool_node_id'),
                obj['tags'].get('nodepool_upload_id'))

    @staticmethod
    def _succeeded(obj):
        return obj['properties']['provisioningState'] == 'Succeeded'

    @staticmethod
    def _failed(obj):
        return obj['properties']['provisioningState'] == 'Failed'

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

    def _getSKUs(self):
        self.log.debug("Querying compute SKUs")
        with self.rate_limiter:
            for sku in self.azul.compute_skus.list():
                for location in sku['locations']:
                    key = (sku['name'], location)
                    self.skus[key] = sku
        self.log.debug("Done querying compute SKUs")

    @cachetools.func.ttl_cache(maxsize=0, ttl=(24 * 60 * 60))
    def _getImage(self, image_name):
        with self.rate_limiter:
            return self.azul.images.get(self.resource_group, image_name)

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listPublicIPAddresses(self):
        with self.rate_limiter:
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
        name = "%s-pip-%s" % (hostname, version)
        with self.rate_limiter:
            self.log.debug(f"Creating external IP address {name}")
            return self.azul.public_ip_addresses.create(
                self.resource_group,
                name,
                v4_params_create,
            )

    def _deletePublicIPAddress(self, name):
        for pip in self._listPublicIPAddresses():
            if pip['name'] == name:
                break
        else:
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting external IP address {name}")
            self.azul.public_ip_addresses.delete(self.resource_group, name)
        return pip

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listNetworkInterfaces(self):
        with self.rate_limiter:
            return self.azul.network_interfaces.list(self.resource_group)

    def _createNetworkInterface(self, tags, hostname, ipv4, ipv6,
                                public_ipv4, public_ipv6):

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
                                             public_ipv4))
        if ipv6:
            ip_configs.append(make_ip_config('nodepool-v6-ip-config',
                                             'IPv6', self.subnet_id,
                                             public_ipv6))

        nic_data = {
            'location': self.provider.location,
            'tags': tags,
            'properties': {
                'ipConfigurations': ip_configs
            }
        }
        name = "%s-nic" % hostname
        with self.rate_limiter:
            self.log.debug(f"Creating NIC {name}")
            return self.azul.network_interfaces.create(
                self.resource_group,
                name,
                nic_data
            )

    def _deleteNetworkInterface(self, name):
        for nic in self._listNetworkInterfaces():
            if nic['name'] == name:
                break
        else:
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting NIC {name}")
            self.azul.network_interfaces.delete(self.resource_group, name)
        return nic

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listVirtualMachines(self):
        with self.rate_limiter:
            return self.azul.virtual_machines.list(self.resource_group)

    def _createVirtualMachine(self, label, image_external_id,
                              image_reference, tags, hostname, nic):
        if image_external_id:
            # This is a diskimage
            image = label.diskimage
            remote_image = self._getImage(image_external_id)
            image_reference = {'id': remote_image['id']}
        elif image_reference:
            # This is a cloud image with aser supplied image-filter;
            # we already found the reference.
            image = label.cloud_image
        else:
            # This is a cloud image with a user-supplied reference or
            # id.
            image = label.cloud_image
            if label.cloud_image.image_reference:
                image_reference = label.cloud_image.image_reference
            else:
                image_reference = {'id': label.cloud_image.image_id}
        os_profile = {'computerName': hostname}
        if image.key:
            linux_config = {
                'ssh': {
                    'publicKeys': [{
                        'path': "/home/%s/.ssh/authorized_keys" % (
                            image.username),
                        'keyData': image.key,
                    }]
                },
                "disablePasswordAuthentication": True,
            }
            os_profile['linuxConfiguration'] = linux_config
        if image.username:
            os_profile['adminUsername'] = image.username
        if image.password:
            os_profile['adminPassword'] = image.password
        elif image.generate_password:
            os_profile['adminPassword'] = generate_password()
        if label.custom_data:
            os_profile['customData'] = label.custom_data

        spec = {
            'location': self.provider.location,
            'tags': tags,
            'properties': {
                'osProfile': os_profile,
                'hardwareProfile': {
                    'vmSize': label.hardware_profile["vm-size"]
                },
                'storageProfile': {
                    'imageReference': image_reference,
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
        }
        if label.user_data:
            spec['properties']['userData'] = label.user_data
        with self.rate_limiter:
            self.log.debug(f"Creating VM {hostname}")
            return self.azul.virtual_machines.create(
                self.resource_group, hostname, spec)

    def _deleteVirtualMachine(self, name):
        for vm in self._listVirtualMachines():
            if vm['name'] == name:
                break
        else:
            self.log.warning(f"VM not found when deleting {name}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting VM {name}")
            self.azul.virtual_machines.delete(self.resource_group, name)
        return vm

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listDisks(self):
        with self.rate_limiter:
            return self.azul.disks.list(self.resource_group)

    def _deleteDisk(self, name):
        # Because the disk listing is unreliable (there is up to a 30
        # minute delay between disks being created and appearing in
        # the listing) we can't use the listing to efficiently
        # determine if the deletion is complete.  We could fall back
        # on the asynchronous operation record, but since disks are
        # the last thing we delete anyway, let's just fire and forget.
        with self.rate_limiter:
            self.azul.disks.delete(self.resource_group, name)
        return None

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listImages(self):
        with self.rate_limiter:
            return self.azul.images.list(self.resource_group)

    def _getImageFromFilter(self, image_filter):
        images = self._listImages()
        images = [i for i in images
                  if i['properties']['provisioningState'] == 'Succeeded']
        if 'name' in image_filter:
            images = [i for i in images
                      if i['name'] == image_filter['name']]
        if 'location' in image_filter:
            images = [i for i in images
                      if i['location'] == image_filter['location']]
        if 'tags' in image_filter:
            for k, v in image_filter['tags'].items():
                images = [i for i in images if i['tags'].get(k) == v]
        images = sorted(images, key=lambda i: i['name'])
        if not images:
            raise Exception("Unable to find image matching filter: %s",
                            image_filter)
        image = images[-1]
        self.log.debug("Found image matching filter: %s", image)
        return {'id': image['id']}
