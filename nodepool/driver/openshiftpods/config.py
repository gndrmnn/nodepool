# Copyright 2018 Red Hat
# Copyright 2023 Acme Gating, LLC
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

from collections import defaultdict
import math

import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver.openshift.config import OpenshiftPool
from nodepool.driver.openshift.config import OpenshiftProviderConfig


class OpenshiftPodsProviderConfig(OpenshiftProviderConfig):
    def __eq__(self, other):
        if isinstance(other, OpenshiftPodsProviderConfig):
            return (super().__eq__(other) and
                    other.context == self.context and
                    other.pools == self.pools)
        return False

    def load(self, config):
        self.launch_retries = int(self.provider.get('launch-retries', 3))
        self.context = self.provider['context']
        # We translate max-projects to max_servers to re-use quota
        # calculation methods.
        self.max_servers = self.provider.get(
            'max-projects',
            self.provider.get('max-servers', math.inf))
        self.max_cores = self.provider.get('max-cores', math.inf)
        self.max_ram = self.provider.get('max-ram', math.inf)
        self.max_resources = defaultdict(lambda: math.inf)
        for k, val in self.provider.get('max-resources', {}).items():
            self.max_resources[k] = val
        for pool in self.provider.get('pools', []):
            # Force label type to be pod
            for label in pool.get('labels', []):
                label['type'] = 'pod'
            pp = OpenshiftPool()
            pp.provider = self
            pp.load(pool, config)
            self.pools[pp.name] = pp

    def getSchema(self):
        env_var = {
            v.Required('name'): str,
            v.Required('value'): str,
        }

        openshift_label = {
            v.Required('name'): str,
            v.Required('image'): str,
            'image-pull': str,
            'image-pull-secrets': list,
            'cpu': int,
            'memory': int,
            'storage': int,
            'cpu-limit': int,
            'memory-limit': int,
            'storage-limit': int,
            'gpu': str,
            'gpu-resource': str,
            'python-path': str,
            'shell-type': str,
            'env': [env_var],
            'node-selector': dict,
            'privileged': bool,
            'scheduler-name': str,
            'volumes': list,
            'volume-mounts': list,
            'labels': dict,
            'annotations': dict,
            'extra-resources': {str: int},
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [openshift_label],
            'max-cores': int,
            'max-ram': int,
            'max-resources': {str: int},
            'default-label-cpu': int,
            'default-label-memory': int,
            'default-label-storage': int,
            'default-label-cpu-limit': int,
            'default-label-memory-limit': int,
            'default-label-storage-limit': int,
            'default-label-extra-resources': {str: int},
        })

        schema = OpenshiftProviderConfig.getCommonSchemaDict()
        schema.update({
            v.Required('pools'): [pool],
            v.Required('context'): str,
            'launch-retries': int,
            'max-pods': int,
            'max-cores': int,
            'max-ram': int,
            'max-resources': {str: int},
        })
        return v.Schema(schema)
