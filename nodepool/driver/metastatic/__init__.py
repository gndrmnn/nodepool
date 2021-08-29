# Copyright 2021 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from nodepool.driver.statemachine import StateMachineDriver
from nodepool.driver.statemachine import StateMachineProvider
from nodepool.driver.metastatic.config import MetastaticProviderConfig
from nodepool.driver.metastatic.adapter import MetastaticAdapter


class MetastaticDriver(StateMachineDriver):
    def getProvider(self, provider_config):
        # We usually don't override this method, but since our "cloud"
        # is actually Nodepool itself, we need to interact with
        # nodepool as a client, so we need a ZK connection.  We can
        # re-use the launcher's connection for this.
        adapter = self.getAdapter(provider_config)
        provider = StateMachineProvider(adapter, provider_config)
        adapter._setProvider(provider)
        return provider

    def getProviderConfig(self, provider):
        return MetastaticProviderConfig(self, provider)

    def getAdapter(self, provider_config):
        return MetastaticAdapter(provider_config)
