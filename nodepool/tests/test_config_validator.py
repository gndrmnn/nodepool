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

from nodepool.cmd import config_validator

from nodepool import tests
from yaml import parser
import voluptuous


class TestConfigValidation(tests.BaseTestCase):

    def setUp(self):
        super(TestConfigValidation, self).setUp()

    def test_good_config(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'good.yaml')

        validator = config_validator.ConfigValidator(config)
        validator.validate()

    def test_yaml_error(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'yaml_error.yaml')

        validator = config_validator.ConfigValidator(config)
        self.assertRaises(parser.ParserError, validator.validate)

    def test_schema(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'schema_error.yaml')

        validator = config_validator.ConfigValidator(config)
        self.assertRaises(voluptuous.MultipleInvalid, validator.validate)
