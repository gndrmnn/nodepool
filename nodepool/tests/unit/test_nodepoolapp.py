# Copyright (C) 2015 OpenStack Foundation
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

import sys  # noqa making sure its available for monkey patching

import mock

from nodepool import cmd
from nodepool import tests

class FakeApp(cmd.NodepoolApp):

    def run(self):
        return True

class TestNodepoolApp(tests.DBTestCase):

    @mock.patch('nodepool.cmd.config_validator.ConfigValidator.validate')
    def test_main(self, config_validator):
        fake_config = self.setup_config("node_cmd.yaml")
        self.assertTrue(FakeApp.main(('-c', fake_config)))
        # Twice because self.setup_config also calls it
        self.assertEquals(2, config_validator.call_count)
