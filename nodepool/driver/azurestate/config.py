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


class ProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.image_id = None
        self.username = None
        self.key = None
        self.python_path = None
        self.connection_type = None
        self.connection_port = None

    def __eq__(self, other):
        if isinstance(other, ProviderCloudImage):
            return (self.name == other.name
                    and self.image_id == other.image_id
                    and self.username == other.username
                    and self.key == other.key
                    and self.python_path == other.python_path
                    and self.connection_type == other.connection_type
                    and self.connection_port == other.connection_port)
        return False

    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.name


class AzureLabel(ConfigValue):
    def __eq__(self, other):
        if (  # other.username != self.username or
              # other.imageReference != self.imageReference or
            other.hardware_profile != self.hardware_profile):
            return False
        return True


class AzurePool(ConfigPool):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<AzurePool %s>" % self.name

    def load(self, pool_config):
        pass


class AzureProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self._pools = {}
        self.driver_object = driver
        self.rate_limit = None
        self.launch_retries = None
        super().__init__(provider)

    def __eq__(self, other):
        if (other.location != self.location or
            other.pools != self.pools):
            return False
        return True

    @property
    def pools(self):
        return self._pools

    @property
    def manage_images(self):
        return False

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.rate_limit = self.provider.get('rate-limit', 1)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.boot_timeout = self.provider.get('boot-timeout', 60)

        self.zuul_public_key = self.provider['zuul-public-key']
        self.location = self.provider['location']
        self.subnet_id = self.provider['subnet-id']
        self.ipv6 = self.provider.get('ipv6', False)
        self.resource_group = self.provider['resource-group']
        self.resource_group_location = self.provider['resource-group-location']
        self.auth_path = self.provider.get(
            'auth-path', os.getenv('AZURE_AUTH_LOCATION', None))

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }

        self.cloud_images = {}
        for image in self.provider['cloud-images']:
            i = ProviderCloudImage()
            i.name = image['name']
            i.username = image['username']
            # i.key = image['key']
            i.key = self.zuul_public_key
            i.image_reference = image['image-reference']
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))
            self.cloud_images[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = AzurePool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            pp.use_internal_ip = bool(pool.get('use-internal-ip', False))
            pp.host_key_checking = bool(pool.get(
                'host-key-checking', True))
            self._pools[pp.name] = pp
            pp.labels = {}

            for label in pool.get('labels', []):
                pl = AzureLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl

                cloud_image_name = label['cloud-image']
                if cloud_image_name:
                    cloud_image = self.cloud_images.get(
                        cloud_image_name, None)
                    if not cloud_image:
                        raise ValueError(
                            "cloud-image %s does not exist in provider %s"
                            " but is referenced in label %s" %
                            (cloud_image_name, self.name, pl.name))
                    # pl.imageReference = cloud_image['image-reference']
                    pl.cloud_image = cloud_image
                    # pl.username = cloud_image.get('username', 'zuul')
                else:
                    # pl.imageReference = None
                    pl.cloud_image = None
                    # pl.username = 'zuul'

                pl.hardware_profile = label['hardware-profile']

                config.labels[label['name']].pools.append(pp)
                pl.tags = label.get('tags', {})

    def getSchema(self):

        azure_image_reference = {
            v.Required('sku'): str,
            v.Required('publisher'): str,
            v.Required('version'): str,
            v.Required('offer'): str,
        }

        azure_hardware_profile = {
            v.Required('vm-size'): str,
        }

        provider_cloud_images = {
            v.Required('name'): str,
            'username': str,
            v.Required('image-reference'): azure_image_reference,
        }

        azure_label = {
            v.Required('name'): str,
            v.Required('hardware-profile'): azure_hardware_profile,
            v.Required('cloud-image'): str,
            v.Optional('tags'): dict,
        }
        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [azure_label],
        })

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('zuul-public-key'): str,
            v.Required('pools'): [pool],
            v.Required('location'): str,
            v.Required('resource-group'): str,
            v.Required('resource-group-location'): str,
            v.Required('subnet-id'): str,
            v.Required('cloud-images'): [provider_cloud_images],
            v.Optional('auth-path'): str,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self._pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
