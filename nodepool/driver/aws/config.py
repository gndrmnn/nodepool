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


class EC2Label(ConfigValue):
    def __eq__(self, other):
        if (other.username != self.username or
            other.ami != self.ami or
            other.flavor != self.flavor):
            return False
        return True


class EC2Pool(ConfigPool):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<EC2Pool %s>" % self.name


class EC2ProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if (other.region != self.region or
            other.pools != self.pools):
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.zuul_public_key = self.provider['zuul-public-key']
        self.region = self.provider['region']
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = EC2Pool()
            pp.name = pool['name']
            pp.provider = self
            pp.max_servers = pool['max-servers']
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = EC2Label()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.ami = label['ami']
                pl.flavor = label['flavor-name']
                pl.username = label.get('username', 'ec2-user')
                config.labels[label['name']].pools.append(pp)

    def get_schema(self):
        ec2_label = {
            v.Required('name'): str,
            v.Required('ami'): str,
            v.Required('flavor-name'): str,
            'username': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [ec2_label],
            v.Required('max-servers'): int,
        }

        provider = {
            v.Required('zuul-public-key'): str,
            v.Required('pools'): [pool],
            v.Required('region'): str,
        }
        return v.Schema(provider)
