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

from nodepool.driver import ConfigPool, ConfigValue
from nodepool.driver import ProviderConfig
from nodepool.config import as_list


class AnsiblePool(ConfigPool):
    def __eq__(self, other):
        if (other.labels != self.labels or
            other.inventory != self.inventory):
            return False
        return True

    def __repr__(self):
        return "<AnsiblePool %s>" % self.name


class AnsibleLabel(ConfigValue):
    def __eq__(self, other):
        if (other.playbooks_path != self.playbooks_path or
            other.extra_vars != self.extra_vars):
            return False
        return True

    def __repr__(self):
        return "<AnsibleLabel %s>" % self.name

class AnsibleProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if (other.pools != self.pools or
            other.provider_root_path != self.provider_root_path or
            other.enable_vault != self.enable_vault):
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.provider_root_path = self.provider.get('provider-root-path',
                                                    '/var/lib/nodepool/driver/ansible')

        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = AnsiblePool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp

            pp.labels = set()
            for label in pool.get('labels', []):
                pl = AnsibleLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.playbooks_path = label.get(
                    'playbooks-path',
                    '{}/{}/playbooks'.format(self.provider_root_path, pl.name)
                )
                pl.username = label.get('username', 'zuul')
                pl.connection_type = label.get('connection-type', 'ssh')
                pl.connection_port = int(label.get('connection-port', 22))
                pl.inventory = label.get(
                    'inventory',
                    '{}/{}/inventory'.format(self.provider_root_path, pl.name)
                )
                pl.enable_vault = label.get('enable-vault', None)
                pl.extra_vars = label.get('extra-vars', None)
                config.labels[pl.name].append(pp)
                 
    def get_schema(self):
        pool_label = {
            v.Required('name'): str,
            'playbooks-path': str,
            'username': str,
            'connection-type': str,
            'connection-port': int,
            'inventory': str,
            'enable-vault': bool,
            'extra-vars': dict,
        }
        pool = {
            'name': str,
            'labels': [pool_label],
        }
        return v.Schema({'pools': [pool]})
