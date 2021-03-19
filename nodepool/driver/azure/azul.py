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

import concurrent.futures
import logging
import time

import requests


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
    def __init__(self, status_code, error_code, message):
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code


class AzureNotFoundError(AzureError):
    pass


class AzureCRUD:
    base_subscription_url = (
        'https://management.azure.com/subscriptions/{subscriptionId}/')
    base_url = ''

    def __init__(self, cloud, **kw):
        self.cloud = cloud
        self.args = kw.copy()
        self.args.update(self.cloud.credential)

    def url(self, endpoint=None, **kw):
        if endpoint is None:
            endpoint = ''
        else:
            endpoint = '/' + endpoint
        url = (self.base_subscription_url + self.base_url + endpoint
               + '?api-version={apiVersion}')
        args = self.args.copy()
        args.update(kw)
        return url.format(**args)

    def id_url(self, url, **kw):
        base_url = 'https://management.azure.com'
        url = base_url + url + '?api-version={apiVersion}'
        args = self.args.copy()
        args.update(kw)
        return url.format(**args)

    def get_by_id(self, resource_id):
        url = self.id_url(resource_id)
        return self.cloud.get(url)

    def _list(self, **kw):
        url = self.url(**kw)
        return self.cloud.paginate(self.cloud.get(url))

    def list(self):
        return self._list()

    def _get(self, **kw):
        url = self.url(**kw)
        return self.cloud.get(url)

    def _create(self, params, **kw):
        url = self.url(**kw)
        return self.cloud.put(url, params)

    def _delete(self, **kw):
        url = self.url(**kw)
        return self.cloud.delete(url)

    def _post(self, endpoint, params, **kw):
        url = self.url(endpoint=endpoint, **kw)
        return self.cloud.post(url, params)


class AzureResourceGroupsCRUD(AzureCRUD):
    base_url = 'resourcegroups/{resourceGroupName}'

    def list(self):
        return self._list(resourceGroupName='')

    def get(self, name):
        return self._get(resourceGroupName=name)

    def create(self, name, params):
        return self._create(params, resourceGroupName=name)

    def delete(self, name):
        return self._delete(resourceGroupName=name)


class AzureResourceProviderCRUD(AzureCRUD):
    base_url = (
        'resourceGroups/{resourceGroupName}/providers/'
        '{providerId}/{resource}/{resourceName}')

    def list(self, resource_group_name):
        return self._list(resourceGroupName=resource_group_name,
                          resourceName='')

    def get(self, resource_group_name, name):
        return self._get(resourceGroupName=resource_group_name,
                         resourceName=name)

    def create(self, resource_group_name, name, params):
        return self._create(params,
                            resourceGroupName=resource_group_name,
                            resourceName=name)

    def delete(self, resource_group_name, name):
        return self._delete(resourceGroupName=resource_group_name,
                            resourceName=name)

    def post(self, resource_group_name, name, endpoint, params):
        return self._post(endpoint, params,
                          resourceGroupName=resource_group_name,
                          resourceName=name)


class AzureNetworkCRUD(AzureCRUD):
    base_url = (
        'resourceGroups/{resourceGroupName}/providers/'
        'Microsoft.Network/virtualNetworks/{virtualNetworkName}/'
        '{resource}/{resourceName}')

    def list(self, resource_group_name, virtual_network_name):
        return self._list(resourceGroupName=resource_group_name,
                          virtualNetworkName=virtual_network_name,
                          resourceName='')

    def get(self, resource_group_name, virtual_network_name, name):
        return self._get(resourceGroupName=resource_group_name,
                         virtualNetworkName=virtual_network_name,
                         resourceName=name)

    def create(self, resource_group_name, virtual_network_name, name, params):
        return self._create(params,
                            resourceGroupName=resource_group_name,
                            virtualNetworkName=virtual_network_name,
                            resourceName=name)

    def delete(self, resource_group_name, virtual_network_name, name):
        return self._delete(resourceGroupName=resource_group_name,
                            virtualNetworkName=virtual_network_name,
                            resourceName=name)


