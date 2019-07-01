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
import os

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class AzureLabel(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.imageReference != self.imageReference or
            other.hardwareProfile != self.hardwareProfile):
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
        self.zuul_public_key = self.provider['zuul-public-key']
        self.location = self.provider['location']
        self.subnet_id = self.provider['subnet_id']
        self.ipv6 = self.provider.get('ipv6', False)
        self.resource_group = self.provider['resource_group']
        self.resource_group_location = self.provider['resource_group_location']
        self.auth_path = self.provider.get(
            'auth_path', os.getenv('AZURE_AUTH_LOCATION', None))
        for pool in self.provider.get('pools', []):
            pp = AzurePool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            self._pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = AzureLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.imageReference = label['imageReference']
                pl.hardwareProfile = label['hardwareProfile']
                pl.username = label.get('username', 'zuul')
                config.labels[label['name']].pools.append(pp)
                pl.tags = label['tags']

    def getSchema(self):
        azure_label = {
            v.Required('name'): str,
            v.Required('imageReference'): dict,
            v.Required('hardwareProfile'): dict,
            'username': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [azure_label],
            v.Required('max-servers'): int,
        }

        provider = {
            v.Required('zuul-public-key'): str,
            v.Required('pools'): [pool],
            v.Required('location'): str,
            v.Required('resource_group'): str,
            v.Required('resource_group_location'): str,
            v.Required('subnet_id'): str,
        }
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self._pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
