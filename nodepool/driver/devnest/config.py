# Copyright 2019 Red Hat
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
from nodepool.driver import ProviderConfig


class DevnestPool(ConfigPool):
    def __eq__(self, other):
        if isinstance(other, DevnestPool):
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.labels == self.labels)
        return False

    def __repr__(self):
        return "<DevnestPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.labels = {}
        for node in pool_config.get('labels', []):
            self.labels[node['name']] = node
            full_config.labels[node['name']].pools.append(self)


class DevnestProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.__pools = {}
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, DevnestProviderConfig):
            return (super().__eq__(other) and
                    other.manage_images == self.manage_images and
                    other.pools == self.pools)
        return False

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.launch_retries = int(self.provider.get('launch-retries', 3))
        for pool in self.provider.get('pools', []):
            pp = DevnestPool()
            pp.load(pool, config)
            pp.provider = self
            self.pools[pp.name] = pp

    def getSchema(self):
        pool_label = {
            v.Required('name'): str,
            v.Required('group'): str,
        }
        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            'name': str,
            'labels': [pool_label],
        })
        schema = ProviderConfig.getCommonSchemaDict()
        schema.update({'pools': [pool]})
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels)
        return labels