class AzureLocationCRUD(AzureCRUD):
    base_url = (
        'providers/{providerId}/locations/{location}/{resource}')

    def list(self, location):
        return self._list(location=location)


class AzureProviderCRUD(AzureCRUD):
    base_url = (
        'providers/{providerId}/{resource}/')

    def list(self):
        return self._list()


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
        self.network_interfaces = AzureResourceProviderCRUD(
            self,
            providerId='Microsoft.Network',
            resource='networkInterfaces',
            apiVersion='2020-07-01')
        self.public_ip_addresses = AzureResourceProviderCRUD(
            self,
            providerId='Microsoft.Network',
            resource='publicIPAddresses',
            apiVersion='2020-07-01')
        self.virtual_machines = AzureResourceProviderCRUD(
            self,
            providerId='Microsoft.Compute',
            resource='virtualMachines',
            apiVersion='2020-12-01')
        self.disks = AzureResourceProviderCRUD(
            self,
            providerId='Microsoft.Compute',
            resource='disks',
            apiVersion='2020-06-30')
        self.images = AzureResourceProviderCRUD(
            self,
            providerId='Microsoft.Compute',
            resource='images',
            apiVersion='2020-12-01')
        self.resource_groups = AzureResourceGroupsCRUD(
            self,
            apiVersion='2020-06-01')
        self.subnets = AzureNetworkCRUD(
            self,
            resource='subnets',
            apiVersion='2020-07-01')
        self.compute_usages = AzureLocationCRUD(
            self,
            providerId='Microsoft.Compute',
            resource='usages',
            apiVersion='2020-12-01')
        self.compute_skus = AzureProviderCRUD(
            self,
            providerId='Microsoft.Compute',
            resource='skus',
            apiVersion='2019-04-01')

    def get(self, url, codes=[200]):
        return self.request('GET', url, None, codes)

    def put(self, url, data, codes=[200, 201, 202]):
        return self.request('PUT', url, data, codes)

    def post(self, url, data, codes=[200, 202]):
        return self.request('POST', url, data, codes)

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
                response.status_code,
                err['error']['code'],
                err['error']['message'])
        else:
            raise AzureError(response.status_code,
                             err['error']['code'],
                             err['error']['message'])

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
                return ret
            raise Exception("Unhandled async operation result: %s",
                            ret['status'])

    def _upload_chunk(self, url, start, end, data):
        headers = {
            'x-ms-blob-type': 'PageBlob',
            'x-ms-page-write': 'Update',
            'Content-Length': str(len(data)),
            'Range': f'bytes={start}-{end}',
        }
        attempts = 10
        for x in range(attempts):
            try:
                requests.put(url, headers=headers, data=data).\
                    raise_for_status()
                break
            except Exception:
                if x == attempts - 1:
                    raise
                else:
                    time.sleep(2 * x)

    def upload_page_blob_to_sas_url(self, url, file_object,
                                    pagesize=(4 * 1024 * 1024),
                                    concurrency=10):
        start = 0
        futures = set()
        if 'comp=page' not in url:
            url += '&comp=page'
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=concurrency) as executor:
            while True:
                chunk = file_object.read(pagesize)
                if not chunk:
                    break
                end = start + len(chunk) - 1
                future = executor.submit(self._upload_chunk, url,
                                         start, end, chunk)
                start += len(chunk)
                futures.add(future)
                # Keep the pool of work supplied with data but without
                # reading the entire file into memory.
                if len(futures) >= (concurrency * 2):
                    (done, futures) = concurrent.futures.wait(
                        futures,
                        return_when=concurrent.futures.FIRST_COMPLETED)
            # We're done reading the file, wait for all uploads to finish
            (done, futures) = concurrent.futures.wait(futures)
