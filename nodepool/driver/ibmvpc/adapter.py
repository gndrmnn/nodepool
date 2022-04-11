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

import logging
import math
import os
import re
import threading
import time

import cachetools.func

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine

from ibm_vpc import VpcV1
from ibm_cloud_sdk_core import get_query_param, ApiException
from ibm_cloud_sdk_core.get_authenticator import (
    get_authenticator_from_environment
)
from ibm_platform_services import ResourceManagerV2
from ibm_platform_services import ResourceControllerV2
from ibm_botocore.client import Config
import ibm_botocore.exceptions
import ibm_boto3
import ibm_boto3.s3.transfer

"""
Metadata and leak detection
---------------------------

IBM Cloud does have support for tagging objects, however, the API is
separate from the object API, which means there is a window between
creating and tagging an object.  Since our use of metadata is
specifically to deal with edge conditions and inopportune crashes,
etc, this is not sufficient for Nodepool's leak detection.

To mitigate this, we use resource groups to hold the same information.
We will create resource groups for each provider-pool with a specific
naming scheme ("np_<provider>_<pool>"), and assume that any item
created in that RG wbelongs to us.  That takes care of two pieces of
metadata; the third is the node id, and we will encode that in the
name (np<id>).

Images are not specific to a pool, so they will be stored under the
"npimages_<provider>" pool.

We could use tags to store metadata on the resource groups (and
therefore release the name restriction), but since we only need to
store two pieces of information on them, it's not necessary.

"""

HOSTNAME_RE = re.compile(r'^np([0-9]+)$')
UPLOAD_RE = re.compile(r'^npimage([0-9]+)(.*?)')
RESOURCE_GROUP_RE = re.compile(r'^np_([A-Za-z0-9-]+)_([A-Za-z0-9-]+)$')
IMAGE_RESOURCE_GROUP_RE = re.compile(r'^npimages_([A-Za-z0-9-]+)$')
CREDENTIALS_FILE_LOCK = threading.Lock()


def make_resource_group_name(provider_name, pool_name):
    if '_' in provider_name or '_' in pool_name:
        raise Exception("Provider and pool names may not contain underscores")
    return f'np_{provider_name}_{pool_name}'


def make_image_resource_group_name(provider_name):
    if '_' in provider_name:
        raise Exception("Provider and pool names may not contain underscores")
    return f'npimages_{provider_name}'


def get_metadata_from_resource_group_object(resource_group, obj):
    metadata = {}
    m = HOSTNAME_RE.match(obj['name'])
    if m:
        metadata['nodepool_node_id'] = m.group(1)
    else:
        m = UPLOAD_RE.match(obj['name'])
        if m:
            metadata['nodepool_upload_id'] = m.group(1)
    if resource_group:
        m = RESOURCE_GROUP_RE.match(resource_group['name'])
        if m:
            metadata['nodepool_provider_name'] = m.group(1)
            metadata['nodepool_pool_name'] = m.group(2)
        else:
            m = IMAGE_RESOURCE_GROUP_RE.match(resource_group['name'])
            if m:
                metadata['nodepool_provider_name'] = m.group(1)
    return metadata


def quota_info_from_profile(profile):
    if not profile:
        return QuotaInformation(instances=1)

    cores = profile.get('vcpu_count', {}).get('value', None)
    ram = profile.get('memory', {}).get('value', None)

    return QuotaInformation(
        cores=cores,
        ram=ram,
        instances=1)


class IBMVPCInstance(statemachine.Instance):
    def __init__(self, provider, vm, profile, resource_group, fip):
        super().__init__()
        self.external_id = vm['name']
        self.metadata = get_metadata_from_resource_group_object(
            resource_group, vm)
        self.private_ipv4 = vm['primary_network_interface'][
            'primary_ipv4_address']
        self.private_ipv6 = None
        self.public_ipv4 = None
        self.public_ipv6 = None
        self.profile = profile

        if fip:
            self.public_ipv4 = fip['address']

        self.interface_ip = self.public_ipv4 or self.private_ipv4
        self.region = provider.region
        self.az = vm['zone']['name']

    def getQuotaInformation(self):
        return quota_info_from_profile(self.profile)


class IBMVPCResource(statemachine.Resource):
    def __init__(self, type, obj, resource_group):
        metadata = get_metadata_from_resource_group_object(resource_group, obj)
        super().__init__(metadata)
        self.type = type
        self.name = obj['name']
        self.id = obj['id']


