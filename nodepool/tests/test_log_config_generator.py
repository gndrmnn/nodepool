
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
import shutil
import subprocess
import tempfile

from nodepool.cmd.log_config_generator import generate_log_config

from nodepool import tests


class TestLogConfigGenerator(tests.BaseTestCase):

    def setUp(self):
        super(TestLogConfigGenerator, self).setUp()

        # the fileConfig run will try to at least stat the
        # files, so log_dir/image_log_dir have to be writable
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        super(TestLogConfigGenerator, self).tearDown()
        shutil.rmtree(self.tmp_dir)

    def test_generator(self):

        # for the generated logfile
        fd = tempfile.NamedTemporaryFile()

        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'bigconfig.yaml')

        args = {
            'config': config,
            'log_dir': self.tmp_dir,
            'image_log_dir': self.tmp_dir,
            'output': fd
        }

        generate_log_config(**args)

        # we want to check this logfile parses but we don't want to
        # destroy the logging configuration.  So run this basic check
        # in a subprocess
        cmd = "python -c 'import logging.config; " \
              "logging.config.fileConfig(\"%s\")'" % (fd.name)

        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                    shell=True)
        except subprocess.CalledProcessError as e:
            print "*** Configuration failed to parse:"
            print e.output
            print "***"
            self.fail("Configuration failed to validate")
