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


class KubernetesLabel(ConfigValue):
    def __eq__(self, other):
        if isinstance(other, KubernetesLabel):
            return (other.name == self.name and
                    other.image_pull == self.image_pull and
                    other.image == self.image)
        return False

    def __repr__(self):
        return "<KubternetesLabel %s>" % self.name


class KubernetesPool(ConfigPool):
    def __eq__(self, other):
        if isinstance(other, KubernetesPool):
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.namespace == self.namespace and
                    other.labels == self.labels)
        return False

    def __repr__(self):
        return "<KubernetesPool %s>" % self.name


class KubernetesProviderConfig(ProviderConfig):
    def __init__(self, *args, **kwargs):
        self.__pools = {}
        super().__init__(*args, **kwargs)

    def __eq__(self, other):
        if isinstance(other, KubernetesProviderConfig):
            return (super().__eq__(other) and
                    other.context == self.context and
                    other.pools == self.pools and
                    other.zuul_public_key == self.public_key)
        return False

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.context = self.provider['context']
        self.zuul_public_key = self.provider['zuul-public-key']
        for pool in self.provider.get('pools', []):
            pp = KubernetesPool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp
            pp.namespace = pool['namespace']
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = KubernetesLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.image = label['image']
                pl.username = label.get('username', 'zuul-worker')
                pl.image_pull = label.get('image_pull', 'Always')
                config.labels[label['name']].pools.append(pp)

    def getSchema(self):
        k8s_label = {
            v.Required('name'): str,
            v.Required('image'): str,
            'image_pull': str,
            'username': str,
        }

        pool = {
            v.Required('name'): str,
            v.Required('namespace'): str,
            v.Required('labels'): [k8s_label],
        }

        provider = {
            v.Required('context'): str,
            v.Required('zuul-public-key'): str,
            v.Required('pools'): [pool],
        }
        return v.Schema(provider)

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels.keys())
        return labels
