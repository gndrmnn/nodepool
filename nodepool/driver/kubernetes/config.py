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

import math
from collections import defaultdict

import voluptuous as v

from nodepool.driver import ConfigPool, ConfigValue, ProviderConfig


class KubernetesLabel(ConfigValue):
    ignore_equality = ["pool"]

    def __repr__(self):
        return "<KubternetesLabel %s>" % self.name


class KubernetesPool(ConfigPool):
    ignore_equality = ["provider"]

    def __repr__(self):
        return "<KubernetesPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config["name"]
        self.max_cores = pool_config.get("max-cores", self.provider.max_cores)
        self.max_ram = pool_config.get("max-ram", self.provider.max_ram)
        self.max_resources = self.provider.max_resources.copy()
        self.max_resources.update(pool_config.get("max-resources", {}))
        self.default_label_cpu = pool_config.get("default-label-cpu")
        self.default_label_memory = pool_config.get("default-label-memory")
        self.default_label_storage = pool_config.get("default-label-storage")
        self.default_label_extra_resources = pool_config.get(
            "default-label-extra_resources", {}
        )
        self.labels = {}
        for label in pool_config.get("labels", []):
            pl = KubernetesLabel()
            pl.name = label["name"]
            pl.type = label["type"]
            pl.spec = label.get("spec")
            pl.image = label.get("image")
            pl.image_pull = label.get("image-pull", "IfNotPresent")
            pl.python_path = label.get("python-path", "auto")
            pl.shell_type = label.get("shell-type")
            pl.cpu = label.get("cpu", self.default_label_cpu)
            pl.memory = label.get("memory", self.default_label_memory)
            pl.storage = label.get("storage", self.default_label_storage)
            pl.extra_resources = self.default_label_extra_resources.copy()
            pl.extra_resources.update(label.get("extra-resources", {}))
            # The limits are the first of:
            # 1) label specific configured limit
            # 2) default label configured limit
            # 3) label specific configured request
            # 4) default label configured request
            # 5) None
            default_cpu_limit = pool_config.get("default-label-cpu-limit", pl.cpu)
            default_memory_limit = pool_config.get(
                "default-label-memory-limit", pl.memory
            )
            default_storage_limit = pool_config.get(
                "default-label-storage-limit", pl.storage
            )
            pl.cpu_limit = label.get("cpu-limit", default_cpu_limit)
            pl.memory_limit = label.get("memory-limit", default_memory_limit)
            pl.storage_limit = label.get("storage-limit", default_storage_limit)
            pl.gpu = label.get("gpu")
            pl.gpu_resource = label.get("gpu-resource")
            pl.env = label.get("env", [])
            pl.node_selector = label.get("node-selector")
            pl.tolerations = label.get("tolerations")
            pl.privileged = label.get("privileged")
            pl.scheduler_name = label.get("scheduler-name")
            pl.volumes = label.get("volumes")
            pl.volume_mounts = label.get("volume-mounts")
            pl.labels = label.get("labels")
            pl.dynamic_labels = label.get("dynamic-labels", {})
            pl.annotations = label.get("annotations")
            pl.pool = self
            self.labels[pl.name] = pl
            full_config.labels[label["name"]].pools.append(self)


class KubernetesProviderConfig(ProviderConfig):
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
        self.launch_retries = int(self.provider.get("launch-retries", 3))
        self.context = self.provider.get("context")
        self.max_servers = self.provider.get("max-servers", math.inf)
        self.max_cores = self.provider.get("max-cores", math.inf)
        self.max_ram = self.provider.get("max-ram", math.inf)
        self.max_resources = defaultdict(lambda: math.inf)
        for k, val in self.provider.get("max-resources", {}).items():
            self.max_resources[k] = val
        for pool in self.provider.get("pools", []):
            pp = KubernetesPool()
            pp.provider = self
            pp.load(pool, config)
            self.pools[pp.name] = pp

    def getSchema(self):
        env_var = {
            v.Required("name"): str,
            v.Required("value"): str,
        }

        k8s_label_from_nodepool = {
            v.Required("name"): str,
            v.Required("type"): str,
            "image": str,
            "image-pull": str,
            "python-path": str,
            "shell-type": str,
            "cpu": int,
            "memory": int,
            "storage": int,
            "cpu-limit": int,
            "memory-limit": int,
            "storage-limit": int,
            "gpu": float,
            "gpu-resource": str,
            "env": [env_var],
            "node-selector": dict,
            "privileged": bool,
            "scheduler-name": str,
            "volumes": list,
            "volume-mounts": list,
            "labels": dict,
            "dynamic-labels": dict,
            "annotations": dict,
            "extra-resources": {str: int},
        }

        k8s_label_from_user = {
            v.Required("name"): str,
            v.Required("type"): str,
            v.Required("spec"): dict,
            "labels": dict,
            "dynamic-labels": dict,
            "annotations": dict,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update(
            {
                v.Required("name"): str,
                v.Required("labels"): [
                    v.Any(k8s_label_from_nodepool, k8s_label_from_user)
                ],
                "max-cores": int,
                "max-ram": int,
                "max-resources": {str: int},
                "default-label-cpu": int,
                "default-label-memory": int,
                "default-label-storage": int,
                "default-label-cpu-limit": int,
                "default-label-memory-limit": int,
                "default-label-storage-limit": int,
                "default-label-extra-resources": {str: int},
            }
        )

        provider = {
            v.Required("pools"): [pool],
            "context": str,
            "launch-retries": int,
            "max-servers": int,
            "max-cores": int,
            "max-ram": int,
            "max-resources": {str: int},
        }

        schema = ProviderConfig.getCommonSchemaDict()
        schema.update(provider)
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
