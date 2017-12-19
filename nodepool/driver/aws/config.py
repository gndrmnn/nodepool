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


class AwsLabel(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.ami != self.ami or
            other.flavor != self.flavor):
            return False
        return True


class AwsPool(ConfigPool):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<AwsPool %s>" % self.name


class AwsProviderConfig(ProviderConfig):
    def __init__(self, *args, **kwargs):
        self.__pools = {}
        super().__init__(*args, **kwargs)

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return True

    def __eq__(self, other):
        if (other.region != self.region or
            other.pools != self.pools):
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.region = self.provider['region']
        for pool in self.provider.get('pools', []):
            pp = AwsPool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            pp.security_group = pool.get('security-group-id')
            pp.subnet = pool.get('subnet-id')
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = AwsLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.ami = label['ami']
                pl.flavor = label['flavor-name']
                pl.key_name = label['key-name']
                pl.username = label.get('username', 'ec2-user')
                pl.volume_type = label.get('volume_type')
                pl.volume_size = label.get('volume_size')
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        ec2_label = {
            v.Required('name'): str,
            v.Required('ami'): str,
            v.Required('flavor-name'): str,
            v.Required('key-name'): str,
            'username': str,
            'volume_type': str,
            'volume_size': int
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [ec2_label],
            v.Required('max-servers'): int,
            'security-group-id': str,
            'subnet-id': str,
        }

        provider = {
            v.Required('pools'): [pool],
            v.Required('region'): str,
        }
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels.keys())
        return labels
