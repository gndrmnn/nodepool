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

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class IBMVPCProviderCloudImage(ConfigValue):
    def __init__(self, image):
        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        self.name = image['name']
        self.username = image['username']
        self.key = None
        self.keys = image.get('keys')
        self.image_href = image.get('image-href')
        self.image_id = image.get('image-id')
        self.image_name = image.get('image-name')
        self.image_filter = image.get('image-filter')
        self.python_path = image.get('python-path', 'auto')
        self.shell_type = image.get('shell-type')
        self.connection_type = image.get('connection-type', 'ssh')
        self.connection_port = image.get(
            'connection-port',
            default_port_mapping.get(self.connection_type, 22))

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return (self.image_name or self.image_href or
                self.image_id or self.name)

    @staticmethod
    def getSchema():
        image_filter = {
            'operating-system': {
                'architecture': str,
                'dedicated-host-only': bool,
                'display-name': str,
                'family': str,
                'href': str,
                'name': str,
                'vendor': str,
                'version': str,
            },
            'owner-type': str,
            'status': str,
        }

        return v.All({
            v.Required('name'): str,
            v.Required('username'): str,
            'keys': [str],
            v.Exclusive('image-href', 'spec'): str,
            v.Exclusive('image-id', 'spec'): str,
            v.Exclusive('image-name', 'spec'): str,
            v.Exclusive('image-filter', 'spec'): image_filter,
            'connection-type': str,
            'connection-port': int,
            'python-path': str,
            'shell-type': str,
        }, {
            v.Required(
                v.Any('image-href', 'image-id', 'image-name', 'image-filter'),
                msg=('Provide either "image-filter", "image-href", '
                     '"image-id", or "image-name" keys')
            ): object,
            object: object,
        })


class IBMVPCProviderDiskImage(ConfigValue):
    def __init__(self, image, diskimage, image_type):
        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        self.name = image['name']
        diskimage.image_types.add(image_type)
        self.pause = bool(image.get('pause', False))
        self.python_path = image.get('python-path', 'auto')
        self.shell_type = image.get('shell-type')
        self.username = diskimage.username
        self.connection_type = image.get('connection-type', 'ssh')
        self.connection_port = image.get(
            'connection-port',
            default_port_mapping.get(self.connection_type, 22))
        self.meta = {}
        self.operating_system = image['operating-system']

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.name

    @staticmethod
    def getSchema():
        return {
            v.Required('name'): str,
            v.Required('operating-system'): str,
            'pause': bool,
            'connection-type': str,
            'connection-port': int,
            'python-path': str,
            'shell-type': str,
        }


class IBMVPCLabel(ConfigValue):
    ignore_equality = ['pool']

    def __init__(self, label, provider_config, provider_pool):
        self.profile = None

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

        self.profile = label['profile']
        self.user_data = label.get('user-data', None)

    @staticmethod
    def getSchema():
        return {
            v.Required('name'): str,
            'cloud-image': str,
            'diskimage': str,
            v.Required('profile'): str,
            'user-data': str,
        }


class IBMVPCPool(ConfigPool):
    ignore_equality = ['provider']

    def __init__(self, provider_config, pool_config):
        super().__init__()
        self.provider = provider_config
        self.load(pool_config)

    def load(self, pool_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.max_servers = pool_config['max-servers']
        self.public_ipv4 = pool_config.get('public-ipv4',
                                           self.provider.public_ipv4)
        self.use_internal_ip = pool_config.get(
            'use-internal-ip', self.provider.use_internal_ip)
        self.host_key_checking = pool_config.get(
            'host-key-checking', self.provider.host_key_checking)
        self.zone = pool_config.get(
            'zone', self.provider.zone)
        self.subnet = pool_config.get(
            'subnet', self.provider.subnet)
        if not self.zone:
            raise Exception("Zone is required in provider or pool config")
        if not self.subnet:
            raise Exception("Subnet is required in provider or pool config")

    @staticmethod
    def getSchema():
        ibmvpc_label = IBMVPCLabel.getSchema()

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): v.Match('[^_]+'),
            v.Required('labels'): [ibmvpc_label],
            'zone': str,
            'subnet': str,
            'public-ipv4': bool,
            'use-internal-ip': bool,
            'host-key-checking': bool,
        })
        return pool


class IBMVPCProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        super().__init__(provider)
        self._pools = {}
        self.rate = None
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
        self.image_type = self.provider.get('image-format', 'qcow2')
        self.image_name_format = 'npimage{upload_id}'
        self.post_upload_hook = self.provider.get('post-upload-hook')

        self.rate = self.provider.get('rate', 1)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.launch_timeout = self.provider.get('launch-timeout', 3600)
        self.boot_timeout = self.provider.get('boot-timeout', 120)

        self.vpc = self.provider['vpc']
        self.region = self.provider['region']
        self.zone = self.provider.get('zone')
        self.subnet = self.provider.get('subnet')
        self.quota = self.provider.get('quota', {})
        # Don't use these directly; these are default values for
        # labels.
        self.public_ipv4 = self.provider.get('public-ipv4', False)
        self.use_internal_ip = self.provider.get('use-internal-ip', False)
        self.host_key_checking = self.provider.get('host-key-checking', True)
        self.credentials_file = self.provider.get('credentials-file')
        self.object_storage = self.provider.get('object-storage')

        self.cloud_images = {}
        for image in self.provider.get('cloud-images', []):
            i = IBMVPCProviderCloudImage(image)
            self.cloud_images[i.name] = i

        self.diskimages = {}
        for image in self.provider.get('diskimages', []):
            diskimage = config.diskimages[image['name']]
            i = IBMVPCProviderDiskImage(image, diskimage, self.image_type)
            self.diskimages[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = IBMVPCPool(self, pool)
            self._pools[pp.name] = pp

            for label in pool.get('labels', []):
                pl = IBMVPCLabel(label, self, pp)
                pp.labels[pl.name] = pl
                config.labels[pl.name].pools.append(pp)

    def getSchema(self):
        provider_cloud_images = IBMVPCProviderCloudImage.getSchema()
        provider_diskimages = IBMVPCProviderDiskImage.getSchema()
        object_storage = {
            v.Required('instance-name'): str,
            v.Required('endpoint'): str,
            v.Required('bucket-name'): str,
        }

        pool = IBMVPCPool.getSchema()

        quota = {
            'instances': int,
            'cores': int,
            'ram': int,
        }

        provider = {
            # Normally provided by superclass; we override to add the
            # underscore restriction
            v.Required('name'): v.Match(r'^[^_]+$'),
            'driver': str,
            'max-concurrency': int,
            # For this class
            v.Required('pools'): [pool],
            v.Required('vpc'): str,
            v.Required('region'): str,
            'zone': str,
            'subnet': str,
            'cloud-images': [provider_cloud_images],
            'diskimages': [provider_diskimages],
            'quota': quota,
            'public-ipv4': bool,
            'use-internal-ip': bool,
            'host-key-checking': bool,
            'post-upload-hook': str,
            'rate': v.Coerce(float),
            'boot-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
            'credentials-file': str,
            'object-storage': object_storage,
        }
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self._pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
