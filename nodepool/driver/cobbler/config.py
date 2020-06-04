# Copyright 2020 AMD
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


class CobblerPool(ConfigPool):
    ignore_equality = ['provider']

    def __repr__(self):
        return "<CobblerPool %s>" % self.name

    def load(self, pool_config, full_config, provider):
        super().load(pool_config)
        self.name = pool_config['name']
        self.provider = provider
        self.labels = {}

        for label in pool_config.get('labels', []):
            self.labels[label] = label
            full_config.labels[label].pools.append(self)

class CobblerProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        self.api_server_username = None
        self.api_server_password = None
        self.token_keepalive = None
        self.rate_limit = None
        self.boot_timeout = None
        self.launch_retries = None
        super().__init__(provider)

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        # Currently we have no image management for Cobbler.
        return False

    def load(self, config):
        self.rate_limit = self.provider.get('rate-limit', 1)
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.api_server_username = self.provider.get('api-server-username')
        self.api_server_password = self.provider.get('api-server-password')
        self.token_keepalive = self.provider.get('token-keepalive', 1200)

        for pool in self.provider.get('pools', []):
            pp = CobblerPool()
            pp.load(pool, config, self)
            self.pools[pp.name] = pp

    def getSchema(self):
        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [str],
        })

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('api-server-username'): str,
            v.Required('api-server-password'): str,
            v.Optional('token-keepalive'): int,
            v.Required('pools'): [pool],
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
