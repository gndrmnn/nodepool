# Copyright (C) 2019 Red Hat
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
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import yaml

from nodepool import tests
from nodepool.driver import Drivers
from nodepool.launcher import StandaloneLauncher


class TestStandaloneLauncher(tests.DBTestCase):
    def setup_config(self, filename):
        test_dir = os.path.dirname(__file__)
        drivers_dir = os.path.join(
            os.path.dirname(os.path.dirname(test_dir)), 'driver')
        Drivers.load([drivers_dir])
        return super().setup_config(filename)

    def test_standalone_launcher(self):
        configfile = self.setup_config('external_driver.yaml')
        launcher = StandaloneLauncher(yaml.load(open(configfile)))
        nodes = launcher.launch(["test-label"])
        self.assertEqual(len(nodes), 1)
        launcher.cleanup(nodes)
