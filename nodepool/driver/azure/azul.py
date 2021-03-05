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

import requests
import logging
import time


class AzureAuth(requests.auth.AuthBase):
    AUTH_URL = "https://login.microsoftonline.com/{tenantId}/oauth2/token"

    def __init__(self, credential):
        self.log = logging.getLogger("azul.auth")
        self.credential = credential
        self.token = None
        self.expiration = time.time()

    def refresh(self):
        if self.expiration - time.time() < 60:
            self.log.debug('Refreshing authentication token')
            url = self.AUTH_URL.format(**self.credential)
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.credential['clientId'],
                'client_secret': self.credential['clientSecret'],
                'resource': 'https://management.azure.com/',
            }
            r = requests.post(url, data)
            ret = r.json()
            self.token = ret['access_token']
            self.expiration = float(ret['expires_on'])

    def __call__(self, r):
        self.refresh()
        r.headers["authorization"] = "Bearer " + self.token
        return r


class AzureError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class AzureNotFoundError(AzureError):
    def __init__(self, status_code, message):
        super().__init__(status_code, message)


class AzureResourceGroupsCRUD:
    def __init__(self, cloud, version):
        self.cloud = cloud
        self.version = version

    def url(self, url, **args):
        base_url = (
            'https://management.azure.com/subscriptions/{subscriptionId}'
            '/resourcegroups/')
        url = base_url + url + '?api-version={apiVersion}'
        args = args.copy()
        args.update(self.cloud.credential)
        args['apiVersion'] = self.version
        return url.format(**args)

    def list(self):
        url = self.url('')
        return self.cloud.paginate(self.cloud.get(url))

    def get(self, name):
        url = self.url(name)
        return self.cloud.get(url)

    def create(self, name, params):
        url = self.url(name)
        return self.cloud.put(url, params)

    def delete(self, name):
        url = self.url(name)
        return self.cloud.delete(url)


class AzureCRUD:
    def __init__(self, cloud, resource, version):
        self.cloud = cloud
        self.resource = resource
        self.version = version

    def url(self, url, **args):
        base_url = (
            'https://management.azure.com/subscriptions/{subscriptionId}'
            '/resourceGroups/{resourceGroupName}/providers/')
        url = base_url + url + '?api-version={apiVersion}'
        args = args.copy()
        args.update(self.cloud.credential)
        args['apiVersion'] = self.version
        return url.format(**args)

    def id_url(self, url, **args):
        base_url = 'https://management.azure.com'
        url = base_url + url + '?api-version={apiVersion}'
        args = args.copy()
        args['apiVersion'] = self.version
        return url.format(**args)

    def list(self, resource_group_name):
        url = self.url(
            self.resource,
            resourceGroupName=resource_group_name,
        )
        return self.cloud.paginate(self.cloud.get(url))

    def get_by_id(self, resource_id):
        url = self.id_url(resource_id)
        return self.cloud.get(url)

    def get(self, resource_group_name, name):
        url = self.url(
            '{_resource}/{_resourceName}',
            _resource=self.resource,
            _resourceName=name,
            resourceGroupName=resource_group_name,
        )
        return self.cloud.get(url)

    def create(self, resource_group_name, name, params):
        url = self.url(
            '{_resource}/{_resourceName}',
            _resource=self.resource,
            _resourceName=name,
            resourceGroupName=resource_group_name,
        )
        return self.cloud.put(url, params)

    def delete(self, resource_group_name, name):
        url = self.url(
            '{_resource}/{_resourceName}',
            _resource=self.resource,
            _resourceName=name,
            resourceGroupName=resource_group_name,
        )
        return self.cloud.delete(url)


class AzureDictResponse(dict):
    def __init__(self, response, *args):
        super().__init__(*args)
        self.response = response
        self.last_retry = time.time()


class AzureListResponse(list):
    def __init__(self, response, *args):
        super().__init__(*args)
        self.response = response
        self.last_retry = time.time()


class AzureCloud:
    TIMEOUT = 60

    def __init__(self, credential):
        self.credential = credential
        self.session = requests.Session()
        self.log = logging.getLogger("azul")
        self.auth = AzureAuth(credential)
        self.network_interfaces = AzureCRUD(
            self,
            'Microsoft.Network/networkInterfaces',
            '2020-07-01')
        self.public_ip_addresses = AzureCRUD(
            self,
            'Microsoft.Network/publicIPAddresses',
            '2020-07-01')
        self.virtual_machines = AzureCRUD(
            self,
            'Microsoft.Compute/virtualMachines',
            '2020-12-01')
        self.disks = AzureCRUD(
            self,
            'Microsoft.Compute/disks',
            '2020-06-30')
        self.resource_groups = AzureResourceGroupsCRUD(
            self,
            '2020-06-01')

    def get(self, url, codes=[200]):
        return self.request('GET', url, None, codes)

    def put(self, url, data, codes=[200, 201]):
        return self.request('PUT', url, data, codes)

    def delete(self, url, codes=[200, 201, 202, 204]):
        return self.request('DELETE', url, None, codes)

    def request(self, method, url, data, codes):
        self.log.debug('%s: %s %s' % (method, url, data))
        response = self.session.request(
            method, url, json=data,
            auth=self.auth, timeout=self.TIMEOUT,
            headers={'Accept': 'application/json',
                     'Accept-Encoding': 'gzip'})

        self.log.debug("Received headers: %s", response.headers)
        if response.status_code in codes:
            if len(response.text):
                self.log.debug("Received: %s", response.text)
                ret_data = response.json()
                if isinstance(ret_data, list):
                    return AzureListResponse(response, ret_data)
                else:
                    return AzureDictResponse(response, ret_data)
            self.log.debug("Empty response")
            return AzureDictResponse(response, {})
        err = response.json()
        self.log.error(response.text)
        if response.status_code == 404:
            raise AzureNotFoundError(
                response.status_code, err['error']['message'])
        else:
            raise AzureError(response.status_code, err['error']['message'])

    def paginate(self, data):
        ret = data['value']
        while 'nextLink' in data:
            data = self.get(data['nextLink'])
            ret += data['value']
        return ret

    def check_async_operation(self, response):
        resp = response.response
        location = resp.headers.get(
            'Azure-AsyncOperation',
            resp.headers.get('Location', None))
        if not location:
            self.log.debug("No async operation found")
            return None
        remain = (response.last_retry +
                  float(resp.headers.get('Retry-After', 2))) - time.time()
        self.log.debug("remain time %s", remain)
        if remain > 0:
            time.sleep(remain)
        response.last_retry = time.time()
        return self.get(location)

    def wait_for_async_operation(self, response, timeout=600):
        start = time.time()
        while True:
            if time.time() - start > timeout:
                raise Exception("Timeout waiting for async operation")
            ret = self.check_async_operation(response)
            if ret is None:
                return
            if ret['status'] == 'InProgress':
                continue
            if ret['status'] == 'Succeeded':
                return
            raise Exception("Unhandled async operation result: %s",
                            ret['status'])
