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

from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class OpenContainerPool(ConfigValue):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<OpenContainerPool %s>" % self.name


class OpenContainerProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if other.hypervisor != self.hypervisor:
            return False
        return True

    def load(self, config):
        self.hypervisor = self.provider['hypervisor']
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = OpenContainerPool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pp.labels[label['name']] = label.get('path', '/')
                config.labels[label['name']].pools.append(pp)

    def get_schema(self):
        oci_label = {
            v.Required('name'): str,
            'path': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('max-servers'): int,
            v.Required('labels'): [oci_label],
        }

        provider = {
            v.Required('hypervisor'): str,
            v.Required('pools'): [pool],
        }
        return v.Schema(provider)