class IBMVPCDeleteStateMachine(statemachine.StateMachine):
    FIP_DELETING = 'deleting fip'
    VM_DELETING = 'deleting vm'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id):
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id
        self.fip = None

    def advance(self):
        if self.state == self.START:
            self.fip = self.adapter._deleteFloatingIP(self.external_id)
            self.state = self.FIP_DELETING

        if self.state == self.FIP_DELETING:
            if self.fip:
                self.fip = self.adapter._refreshFloatingIP(
                    self.fip, delete=True)
            if self.fip is None:
                self.vm = self.adapter._deleteInstance(self.external_id)
                self.state = self.VM_DELETING

        if self.state == self.VM_DELETING:
            self.vm = self.adapter._refreshInstance(self.vm, delete=True)
            if self.vm is None:
                self.state = self.COMPLETE
                self.complete = True


class IBMVPCCreateStateMachine(statemachine.StateMachine):
    FIP_CREATING = 'creating fip'
    VM_CREATING = 'creating vm'
    VM_RETRY = 'retrying vm creation'
    FIP_ATTACHING = 'attaching fip'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries):
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.attempts = 0
        if image_external_id:
            self.image_id = {'id': image_external_id}
        else:
            self.image_id = None
        self.metadata = metadata
        self.hostname = hostname
        self.label = label
        self.fip = None
        self.vm = None
        self.resource_group_name = make_resource_group_name(
            self.adapter.provider.name,
            label.pool.name)

    def _findImage(self):
        if self.image_id:
            return

        if self.label.cloud_image and self.label.cloud_image.image_id:
            self.image_id = {'id': self.label.cloud_image.image_id}

        if self.label.cloud_image and self.label.cloud_image.image_href:
            self.image_id = {'href': self.label.cloud_image.image_href}

        if self.label.cloud_image and self.label.cloud_image.image_name:
            self.image_id = self.adapter._getImageIdFromName(
                self.label.cloud_image.image_name)

        if self.label.cloud_image and self.label.cloud_image.image_filter:
            self.image_id = self.adapter._getImageIdFromFilter(
                self.label.cloud_image.image_filter)

    def advance(self):
        if self.state == self.START:
            self.external_id = self.hostname

            self._findImage()

            if self.label.pool.public_ipv4:
                self.fip = self.adapter._createFloatingIP(
                    self.hostname, self.label, self.resource_group_name)
            self.state = self.FIP_CREATING

        if self.state == self.FIP_CREATING:
            if self.fip:
                self.fip = self.adapter._refreshFloatingIP(self.fip)
                if self.fip['status'] == 'failed':
                    raise Exception("Floating IP Error")
                if self.fip['status'] != 'available':
                    return
            self.vm = self.adapter._createInstance(
                self.label, self.image_id, self.hostname,
                self.resource_group_name)
            self.state = self.VM_CREATING

        if self.state == self.VM_CREATING:
            self.vm = self.adapter._refreshInstance(self.vm)
            if self.vm['status'] == 'running':
                if self.fip:
                    self.adapter._attachFloatingIP(self.vm, self.fip)
                self.state = self.FIP_ATTACHING
            elif self.vm['status'] == 'failed':
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                self.vm = self.adapter._deleteInstance(
                    self.external_id)
                self.state = self.VM_RETRY
            else:
                return

        if self.state == self.VM_RETRY:
            self.vm = self.adapter._refreshInstance(self.vm, delete=True)
            if self.vm is None:
                self.state = self.FIP_CREATING
                return

        if self.state == self.FIP_ATTACHING:
            if self.fip:
                self.fip = self.adapter._refreshFloatingIP(self.fip)
                if self.fip['status'] == 'failed':
                    raise Exception("Floating IP Error")
                if 'target' not in self.fip:
                    return
            self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            rg = self.adapter._getResourceGroupByReference(
                self.vm['resource_group'])
            profile = self.adapter.profiles.get(self.vm['profile']['name'])
            return IBMVPCInstance(self.adapter.provider,
                                  self.vm, profile, rg, self.fip)


class IBMVPCAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.ibmvpc.IBMVPCAdapter")
    list_ttl = 10
    IMAGE_UPLOAD_SLEEP = 30

    def __init__(self, provider_config):
        self.provider = provider_config
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)
        with CREDENTIALS_FILE_LOCK:
            # Hold the lock during initialization of all of the
            # service managers since they may consult the env
            # variables and credentials file contents during
            # initialization.
            old_cred_value = os.environ.get('IBM_CREDENTIALS_FILE')
            try:
                authenticator = self._initResourceManagers()
            finally:
                if old_cred_value is None:
                    if 'IBM_CREDENTIALS_FILE' in os.environ:
                        del os.environ['IBM_CREDENTIALS_FILE']
                else:
                    os.environ['IBM_CREDENTIALS_FILE'] = old_cred_value

        self.vpc = None
        for vpc in self.cloud_vpc.list_vpcs().get_result()['vpcs']:
            if vpc['name'] == self.provider.vpc:
                self.vpc = vpc
                break
        if self.vpc is None:
            raise Exception(f"Unable to find vpc {self.provider.vpc}")

        self.storage_instance = None
        if self.provider.object_storage:
            for res in self.cloud_resource_controller.list_resource_instances(
                    name=self.provider.object_storage['instance-name'],
                    type='service_instance').get_result()['resources']:
                crn_parts = res['crn'].split(':')
                service_name = crn_parts[4]
                if service_name == 'cloud-object-storage':
                    self.storage_instance = res
            if hasattr(authenticator, 'token_manager'):
                apikey = authenticator.token_manager.apikey
            else:
                # This code path is used by unit tests
                apikey = 'test'
            self.cloud_storage = ibm_boto3.client(
                "s3",
                ibm_api_key_id=apikey,
                ibm_service_instance_id=self.storage_instance['crn'],
                config=Config(signature_version="oauth"),
                endpoint_url=self.provider.object_storage['endpoint'],
            )

        self.resource_groups = {}
        self._getResourceGroups()
        self._createResourceGroups()

        self.profiles = {}
        self._getProfiles()
        self.subnets = {}
        self._getSubnets()
        # Sanity check the subnet is in the provider's zone
        for pool in self.provider.pools.values():
            subnet_zone = self.subnets[pool.subnet]['zone']['name']
            if subnet_zone != pool.zone:
                raise Exception(f"Subnet {pool.subnet} zone "
                                f"{subnet_zone} does not match pool "
                                f"zone {pool.zone}")
        self.keys = {}
        self._getKeys()

    def _initResourceManagers(self):
        # This method is called inside a global lock that allows us to
        # update environment variables without affecting other
        # threads.  It also restores the IBM_CREDENTIALS_FILE env
        # variable after we return.  Perform any initialization which
        # may be affected by setting this env variable here.
        if self.provider.credentials_file:
            os.environ['IBM_CREDENTIALS_FILE'] = self.provider.credentials_file
        authenticator = get_authenticator_from_environment('vpc')
        self.cloud_vpc = VpcV1(authenticator=authenticator)
        # Set the service URL to our region
        service_url = f"https://{self.provider.region}.iaas.cloud.ibm.com/v1"
        self.cloud_vpc.set_service_url(service_url)
        # These use a global URL; no region needed
        self.cloud_resource_manager = ResourceManagerV2(
            authenticator=authenticator)
        self.cloud_resource_controller = ResourceControllerV2(
            authenticator=authenticator)
        return authenticator

    def getCreateStateMachine(self, hostname, label,
                              image_external_id, metadata, retries, log):
        return IBMVPCCreateStateMachine(self, hostname, label,
                                        image_external_id, metadata, retries)

    def getDeleteStateMachine(self, external_id, log):
        return IBMVPCDeleteStateMachine(self, external_id)

    def listResources(self):
        for vm in self._listInstances():
            rg = self._getResourceGroupByReference(vm['resource_group'])
            yield IBMVPCResource('instance', vm, rg)
        for fip in self._listFloatingIPs():
            rg = self._getResourceGroupByReference(fip['resource_group'])
            yield IBMVPCResource('fip', fip, rg)
        for image in self._listImages():
            if image['owner_type'] != 'user':
                continue
            rg = self._getResourceGroupByReference(image['resource_group'])
            yield IBMVPCResource('image', image, rg)
        rgname = make_image_resource_group_name(self.provider.name)
        if self.storage_instance and rgname in self.resource_groups:
            rg = self.resource_groups[rgname]
            try:
                objects = self.cloud_storage.list_objects(
                    Bucket=self.provider.object_storage['bucket-name']
                )['Contents']
            except ibm_botocore.exceptions.ClientError:
                # The bucket may not exist
                objects = []
            for obj in objects:
                # Make an object that looks like a VPC object for
                # compatability
                storage_object = {
                    'name': obj['Key'],
                    'id': obj['Key'],
                }
                yield IBMVPCResource('object', storage_object, rg)

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.name}")
        with self.rate_limiter:
            if resource.type == 'instance':
                self.cloud_vpc.delete_instance(resource.id)
            elif resource.type == 'fip':
                self.cloud_vpc.delete_floating_ip(resource.id)
            elif resource.type == 'image':
                self.cloud_vpc.delete_image(resource.id)
            elif resource.type == 'object':
                self.cloud_storage.delete_object(
                    Bucket=self.provider.object_storage['bucket-name'],
                    Key=resource.id)

    def listInstances(self):
        fips = {ip['name']: ip for ip in self._listFloatingIPs()}
        for vm in self._listInstances():
            rg = self._getResourceGroupByReference(vm['resource_group'])
            profile = self.profiles.get(vm['profile']['name'])
            fip = fips.get(vm['name'])
            if fip and 'target' not in fip:
                fip = None
            if fip:
                if fip['target']['id'] != vm['primary_network_interface'][
                        'id']:
                    fip = None
            yield IBMVPCInstance(self.provider, vm, profile, rg, fip)

    def getQuotaLimits(self):
        # IBM Cloud does not make quota information available through
        # the API.
        # This looks like it should:
        # https://cloud.ibm.com/apidocs/resource-controller/resource-manager?code=python#list-quota-definitions
        # But the keys and values don't bear any resemblance to this:
        # https://cloud.ibm.com/docs/vpc?topic=vpc-quotas

        cores = self.provider.quota.get('cores', None)
        ram = self.provider.quota.get('ram', None)
        instances = self.provider.quota.get('instances', None)

        return QuotaInformation(
            cores=cores,
            ram=ram,
            instances=instances,
            default=math.inf)

    def getQuotaForLabel(self, label):
        profile = self.profiles.get(label.profile)
        return quota_info_from_profile(profile)

    def uploadImage(self, provider_image, image_name, filename,
                    image_format, metadata, md5, sha256):

        # Find the bucket location to construct the url
        bucket_name = self.provider.object_storage['bucket-name']
        bucket_location = self.cloud_storage.get_bucket_location(
            Bucket=bucket_name
        )['LocationConstraint'].rsplit('-', 1)[0]
        object_filename = f'{image_name}.{image_format}'
        object_url = f'cos://{bucket_location}/{bucket_name}/{object_filename}'

        self.log.debug(f"Uploading image {image_name} to object storage")
        with open(filename, "rb") as fobj:
            self.cloud_storage.upload_fileobj(
                fobj, bucket_name, object_filename)

        rgname = make_image_resource_group_name(self.provider.name)
        if rgname not in self.resource_groups:
            self.log.info(f"Creating resource group {rgname}")
            resp = self.cloud_resource_manager.create_resource_group(
                name=rgname).get_result()
            self.resource_groups[rgname] = resp

        self.log.debug(f"Creating image {image_name}")
        with self.rate_limiter:
            rg = self.resource_groups[rgname]
            image = self.cloud_vpc.create_image({
                'name': image_name,
                'resource_group': {'id': rg['id']},
                'file': {'href': object_url},
                'operating_system': {'name': provider_image.operating_system},
            }).get_result()

        while True:
            time.sleep(self.IMAGE_UPLOAD_SLEEP)
            with self.rate_limiter:
                image = self.cloud_vpc.get_image(image['id']).get_result()
                if image['status'] == 'pending':
                    continue
                if image['status'] == 'available':
                    break
                raise Exception(
                    f"Image status of {image_name} is {image['status']}")

        self.log.debug(f"Image {image_name} is available")

        self.log.debug("Deleting image from object storage")
        self.cloud_storage.delete_object(Bucket=bucket_name,
                                         Key=object_filename)

        self.log.info(f"Uploaded image {image_name}")
        return image['id']

    def deleteImage(self, external_id):
        self.log.debug(f"Deleting image {external_id}")
        bucket_name = self.provider.object_storage['bucket-name']

        with self.rate_limiter:
            try:
                image = self.cloud_vpc.get_image(external_id).get_result()
            except ApiException as e:
                if e.code == 404:
                    self.log.debug(f"Image {external_id} does not exist")
                    return

        # See if there are any objects to clean up
        name = image['name']
        with self.rate_limiter:
            objects = self.cloud_storage.list_objects(
                Bucket=self.provider.object_storage['bucket-name']
            )['Contents']
        for obj in objects:
            key = obj['Key']
            if key.startswith(name):
                self.log.debug(f"Deleting object {key}")
                with self.rate_limiter:
                    self.cloud_storage.delete_object(Bucket=bucket_name,
                                                     Key=key)
                self.log.info(f"Deleted object {key}")

        with self.rate_limiter:
            self.cloud_vpc.delete_image(external_id)

        self.log.info(f"Deleted image {external_id}")

    # Local implementation below

    def _handlePagination(self, fn, resultname, args=[], kw={}):
        ret = []
        start = None
        with self.rate_limiter:
            while True:
                result = fn(*args, **kw, start=start).get_result()
                ret.extend(result[resultname])
                if 'next' in result:
                    start = get_query_param(result['next']['href'], 'start')
                else:
                    break
        return ret

    def _getResourceGroups(self):
        for rg in self.cloud_resource_manager.list_resource_groups()\
                                             .get_result()['resources']:
            self.log.debug(f"Found resource group {rg['name']}")
            self.resource_groups[rg['name']] = rg

    def _getResourceGroupByReference(self, ref):
        # The API docs say that a resource group reference always
        # includes a name, however in at least one case (image list)
        # it does not.  This method attempts to look up a RG by name
        # if present, and if not, falls back to id.
        if 'name' in ref:
            return self.resource_groups[ref['name']]
        for rg in self.resource_groups.values():
            if rg['id'] == ref['id']:
                return rg
        raise Exception(f"Resource group not found: {ref}")

    def _createResourceGroups(self):
        # Create a resource group for every pool
        for pool in self.provider.pools:
            rgname = make_resource_group_name(self.provider.name, pool)
            if rgname in self.resource_groups:
                continue
            self.log.info(f"Creating resource group {rgname}")
            resp = self.cloud_resource_manager.create_resource_group(
                name=rgname).get_result()
            self.resource_groups[rgname] = resp

    def _getProfiles(self):
        self.log.debug("Querying instance profiles")
        with self.rate_limiter:
            result = self.cloud_vpc.list_instance_profiles().get_result()
            for profile in result['profiles']:
                self.profiles[profile['name']] = profile
        self.log.debug("Done querying instance profiles")

    def _getSubnets(self):
        self.log.debug("Querying subnets")
        for sn in self._handlePagination(self.cloud_vpc.list_subnets,
                                         'subnets'):
            self.subnets[sn['name']] = sn
        self.log.debug("Done querying subnets")

    def _getKeys(self):
        self.log.debug("Querying keys")
        for k in self._handlePagination(self.cloud_vpc.list_keys,
                                        'keys'):
            self.keys[k['name']] = k
        self.log.debug("Done querying keys")

    # Floating IPs

    @cachetools.func.ttl_cache(maxsize=1, ttl=list_ttl)
    def _listFloatingIPs(self):
        return self._handlePagination(self.cloud_vpc.list_floating_ips,
                                      'floating_ips')

    def _refreshFloatingIP(self, obj, delete=False, force=False):
        if obj is None:
            return None

        for new_obj in self._listFloatingIPs():
            if new_obj['id'] == obj['id']:
                return new_obj
        if delete:
            return None
        return obj

    def _createFloatingIP(self, hostname, label, resource_group_name):
        rg = self.resource_groups[resource_group_name]
        with self.rate_limiter:
            self.log.debug(f"Creating floating IP address {hostname}")
            return self.cloud_vpc.create_floating_ip({
                'name': hostname,
                'resource_group': {'id': rg['id']},
                'zone': {'name': label.pool.zone},
            }).get_result()

    def _attachFloatingIP(self, vm, fip):
        with self.rate_limiter:
            self.log.debug(f"Attaching floating IP address to {vm['name']}")
            return self.cloud_vpc.add_instance_network_interface_floating_ip(
                vm['id'], vm['primary_network_interface']['id'], fip['id']
            ).get_result()

    def _deleteFloatingIP(self, name):
        for fip in self._listFloatingIPs():
            if fip['name'] == name:
                break
        else:
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting floating IP address {name}")
            self.cloud_vpc.delete_floating_ip(fip['id'])
        return fip

    # Instances

    @cachetools.func.ttl_cache(maxsize=1, ttl=list_ttl)
    def _listInstances(self):
        return self._handlePagination(self.cloud_vpc.list_instances,
                                      'instances')

    def _refreshInstance(self, obj, delete=False):
        if obj is None:
            return None

        for new_obj in self._listInstances():
            if new_obj['id'] == obj['id']:
                return new_obj
        if delete:
            return None
        return obj

    def _createInstance(self, label, image_id, hostname, resource_group_name):
        rg = self.resource_groups[resource_group_name]
        subnet_id = self.subnets[label.pool.subnet]['id']

        if label.cloud_image and label.cloud_image.keys:
            key_ids = [self.keys[k]['id'] for k in label.cloud_image.keys]
        else:
            key_ids = []
        with self.rate_limiter:
            self.log.debug(f"Creating instance {hostname} from {image_id}")
            data = {
                'keys': [{'id': k} for k in key_ids],
                'name': hostname,
                'profile': {'name': label.profile},
                'resource_group': {'id': rg['id']},
                'vpc': {'id': self.vpc['id']},
                'image': image_id,
                'primary_network_interface': {
                    'name': 'eth0',
                    'subnet': {'id': subnet_id},
                },
                'zone': {'name': label.pool.zone},
                'boot_volume_attachment': {
                    'volume': {
                        'name': hostname,
                        'profile': {'name': 'general-purpose'},
                    },
                    'delete_volume_on_instance_delete': True,
                },
            }
            if label.user_data:
                data['user_data'] = label.user_data
            return self.cloud_vpc.create_instance(data).get_result()

    def _deleteInstance(self, name):
        for vm in self._listInstances():
            if vm['name'] == name:
                break
        else:
            self.log.warning(f"VM not found when deleting {name}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting VM {name}")
            self.cloud_vpc.delete_instance(vm['id'])
        return vm

    # Images

    @cachetools.func.ttl_cache(maxsize=1, ttl=list_ttl)
    def _listImages(self):
        return self._handlePagination(self.cloud_vpc.list_images,
                                      'images')

    def _getImageIdFromName(self, image_name):
        images = self._listImages()
        images = [i for i in images if i['name'] == image_name]
        if not images:
            raise Exception("Unable to find image matching name: %s",
                            image_name)
        image = images[0]
        self.log.debug("Found image matching name: %s", image_name)
        return {'id': image['id']}

    def _getImageIdFromFilter(self, image_filter):
        images = []

        os_dict = image_filter.get('operating-system', {})
        os_dict = {k.replace('-', '_'): v for (k, v) in os_dict.items()}
        for img in self._listImages():
            if os_dict:
                img_os_dict = {
                    k: v for (k, v) in img['operating_system'].items()
                    if k in os_dict
                }
                if img_os_dict != os_dict:
                    continue
            if 'owner-type' in image_filter:
                if image_filter['owner-type'] != img['owner_type']:
                    continue
            if 'status' in image_filter:
                if image_filter['status'] != img['status']:
                    continue
            images.append(img)
        if not images:
            raise Exception("Unable to find image matching filter: %s",
                            image_filter)
        self.log.debug("Found %i images matching filter: %s",
                       len(images), image_filter)
        image = images[0]
        return {'id': image['id']}

    # This method is currently unused but is retained in case it is
    # useful in the future
    def _detachFloatingIP(self, name):
        for fip in self._listFloatingIPs():
            if fip['name'] == name:
                break
        else:
            return None
        for vm in self._listInstances():
            if vm['name'] == name:
                break
        else:
            return fip
        # Make sure we have the most up to date info to know if it's attached
        if 'target' not in fip:
            with self.rate_limiter:
                fip = self.cloud_vpc.get_floating_ip(fip['id']).get_result()
        if 'target' not in fip:
            return fip
        with self.rate_limiter:
            self.log.debug(f"Detaching floating IP address from {name}")
            return self.cloud_vpc.\
                remove_instance_network_interface_floating_ip(
                    vm['id'], vm['primary_network_interface']['id'], fip['id']
                ).get_result()
