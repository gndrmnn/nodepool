# Copyright 2018-2019 Red Hat
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

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class GceProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.image_id = None
        self.username = None
        self.key = None
        self.python_path = None
        self.connection_type = None
        self.connection_port = None
        self.shell_type = None

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.name


class GceLabel(ConfigValue):
    ignore_equality = ['pool']

    def __init__(self, label, provider_config, provider_pool):
        self.name = label['name']
        self.pool = provider_pool

        cloud_image_name = label.get('cloud-image', None)
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
        self.instance_type = label['instance-type']
        self.volume_type = label.get('volume-type', 'pd-standard')
        self.volume_size = label.get('volume-size', '10')
        self.diskimage = None


class GcePool(ConfigPool):
    ignore_equality = ['provider']

    def __init__(self, provider_config, pool_config):
        super().__init__()
        self.provider = provider_config
        self.load(pool_config)

    def load(self, pool_config):
        super().load(pool_config)
        self.name = pool_config['name']

        self.host_key_checking = bool(
            pool_config.get('host-key-checking', True))
        self.use_internal_ip = bool(
            pool_config.get('use-internal-ip', False))


class GceProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        super().__init__(provider)
        self._pools = {}
        self.rate = None
        self.region = None
        self.boot_timeout = None
        self.launch_retries = None
        self.project = None
        self.zone = None
        self.cloud_images = {}
        self.rate_limit = None

    @property
    def pools(self):
        return self._pools

    @property
    def manage_images(self):
        # Currently we have no image management for google. This should
        # be updated if that changes.
        return False

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.rate = self.provider.get('rate', 2)
        self.region = self.provider.get('region')
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.launch_timeout = self.provider.get('launch-timeout', 3600)
        self.project = self.provider.get('project')
        self.zone = self.provider.get('zone')
        self.rate_limit = self.provider.get('rate-limit', 1)

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        # TODO: diskimages

        for image in self.provider.get('cloud-images', []):
            i = GceProviderCloudImage()
            i.name = image['name']
            i.image_id = image.get('image-id', None)
            i.image_project = image.get('image-project', None)
            i.image_family = image.get('image-family', None)
            i.username = image.get('username', None)
            i.key = image.get('key', None)
            i.python_path = image.get('python-path', 'auto')
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))
            i.shell_type = image.get('shell-type', None)
            self.cloud_images[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = GcePool(self, pool)
            self._pools[pp.name] = pp

            for label in pool.get('labels', []):
                pl = GceLabel(label, self, pp)
                pp.labels[pl.name] = pl
                config.labels[pl.name].pools.append(pp)

    def getSchema(self):
        pool_label = {
            v.Required('name'): str,
            v.Required('cloud-image'): str,
            v.Required('instance-type'): str,
            'volume-type': str,
            'volume-size': int
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [pool_label],
            'use-internal-ip': bool,
        })

        provider_cloud_images = {
            'name': str,
            'connection-type': str,
            'connection-port': int,
            'shell-type': str,
            'image-id': str,
            'image-project': str,
            'image-family': str,
            'username': str,
            'key': str,
            'python-path': str,
        }

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('region'): str,
            v.Required('project'): str,
            v.Required('zone'): str,
            'cloud-images': [provider_cloud_images],
            'boot-timeout': int,
            'launch-retries': int,
            'rate-limit': int,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
