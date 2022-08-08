# Copyright 2018 Red Hat
# Copyright 2022 Acme Gating, LLC
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

from nodepool.driver.statemachine import StateMachineDriver
# Import the modules rather than the class so that the unit tests can
# override the classes to add some test-specific methods/data.
import nodepool.driver.aws.config as driver_config
import nodepool.driver.aws.adapter as driver_adapter


class AwsDriver(StateMachineDriver):
    def getProviderConfig(self, provider):
        return driver_config.AwsProviderConfig(self, provider)

    def getAdapter(self, provider_config):
        return driver_adapter.AwsAdapter(provider_config)
