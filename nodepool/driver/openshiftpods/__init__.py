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

from kubernetes import config as k8s_config
from nodepool.driver import Driver
from nodepool.driver.openshiftpods.config import OpenshiftPodsProviderConfig
from nodepool.driver.openshiftpods.provider import OpenshiftPodsProvider


class OpenshiftPodsDriver(Driver):
    def __init__(self):
        super().__init__()

    def reset(self):
        try:
            k8s_config.load_kube_config(persist_config=True)
        except k8s_config.config_exception.ConfigException as e:
            if 'Invalid kube-config file. No configuration found.' in str(e):
                pass
            else:
                raise
        except FileNotFoundError:
            pass

    def getProviderConfig(self, provider):
        return OpenshiftPodsProviderConfig(self, provider)

    def getProvider(self, provider_config):
        return OpenshiftPodsProvider(provider_config)
