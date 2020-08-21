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

from nodepool.cmd.config_validator import ConfigValidator

from nodepool import tests


class TestConfigValidation(tests.BaseTestCase):

    def setUp(self):
        super(TestConfigValidation, self).setUp()

    def test_good_config(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'good.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate(dict(NODEPOOL_PORT="8005"))
        self.assertEqual(ret, 0)
        self.assertEqual(validator.config['webapp']['port'], 8005)

    def test_yaml_error(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'yaml_error.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)

    def test_missing_top_level_label(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'missing_top_label.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)

    def test_no_diskimage_name(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'no_diskimage_name.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)

    def test_duplicate_diskimage_name(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'duplicate_diskimage_name.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)

    def test_missing_parent_diskimage(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'missing_parent_diskimage.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)

    def test_schema(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate',
                              'schema_error.yaml')

        validator = ConfigValidator(config)
        ret = validator.validate()
        self.assertEqual(ret, 1)
