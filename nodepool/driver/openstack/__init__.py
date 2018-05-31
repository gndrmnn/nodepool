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

import os_client_config

from nodepool.driver import Driver
from nodepool.driver.openstack.config import OpenStackProviderConfig


class OpenStackDriver(Driver):
    def __init__(self):
        super(self, OpenStackDriver).__init__(self)
        self.reset()

    def reset(self):
        self.os_client_config = os_client_config.OpenStackConfig()

    def getProviderConfig(self, provider):
        return OpenStackProviderConfig(self, provider)
