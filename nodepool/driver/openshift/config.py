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

import os
import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class OpenshiftLabel(ConfigValue):
    def __eq__(self, other):
        if other.name != self.name:
            return False
        return True


class OpenshiftPool(ConfigPool):
    def __eq__(self, other):
        if other.labels != self.labels:
            return False
        return True

    def __repr__(self):
        return "<OpenshiftPool %s>" % self.name


class OpenshiftProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if other.config_file != self.config_file:
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.config_file = os.path.expanduser(
            self.provider.get('config-file', '~/.kube/config'))
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = OpenshiftPool()
            pp.name = pool['name']
            pp.provider = self
            # TODO: check if this is needed?
            pp.max_servers = 42
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = OpenshiftLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        openshift_label = {
            v.Required('name'): str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [openshift_label],
        }

        provider = {
            'config-file': str,
            v.Required('pools'): [pool],
        }
        return v.Schema(provider)

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels.keys())
        return labels
