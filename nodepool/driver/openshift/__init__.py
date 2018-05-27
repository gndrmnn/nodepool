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

from nodepool.driver import Driver
from nodepool.driver.openshift.config import OpenshiftProviderConfig
from nodepool.driver.openshift.provider import OpenshiftProvider
from openshift import config


class OpenshiftDriver(Driver):
    def __init__(self):
        super().__init__()

    def reset(self):
        try:
            config.load_kube_config(persist_config=True)
        except FileNotFoundError:
            pass

    def getProviderConfig(self, provider):
        return OpenshiftProviderConfig(self, provider)

    def getProvider(self, provider_config, use_taskmanager):
        return OpenshiftProvider(provider_config, use_taskmanager)
