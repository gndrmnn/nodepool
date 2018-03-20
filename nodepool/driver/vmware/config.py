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


class VmwareLabel(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.template != self.template or
            other.num_cpu != self.num_cpu or
            other.memory_mb != self.memory_mb):
            return False
        return True


class VmwarePool(ConfigPool):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<VmwarePool %s>" % self.name


class VmwareProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if (other.pools != self.pools or
            other.resource_pool != self.resource_pool):
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.resource_pool = self.provider['resource_pool']
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = VmwarePool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = VmwareLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.template = label['template']
                pl.num_cpu = label['num_cpu']
                pl.memory_mb = label['memory_mb']
                pl.username = label.get('username', 'zuul')
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        vmware_label = {
            v.Required('name'): str,
            v.Required('template'): str,
            v.Required('num_cpu'): int,
            v.Required('memory_mb'): int,
            'username': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [vmware_label],
            v.Required('max-servers'): int,
        }

        provider = {
            v.Required('pools'): [pool],
            v.Required('resource_pool'): str,
        }
        return v.Schema(provider)

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels.keys())
        return labels
