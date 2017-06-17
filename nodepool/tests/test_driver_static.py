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

import fixtures
import logging
import os

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool.cmd.config_validator import ConfigValidator
from voluptuous import MultipleInvalid


class TestDriverStatic(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverStatic")

    def test_static_validator(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'static_error.yaml')
        validator = ConfigValidator(config)
        self.assertRaises(MultipleInvalid, validator.validate)

    def test_static_config(self):
        configfile = self.setup_config('static.yaml')
        config = nodepool_config.loadConfig(configfile)
        self.assertIn('static-provider', config.providers)

    def test_static_handler(self):
        def fake_check_host(self, node):
            return True

        check_host = 'nodepool.driver.static.provider.StaticNodeProvider.' \
                     'checkHost'
        self.useFixture(fixtures.MonkeyPatch(check_host, fake_check_host))

        configfile = self.setup_config('static.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        nodes = self.waitForNodes('fake-concurrent-label', 2)
        self.assertEqual(len(nodes), 2)
