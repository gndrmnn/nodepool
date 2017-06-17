# Copyright 2017 Red Hat
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

from nodepool.config import as_list
from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class RuncLabel(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.python_path != self.python_path or
            other.homedir != self.homedir or
            other.path != self.path):
            return False
        return True


class RuncPool(ConfigPool):
    def __eq__(self, other):
        if isinstance(other, RuncPool):
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.labels == self.labels)
        if (other.labels != self.labels and
            other.max_servers != self.max_servers):
            return False
        return True

    def __repr__(self):
        return "<RuncPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.hostkeys = as_list(pool_config.get('host-key', []))
        self.labels = {}
        for label in pool_config.get('labels', []):
            pl = RuncLabel()
            pl.name = label['name']
            pl.pool = self
            self.labels[pl.name] = pl
            pl.username = label.get('username', 'zuul')
            pl.python_path = label.get('python-path', '/usr/bin/python2')
            pl.homedir = label.get('home-dir', '/home/%s' % pl.username)
            pl.path = label.get('path', '/')
            full_config.labels[label['name']].pools.append(self)


class RuncProviderConfig(ProviderConfig):
    def __init__(self, *args, **kwargs):
        self.__pools = {}
        super().__init__(*args, **kwargs)

    def __eq__(self, other):
        if isinstance(other, RuncProviderConfig):
            return (super().__eq__(other) and
                    other.manage_images == self.manage_images and
                    other.pools == self.pools)
        return False

    @staticmethod
    def reset():
        pass

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.zuul_console_dir = self.provider.get(
            'zuul-console-dir', '/tmp').rstrip('/')
        for pool in self.provider.get('pools', []):
            pp = RuncPool()
            pp.load(pool, config)
            pp.provider = self
            self.pools[pp.name] = pp

    def getSchema(self):
        runc_label = {
            v.Required('name'): str,
            'path': str,
            'username': str,
            'python-path': str,
            'home-dir': str,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            'host-key': v.Any(str, [str]),
            v.Required('labels'): [runc_label],
        })

        schema = ProviderConfig.getCommonSchemaDict()
        schema.update({
            v.Required('pools'): [pool],
            'zuul-console-dir': str,
        })
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
