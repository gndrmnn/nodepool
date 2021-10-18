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

import copy
import json
import time
import os
import re
import tempfile
import urllib
import uuid

import fixtures

import responses
import requests


class CRUDManager:
    name = ''

    def __init__(self, cloud):
        self.cloud = cloud
        self.items = []
        self.requests = []

    def list(self, request):
        resp = {'value': self.items}
        return (200, {}, json.dumps(resp))

    def get(self, request):
        url = urllib.parse.urlparse(request.path_url)
        for item in self.items:
            if item['id'] == url.path:
                return (200, {}, json.dumps(item))
        return (404, {}, json.dumps({
            'error': {
                'message': 'Not Found',
                'code': 'NotFound',
            }}))


class ResourceGroupsCRUD(CRUDManager):
    name = "resourcegroups"

    def put(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = "Microsoft.Resources/resourceGroups"
        data['provisioningState'] = 'Succeeded'

        self.items.append(data)
        return (200, {}, json.dumps(data))


class PublicIPAddressesCRUD(CRUDManager):
    name = "Microsoft.Network/publicIPAddresses"

    def put(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = self.name
        data['properties'] = {
            "provisioningState": "Updating",
            "resourceGuid": str(uuid.uuid4()),
            "publicIPAddressVersion": "IPv4",
            "publicIPAllocationMethod": "Dynamic",
            "idleTimeoutInMinutes": 4,
            "ipTags": [],
        }
        self.items.append(data)
        ret = json.dumps(data)
        # Finish provisioning after return
        data['properties']['ipAddress'] = "fake"
        data['properties']['provisioningState'] = "Succeeded"
        return (200, {}, ret)


class NetworkInterfacesCRUD(CRUDManager):
    name = "Microsoft.Network/networkInterfaces"

    def put(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = self.name
        ipconfig = data['properties']['ipConfigurations'][0]
        data['properties'] = {
            "provisioningState": "Succeeded",
            "resourceGuid": str(uuid.uuid4()),
            "ipConfigurations": [
                {
                    "name": ipconfig['name'],
                    "id": os.path.join(data['id'], ipconfig['name']),
                    "type": ("Microsoft.Network/networkInterfaces/"
                             "ipConfigurations"),
                    "properties": {
                        "provisioningState": "Succeeded",
                        "privateIPAddress": "10.0.0.4",
                        "privateIPAllocationMethod": "Dynamic",
                        "publicIPAddress": (ipconfig['properties']
                                            ['publicIpAddress']),
                        "subnet": ipconfig['properties']['subnet'],
                        "primary": True,
                        "privateIPAddressVersion": "IPv4",
                    },
                }
            ],
            "enableAcceleratedNetworking": False,
            "enableIPForwarding": False,
            "hostedWorkloads": [],
            "tapConfigurations": [],
            "nicType": "Standard"
        }
        self.items.append(data)
        return (200, {}, json.dumps(data))


class VirtualMachinesCRUD(CRUDManager):
    name = "Microsoft.Compute/virtualMachines"

    def put(self, request):
        data = json.loads(request.body)
        self.requests.append(copy.deepcopy(data))
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = self.name
        data['properties'] = {
            "vmId": str(uuid.uuid4()),
            "hardwareProfile": data['properties']['hardwareProfile'],
            "storageProfile": {
                "imageReference": (data['properties']['storageProfile']
                                   ['imageReference']),
                "osDisk": {
                    "osType": "Linux",
                    "createOption": "FromImage",
                    "caching": "ReadWrite",
                    "managedDisk": {
                        "storageAccountType": "Premium_LRS"
                    },
                    "diskSizeGB": 30
                },
                "dataDisks": []
            },
            "osProfile": data['properties']['osProfile'],
            "networkProfile": data['properties']['networkProfile'],
            "provisioningState": "Creating"
        }
        self.items.append(data)
        disk_data = data.copy()
        disk_data['name'] = 'bionic-azure-' + str(uuid.uuid4())
        disk_data['type'] = "Microsoft.Compute/disks"
        disk_data['id'] = '/'.join(url.path.split('/')[:5] +
                                   [disk_data['type'], disk_data['name']])
        disk_data['properties'] = {"provisioningState": "Succeeded"}
        self.cloud.crud["Microsoft.Compute/disks"].items.append(disk_data)

        ret = json.dumps(data)
        # Finish provisioning after return
        data['properties']['provisioningState'] = "Succeeded"
        return (200, {}, ret)


class DisksCRUD(CRUDManager):
    name = "Microsoft.Compute/disks"

    def put(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = self.name
        data['properties'] = {
            "provisioningState": "Succeeded",
        }
        self.items.append(data)
        async_url = 'https://management.azure.com/async' + request.path_url
        headers = {'Azure-AsyncOperation': async_url,
                   'Retry-After': '0'}
        return (200, headers, json.dumps(data))

    def post(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-2]
        action = url.path.split('/')[-1]
        if action == 'beginGetAccess':
            async_url = 'https://management.azure.com/async' + request.path_url
            async_url = async_url.replace('/beginGetAccess', '')
            for item in self.items:
                if item['name'] == name:
                    item['accessSAS'] = (
                        'https://management.azure.com/sas/' + name)
        if action == 'endGetAccess':
            async_url = 'https://management.azure.com/async' + request.path_url
            async_url = async_url.replace('/endGetAccess', '')
        headers = {'Azure-AsyncOperation': async_url,
                   'Retry-After': '0'}
        return (200, headers, json.dumps(data))

    def delete(self, request):
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        async_url = 'https://management.azure.com/async' + request.path_url
        for item in self.items:
            if item['name'] == name:
                self.items.remove(item)
                headers = {'Azure-AsyncOperation': async_url,
                           'Retry-After': '0'}
                return (200, headers, '')
        return (404, {}, json.dumps({
            'error': {
                'message': 'Not Found',
                'code': 'NotFound',
            }}))


class ImagesCRUD(CRUDManager):
    name = "Microsoft.Compute/images"

    def put(self, request):
        data = json.loads(request.body)
        url = urllib.parse.urlparse(request.path_url)
        name = url.path.split('/')[-1]
        data['id'] = url.path
        data['name'] = name
        data['type'] = self.name
        data['properties'] = {
            "provisioningState": "Succeeded",
        }
        self.items.append(data)
        async_url = 'https://management.azure.com/async' + request.path_url
        headers = {'Azure-AsyncOperation': async_url,
                   'Retry-After': '0'}
        return (200, headers, json.dumps(data))


class FakeAzureFixture(fixtures.Fixture):
    tenant_id = str(uuid.uuid4())
    subscription_id = str(uuid.uuid4())
    access_token = "secret_token"
    auth = {
        "clientId": str(uuid.uuid4()),
        "clientSecret": str(uuid.uuid4()),
        "subscriptionId": subscription_id,
        "tenantId": tenant_id,
        "activeDirectoryEndpointUrl": "https://login.microsoftonline.com",
        "resourceManagerEndpointUrl": "https://management.azure.com/",
        "activeDirectoryGraphResourceId": "https://graph.windows.net/",
        "sqlManagementEndpointUrl":
            "https://management.core.windows.net:8443/",
        "galleryEndpointUrl": "https://gallery.azure.com/",
        "managementEndpointUrl": "https://management.core.windows.net/",
    }

    def _setUp(self):
        self.crud = {}
        self.responses = responses.RequestsMock()
        self.responses.start()

        self.auth_file = tempfile.NamedTemporaryFile('w', delete=False)
        with self.auth_file as f:
            json.dump(self.auth, f)

        self.responses.add(
            responses.POST,
            f'https://login.microsoftonline.com/{self.tenant_id}/oauth2/token',
            json={
                'access_token': 'secret_token',
                'expires_on': time.time() + 600,
            })

        self.responses.add_callback(
            responses.GET,
            ('https://management.azure.com/subscriptions/'
             f'{self.subscription_id}/providers/Microsoft.Compute/skus/'
             '?api-version=2019-04-01'),
            callback=self._get_compute_skus,
            content_type='application/json')

        self.responses.add_callback(
            responses.GET,
            ('https://management.azure.com/subscriptions/'
             f'{self.subscription_id}/providers/Microsoft.Compute/locations/'
             'centralus/usages?api-version=2020-12-01'),
            callback=self._get_compute_usages,
            content_type='application/json')

        async_re = re.compile('https://management.azure.com/async/(.*)')
        self.responses.add_callback(
            responses.GET, async_re,
            callback=self._get_async,
            content_type='application/json')

        sas_re = re.compile('https://management.azure.com/sas/(.*)')
        self.responses.add_callback(
            responses.PUT, sas_re,
            callback=self._put_sas,
            content_type='application/json')

        self._setup_crud(ResourceGroupsCRUD, '2020-06-01',
                         resource_grouped=False)

        self._setup_crud(VirtualMachinesCRUD, '2020-12-01')
        self._setup_crud(NetworkInterfacesCRUD, '2020-07-01')
        self._setup_crud(PublicIPAddressesCRUD, '2020-07-01')
        self._setup_crud(DisksCRUD, '2020-06-30')
        self._setup_crud(ImagesCRUD, '2020-12-01')

        self.addCleanup(self.responses.stop)
        self.addCleanup(self.responses.reset)

    def _setup_crud(self, manager, api_version, resource_grouped=True):
        self.crud[manager.name] = manager(self)

        if resource_grouped:
            rg = 'resourceGroups/(.*?)/providers/'
        else:
            rg = ''

        list_re = re.compile(
            'https://management.azure.com/subscriptions/'
            + f'{self.subscription_id}/'
            + rg + f'{manager.name}/?\\?api-version={api_version}')
        crud_re = re.compile(
            'https://management.azure.com/subscriptions/'
            + f'{self.subscription_id}/'
            + rg + f'{manager.name}/(.+?)\\?api-version={api_version}')
        self.responses.add_callback(
            responses.GET, list_re, callback=self.crud[manager.name].list,
            content_type='application/json')
        self.responses.add_callback(
            responses.GET, crud_re, callback=self.crud[manager.name].get,
            content_type='application/json')
        self.responses.add_callback(
            responses.PUT, crud_re, callback=self.crud[manager.name].put,
            content_type='application/json')
        if hasattr(self.crud[manager.name], 'post'):
            self.responses.add_callback(
                responses.POST, crud_re, callback=self.crud[manager.name].post,
                content_type='application/json')
        if hasattr(self.crud[manager.name], 'delete'):
            self.responses.add_callback(
                responses.DELETE, crud_re,
                callback=self.crud[manager.name].delete,
                content_type='application/json')

    def _extract_resource_group(self, path):
        url = re.compile('/subscriptions/(.*?)/resourceGroups/(.*?)/')
        m = url.match(path)
        return m.group(2)

    def _get_compute_skus(self, request):
        data = {
            'value': [
                {'capabilities': [
                    {'name': 'MaxResourceVolumeMB', 'value': '4096'},
                    {'name': 'OSVhdSizeMB', 'value': '1047552'},
                    {'name': 'vCPUs', 'value': '1'},
                    {'name': 'HyperVGenerations', 'value': 'V1,V2'},
                    {'name': 'MemoryGB', 'value': '0.5'},
                    {'name': 'MaxDataDiskCount', 'value': '2'},
                    {'name': 'LowPriorityCapable', 'value': 'False'},
                    {'name': 'PremiumIO', 'value': 'True'},
                    {'name': 'VMDeploymentTypes', 'value': 'IaaS'},
                    {'name': 'CombinedTempDiskAndCachedIOPS', 'value': '200'},
                    {'name': 'CombinedTempDiskAndCachedReadBytesPerSecond',
                     'value': '10485760'},
                    {'name': 'CombinedTempDiskAndCachedWriteBytesPerSecond',
                     'value': '10485760'},
                    {'name': 'UncachedDiskIOPS', 'value': '160'},
                    {'name': 'UncachedDiskBytesPerSecond',
                     'value': '10485760'},
                    {'name': 'EphemeralOSDiskSupported', 'value': 'True'},
                    {'name': 'EncryptionAtHostSupported', 'value': 'True'},
                    {'name': 'AcceleratedNetworkingEnabled', 'value': 'False'},
                    {'name': 'RdmaEnabled', 'value': 'False'},
                    {'name': 'MaxNetworkInterfaces', 'value': '2'}],
                 'family': 'standardBSFamily',
                 'locationInfo': [
                     {'location': 'centralus',
                      'zoneDetails': [
                          {'Name': ['3', '2', '1'],
                           'capabilities': [
                               {'name': 'UltraSSDAvailable',
                                'value': 'True'}]}],
                      'zones': ['3', '1', '2']}],
                 'locations': ['centralus'],
                 'name': 'Standard_B1ls',
                 'resourceType': 'virtualMachines',
                 'restrictions': [],
                 'size': 'B1ls',
                 'tier': 'Standard'}
            ]
        }
        return (200, {}, json.dumps(data))

    def _get_compute_usages(self, request):
        mgr = self.crud["Microsoft.Compute/virtualMachines"]
        data = {
            'value': [
                {
                    "limit": 4,
                    "unit": "Count",
                    "currentValue": len(mgr.items),
                    "name": {
                        "value": "cores",
                        "localizedValue": "Total Regional vCPUs"
                    }
                }, {
                    "limit": 25000,
                    "unit": "Count",
                    "currentValue": len(mgr.items),
                    "name": {
                        "value": "virtualMachines",
                        "localizedValue": "Virtual Machines"
                    }
                }
            ]}
        return (200, {}, json.dumps(data))

    def _get_async(self, request):
        path = request.path_url[len('/async'):]
        ret = requests.get('https://management.azure.com' + path)
        data = {
            'status': 'Succeeded',
            'properties': {
                'output': ret.json(),
            }
        }
        return (200, {}, json.dumps(data))

    def _put_sas(self, request):
        return (201, {}, '')
