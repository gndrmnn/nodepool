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

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class OpenshiftLabel(ConfigValue):
    ignore_equality = ['pool']

    def __repr__(self):
        return "<OpenshiftLabel %s>" % self.name


class OpenshiftPool(ConfigPool):
    ignore_equality = ['provider']

    def __repr__(self):
        return "<OpenshiftPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.default_label_cpu = pool_config.get('default-label-cpu')
        self.default_label_memory = pool_config.get('default-label-memory')
        self.default_label_storage = pool_config.get('default-label-storage')
        self.labels = {}
        for label in pool_config.get('labels', []):
            pl = OpenshiftLabel()
            pl.name = label['name']
            pl.type = label['type']
            pl.image = label.get('image')
            pl.image_pull = label.get('image-pull', 'IfNotPresent')
            pl.image_pull_secrets = label.get('image-pull-secrets', [])
            pl.cpu = label.get('cpu', self.default_label_cpu)
            pl.memory = label.get('memory', self.default_label_memory)
            pl.storage = label.get('storage', self.default_label_storage)
            # The limits are the first of:
            # 1) label specific configured limit
            # 2) default label configured limit
            # 3) label specific configured request
            # 4) default label configured default
            # 5) None
            default_cpu_limit = pool_config.get(
                'default-label-cpu-limit', pl.cpu)
            default_memory_limit = pool_config.get(
                'default-label-memory-limit', pl.memory)
            default_storage_limit = pool_config.get(
                'default-label-storage-limit', pl.storage)
            pl.cpu_limit = label.get(
                'cpu-limit', default_cpu_limit)
            pl.memory_limit = label.get(
                'memory-limit', default_memory_limit)
            pl.storage_limit = label.get(
                'storage-limit', default_storage_limit)
            pl.python_path = label.get('python-path', 'auto')
            pl.shell_type = label.get('shell-type')
            pl.env = label.get('env', [])
            pl.node_selector = label.get('node-selector')
            pl.privileged = label.get('privileged')
            pl.scheduler_name = label.get('scheduler-name')
            pl.volumes = label.get('volumes')
            pl.volume_mounts = label.get('volume-mounts')
            pl.labels = label.get('labels')
            pl.pool = self
            self.labels[pl.name] = pl
            full_config.labels[label['name']].pools.append(self)


class OpenshiftProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        super().__init__(provider)

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.launch_retries = int(self.provider.get('launch-retries', 3))
        self.context = self.provider['context']
        self.max_projects = self.provider.get('max-projects', math.inf)
        for pool in self.provider.get('pools', []):
            pp = OpenshiftPool()
            pp.load(pool, config)
            pp.provider = self
            self.pools[pp.name] = pp

    def getSchema(self):
        env_var = {
            v.Required('name'): str,
            v.Required('value'): str,
        }

        openshift_label = {
            v.Required('name'): str,
            v.Required('type'): str,
            'image': str,
            'image-pull': str,
            'image-pull-secrets': list,
            'cpu': int,
            'memory': int,
            'storage': int,
            'cpu-limit': int,
            'memory-limit': int,
            'storage-limit': int,
            'python-path': str,
            'shell-type': str,
            'env': [env_var],
            'node-selector': dict,
            'privileged': bool,
            'scheduler-name': str,
            'volumes': list,
            'volume-mounts': list,
            'labels': dict,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [openshift_label],
            v.Optional('default-label-cpu'): int,
            v.Optional('default-label-memory'): int,
            v.Optional('default-label-storage'): int,
            v.Optional('default-label-cpu-limit'): int,
            v.Optional('default-label-memory-limit'): int,
            v.Optional('default-label-storage-limit'): int,
        })

        schema = ProviderConfig.getCommonSchemaDict()
        schema.update({
            v.Required('pools'): [pool],
            v.Required('context'): str,
            'launch-retries': int,
            'max-projects': int,
        })
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
