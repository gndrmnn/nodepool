# Copyright (C) 2017 Red Hat
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

import logging
import os

from voluptuous import MultipleInvalid

from nodepool import tests
from nodepool import config as nodepool_config
from nodepool.cmd.config_validator import ConfigValidator


class TestDriverRunC(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverRunC")

    def test_runc_validator(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'runc_error.yaml')
        validator = ConfigValidator(config)
        self.assertRaises(MultipleInvalid, validator.validate)

    def test_runc_config(self):
        configfile = self.setup_config('runc.yaml')
        config = nodepool_config.loadConfig(configfile)
        self.assertIn('runc-provider', config.providers)
