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

import math

import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class MetastaticCloudImage(ConfigValue):
    def __init__(self):
        self.name = 'unused'
        self.username = 'unknown'
        self.python_path = 'unknown'
        self.shell_type = 'unknown'
        self.connection_port = 'unknown'
        self.connection_type = 'unknown'


class MetastaticLabel(ConfigValue):
    ignore_equality = ['pool']

    def __init__(self, label, provider_config, provider_pool):
        self.pool = provider_pool
        self.name = label['name']
        self.backing_label = label['backing-label']
        self.diskimage = None
        self.cloud_image = MetastaticCloudImage()
        self.max_parallel_jobs = label.get('max-parallel-jobs', 1)
        self.grace_time = label.get('grace-time', 60)

    @staticmethod
    def getSchema():
        return {
            v.Required('name'): str,
            v.Required('backing-label'): str,
            'max-parallel-jobs': int,
            'grace-time': int,
        }

    def isBackingConfigEqual(self, other):
        # An equality check of the backing configuration
        return (
            self.backing_label == other.backing_label and
            self.max_parallel_jobs == other.max_parallel_jobs and
            self.grace_time == other.grace_time
        )


class MetastaticPool(ConfigPool):
    ignore_equality = ['provider']

    def __init__(self, provider_config, pool_config):
        super().__init__()
        self.provider = provider_config
        self.labels = {}
        # We will just use the interface_ip of the backing node
        self.use_internal_ip = False
        self.host_key_checking = False

        self.load(pool_config)

    def load(self, pool_config):
        self.name = pool_config['name']
        self.max_servers = pool_config.get('max-servers', math.inf)
        for label in pool_config.get('labels', []):
            b = MetastaticLabel(label, self.provider, self)
            self.labels[b.name] = b

    @staticmethod
    def getSchema():
        label = MetastaticLabel.getSchema()

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [label],
            'max-servers': int,
        })
        return pool


class MetastaticProviderConfig(ProviderConfig):
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
        return False

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.rate = self.provider.get('rate', 1)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.launch_timeout = self.provider.get('launch-timeout', 3600)
        self.boot_timeout = self.provider.get('boot-timeout', 120)
        label_defs = {}
        for pool in self.provider.get('pools', []):
            pp = MetastaticPool(self, pool)
            self._pools[pp.name] = pp

            for label in pool.get('labels', []):
                pl = MetastaticLabel(label, self, pp)

                if pl.backing_label in label_defs:
                    if not pl.isBackingConfigEqual(
                            label_defs[pl.backing_label]):
                        raise Exception(
                            "Multiple label definitions for the same "
                            "backing label must be identical")
                label_defs[pl.backing_label] = pl
                config.labels[pl.name].pools.append(pp)

    def getSchema(self):
        pool = MetastaticPool.getSchema()

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self._pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels

    def _getLabel(self, label):
        for pool in self._pools.values():
            if label in pool.labels:
                return pool.labels[label]
