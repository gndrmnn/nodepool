# Copyright 2018 Red Hat
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


class ProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.image_id = None
        self.username = None
        self.connection_type = None
        self.connection_port = None

    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.name


class ProviderLabel(ConfigValue):
    ignore_equality = ['pool']

    def __init__(self):
        self.name = None
        self.cloud_image = None
        self.ebs_optimized = None
        self.instance_type = None
        self.key_name = None
        self.volume_size = None
        self.volume_type = None
        self.userdata = None
        self.iam_instance_profile = None
        # The ProviderPool object that owns this label.
        self.pool = None
        self.tags = None

    def __repr__(self):
        return "<ProviderLabel %s>" % self.name


class ProviderPool(ConfigPool):
    ignore_equality = ['provider']

    def __init__(self):
        self.name = None
        self.max_cores = None
        self.max_ram = None
        self.subnet_id = None
        self.security_group_id = None
        self.public_ip = True
        self.host_key_checking = True
        self.labels = None
        # The ProviderConfig object that owns this pool.
        self.provider = None

        # Initialize base class attributes
        super().__init__()

    def load(self, pool_config, full_config, provider):
        super().load(pool_config)
        self.name = pool_config['name']
        self.provider = provider

        self.security_group_id = pool_config.get('security-group-id')
        self.subnet_id = pool_config.get('subnet-id')
        self.host_key_checking = bool(
            pool_config.get('host-key-checking', True))
        self.public_ip = bool(pool_config.get('public-ip-address', True))

        for label in pool_config.get('labels', []):
            pl = ProviderLabel()
            pl.name = label['name']
            pl.pool = self
            self.labels[pl.name] = pl
            cloud_image_name = label.get('cloud-image', None)
            if cloud_image_name:
                cloud_image = self.provider.cloud_images.get(
                    cloud_image_name, None)
                if not cloud_image:
                    raise ValueError(
                        "cloud-image %s does not exist in provider %s"
                        " but is referenced in label %s" %
                        (cloud_image_name, self.name, pl.name))
            else:
                cloud_image = None
            pl.cloud_image = cloud_image
            pl.ebs_optimized = bool(label.get('ebs-optimized', False))
            pl.instance_type = label['instance-type']
            pl.key_name = label['key-name']
            pl.volume_type = label.get('volume-type')
            pl.volume_size = label.get('volume-size')
            pl.userdata = label.get('userdata', None)
            pl.iam_instance_profile = label.get('iam-instance-profile', None)
            pl.tags = [
                {
                    "Key": k,
                    "Value": str(v)
                } for k, v in label.get('tags', {}).items()
            ]
            full_config.labels[label['name']].pools.append(self)

    def __repr__(self):
        return "<ProviderPool %s>" % self.name


class AwsProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        self.profile_name = None
        self.region_name = None
        self.boot_timeout = None
        self.launch_retries = None
        self.cloud_images = {}
        super().__init__(provider)

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        # Currently we have no image management for AWS. This should
        # be updated if that changes.
        return False

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.profile_name = self.provider.get('profile-name')
        self.region_name = self.provider.get('region-name')
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_retries = self.provider.get('launch-retries', 3)

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        # TODO: diskimages

        for image in self.provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.image_id = image.get('image-id', None)

            image_filters = image.get("image-filters", None)
            if image_filters is not None:
                # ensure 'name' and 'values' keys are capitalized for boto
                def capitalize_keys(image_filter):
                    return {
                        k.capitalize(): v for (k, v) in image_filter.items()
                    }

                image_filters = [capitalize_keys(f) for f in image_filters]
            i.image_filters = image_filters

            i.username = image.get('username', None)
            i.python_path = image.get('python-path', 'auto')
            i.shell_type = image.get('shell-type', None)
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))
            self.cloud_images[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = ProviderPool()
            pp.load(pool, config, self)
            self.pools[pp.name] = pp

    def getSchema(self):
        pool_label = {
            v.Required('name'): str,
            v.Exclusive('cloud-image', 'label-image'): str,
            v.Required('instance-type'): str,
            v.Required('key-name'): str,
            'ebs-optimized': bool,
            'volume-type': str,
            'volume-size': int,
            'userdata': str,
            'iam-instance-profile': {
                v.Exclusive('name', 'iam_instance_profile_id'): str,
                v.Exclusive('arn', 'iam_instance_profile_id'): str
            },
            'tags': dict,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [pool_label],
            'host-key-checking': bool,
            'security-group-id': str,
            'subnet-id': str,
            'public-ip-address': bool,
        })

        image_filters = {
            v.Any('Name', 'name'): str,
            v.Any('Values', 'values'): [str]
        }

        provider_cloud_images = {
            'name': str,
            'connection-type': str,
            'connection-port': int,
            'shell-type': str,
            'image-id': str,
            "image-filters": [image_filters],
            'username': str,
            'python-path': str,
        }

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('region-name'): str,
            'profile-name': str,
            'cloud-images': [provider_cloud_images],
            'hostname-format': str,
            'boot-timeout': int,
            'launch-retries': int,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
