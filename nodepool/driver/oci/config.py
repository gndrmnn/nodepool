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

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class OpenContainerLabel(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.homedir != self.homedir or
            other.path != self.path):
            return False
        return True


class OpenContainerPool(ConfigPool):
    def __eq__(self, other):
        if (other.labels != self.labels and
            other.max_servers != self.max_servers):
            return False
        return True

    def __repr__(self):
        return "<OpenContainerPool %s>" % self.name


class OpenContainerProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if other.hypervisor != self.hypervisor:
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.hypervisor = self.provider['hypervisor']
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = OpenContainerPool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool.get('max-servers')
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = OpenContainerLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.username = label.get('username', 'zuul')
                pl.homedir = label.get('home-dir', '/home/%s' % pl.username)
                pl.path = label.get('path', '/')
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        oci_label = {
            v.Required('name'): str,
            'path': str,
            'username': str,
            'home-dir': str,
        }

        pool = {
            v.Required('name'): str,
            'max-servers': int,
            v.Required('labels'): [oci_label],
        }

        provider = {
            v.Required('hypervisor'): str,
            v.Required('pools'): [pool],
        }
        return v.Schema(provider)

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pool.values():
            labels.update(pool.labels.keys())
        return labels
