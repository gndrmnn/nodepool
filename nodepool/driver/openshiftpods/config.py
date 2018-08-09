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

import math
import voluptuous as v

from nodepool.driver import ProviderConfig

from nodepool.driver.openshift.config import OpenshiftLabel
from nodepool.driver.openshift.config import OpenshiftPool


class OpenshiftPodsProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, OpenshiftPodsProviderConfig):
            return (super().__eq__(other) and
                    other.context == self.context and
                    other.pools == self.pools)
        return False

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.context = self.provider['context']
        self.max_pods = self.provider.get('max-pods', math.inf)
        for pool in self.provider.get('pools', []):
            pp = OpenshiftPool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = OpenshiftLabel()
                pl.name = label['name']
                pl.type = "pod"
                pl.image = label['image']
                pl.image_pull = label.get('image-pull', 'IfNotPresent')
                pl.pool = pp
                pp.labels[pl.name] = pl
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        openshift_label = {
            v.Required('name'): str,
            v.Required('image'): str,
            'image-pull': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [openshift_label],
        }

        provider = {
            v.Required('pools'): [pool],
            v.Required('context'): str,
            'max-pods': int,
        }
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
