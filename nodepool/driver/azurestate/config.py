# Copyright 2018 Red Hat
# Copyright 2021 Acme Gating, LLC
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import voluptuous as v
import os

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class AzureProviderCloudImage(ConfigValue):
    def __init__(self, image, zuul_public_key):
        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        self.name = image['name']
        self.username = image['username']
        # TODO(corvus): remove zuul_public_key
        self.key = image.get('key', zuul_public_key)
        self.image_reference = image['image-reference']
        self.python_path = image.get('python-path')
        self.connection_type = image.get('connection-type', 'ssh')
        self.connection_port = image.get(
            'connection-port',
            default_port_mapping.get(self.connection_type, 22))

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.name

    @staticmethod
    def getSchema():
        azure_image_reference = {
            v.Required('sku'): str,
            v.Required('publisher'): str,
            v.Required('version'): str,
            v.Required('offer'): str,
        }

        return {
            v.Required('name'): str,
            v.Required('username'): str,
            # TODO(corvus): make required when zuul_public_key removed
            'key': str,
            v.Required('image-reference'): azure_image_reference,
            'connection-type': str,
            'connection-port': int,
            'python-path': str,
            # TODO(corvus): shell-type
        }


class AzureProviderDiskImage(ConfigValue):
    def __init__(self, image, diskimage):
        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        self.name = image['name']
        diskimage.image_types.add('vhd')
        self.pause = bool(image.get('pause', False))
        self.python_path = image.get('python-path')
        self.username = image.get('username')
        self.key = image.get('key')
        self.connection_type = image.get('connection-type', 'ssh')
        self.connection_port = image.get(
            'connection-port',
            default_port_mapping.get(self.connection_type, 22))
        self.meta = {}

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.name

    @staticmethod
    def getSchema():
        return {
            v.Required('name'): str,
            'username': str,
            'key': str,
            'pause': bool,
            'connection-type': str,
            'connection-port': int,
            'python-path': str,
            # TODO(corvus): shell-type
        }


class AzureLabel(ConfigValue):
    ignore_equality = ['pool']

    def __init__(self, label, provider_config, provider_pool):
        self.hardware_profile = None

        self.name = label['name']
        self.pool = provider_pool

        cloud_image_name = label.get('cloud-image')
        if cloud_image_name:
            cloud_image = provider_config.cloud_images.get(
                cloud_image_name, None)
            if not cloud_image:
                raise ValueError(
                    "cloud-image %s does not exist in provider %s"
                    " but is referenced in label %s" %
                    (cloud_image_name, provider_config.name, self.name))
            self.cloud_image = cloud_image
        else:
            self.cloud_image = None

        diskimage_name = label.get('diskimage')
        if diskimage_name:
            diskimage = provider_config.diskimages.get(
                diskimage_name, None)
            if not diskimage:
                raise ValueError(
                    "diskimage %s does not exist in provider %s"
                    " but is referenced in label %s" %
                    (diskimage_name, provider_config.name, self.name))
            self.diskimage = diskimage
        else:
            self.diskimage = None

        self.hardware_profile = label['hardware-profile']
        self.tags = label.get('tags', {})

    @staticmethod
    def getSchema():
        azure_hardware_profile = {
            v.Required('vm-size'): str,
        }

        return {
            v.Required('name'): str,
            'cloud-image': str,
            'diskimage': str,
            v.Required('hardware-profile'): azure_hardware_profile,
            'tags': dict,
        }


class AzurePool(ConfigPool):
    ignore_equality = ['provider']

    def __init__(self, provider_config, pool_config):
        super().__init__()
        self.provider = provider_config
        self.load(pool_config)

    def load(self, pool_config):
        self.name = pool_config['name']
        self.max_servers = pool_config['max-servers']
        self.public_ipv4 = pool_config.get('public-ipv4',
                                           self.provider.public_ipv4)
        self.public_ipv6 = pool_config.get('public-ipv6',
                                           self.provider.public_ipv6)
        self.ipv4 = pool_config.get('ipv4', self.provider.ipv4)
        self.ipv6 = pool_config.get('ipv6', self.provider.ipv6)
        self.ipv4 = self.ipv4 or self.public_ipv4
        self.ipv6 = self.ipv6 or self.public_ipv6
        if not self.ipv4 or self.ipv6:
            self.ipv4 = True
        self.use_internal_ip = pool_config.get(
            'use-internal-ip', self.provider.use_internal_ip)
        self.host_key_checking = pool_config.get(
            'host-key-checking', self.provider.use_internal_ip)

    @staticmethod
    def getSchema():
        azure_label = AzureLabel.getSchema()

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [azure_label],
            'ipv4': bool,
            'ipv6': bool,
            'public-ipv4': bool,
            'public-ipv6': bool,
            'use-internal-ip': bool,
            'host-key-checking': bool,
        })
        return pool


class AzureProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        super().__init__(provider)
        self._pools = {}
        self.rate_limit = None
        self.launch_retries = None

    @property
    def pools(self):
        return self._pools

    @property
    def manage_images(self):
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.image_type = 'vhd'
        self.image_name_format = '{image_name}-{timestamp}'
        self.post_upload_hook = self.provider.get('post-upload-hook')

        self.rate_limit = self.provider.get('rate-limit', 1)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.boot_timeout = self.provider.get('boot-timeout', 60)

        # TODO(corvus): remove
        self.zuul_public_key = self.provider.get('zuul-public-key')
        self.location = self.provider['location']
        self.subnet_id = self.provider.get('subnet-id')
        self.network = self.provider.get('network')
        # Don't use these directly; these are default values for
        # labels.
        self.public_ipv4 = self.provider.get('public-ipv4', False)
        self.public_ipv6 = self.provider.get('public-ipv6', False)
        self.ipv4 = self.provider.get('ipv4', None)
        self.ipv6 = self.provider.get('ipv6', None)
        self.use_internal_ip = self.provider.get('use-internal-ip', False)
        self.host_key_checking = self.provider.get('host-key-checking', True)
        self.resource_group = self.provider['resource-group']
        self.resource_group_location = self.provider['resource-group-location']
        self.auth_path = self.provider.get(
            'auth-path', os.getenv('AZURE_AUTH_LOCATION', None))

        self.cloud_images = {}
        for image in self.provider['cloud-images']:
            i = AzureProviderCloudImage(image, self.zuul_public_key)
            self.cloud_images[i.name] = i

        self.diskimages = {}
        for image in self.provider['diskimages']:
            diskimage = config.diskimages[image['name']]
            i = AzureProviderDiskImage(image, diskimage)
            self.diskimages[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = AzurePool(self, pool)
            self._pools[pp.name] = pp

            for label in pool.get('labels', []):
                pl = AzureLabel(label, self, pp)
                pp.labels[pl.name] = pl
                config.labels[pl.name].pools.append(pp)

    def getSchema(self):
        provider_cloud_images = AzureProviderCloudImage.getSchema()

        pool = AzurePool.getSchema()

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('location'): str,
            v.Required('resource-group'): str,
            v.Required('resource-group-location'): str,
            'subnet-id': str,
            'network': v.Any(str, {
                'resource-group': str,
                'network': str,
                'subnet': str,
            }),
            v.Required('cloud-images'): [provider_cloud_images],
            v.Required('auth-path'): str,
            'ipv4': bool,
            'ipv6': bool,
            'public-ipv4': bool,
            'public-ipv6': bool,
            'use-internal-ip': bool,
            'host-key-checking': bool,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self._pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
