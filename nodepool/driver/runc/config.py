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
        self.hypervisor = self.provider.get('hypervisor')
        for pool in self.provider.get('pools', []):
            pp = RuncPool()
            pp.name = pool['name']
            pp.hostkeys = as_list(pool.get('host-key', []))
            pp.provider = self
            pp.max_servers = int(pool.get('max-servers', 100))
            self.__pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = RuncLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.username = label.get('username', 'zuul')
                pl.homedir = label.get('home-dir', '/home/%s' % pl.username)
                pl.path = label.get('path', '/')
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        runc_label = {
            v.Required('name'): str,
            'path': str,
            'username': str,
            'home-dir': str,
        }

        pool = {
            v.Required('name'): str,
            'max-servers': int,
            'host-key': v.Any(str, [str]),
            v.Required('labels'): [runc_label],
        }

        provider = {
            # Backward compatibility support
            'hypervisor': str,
            v.Required('pools'): [pool],
        }
        return v.Schema(provider)

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels.keys())
        return labels
