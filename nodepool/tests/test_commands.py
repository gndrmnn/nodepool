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

import fixtures
import mock

from nodepool.cmd import nodepoolcmd
from nodepool import tests


class TestNodepoolCMD(tests.DBTestCase):
    def patch_argv(self, *args):
        argv = ["nodepool"]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def test_snapshot_image_update(self):
        configfile = self.setup_config("node.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider", "fake-image")
        nodepoolcmd.main()

        # Test that images show up in listing
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), 1)
            self.assertEquals(to_str.called, True)

    def test_dib_image_update(self):
        configfile = self.setup_config("node_dib.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-dib-provider", "fake-dib-image")
        nodepoolcmd.main()

        # Test that images show up in listing
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), 1)
            self.assertEquals(to_str.called, True)

    def test_dib_snapshot_image_update(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider1", "fake-dib-image")
        nodepoolcmd.main()
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider2", "fake-dib-image")
        nodepoolcmd.main()

        # Test that images show up in listing
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), 2)
            self.assertEquals(to_str.called, True)

    def test_dib_snapshot_image_update_all(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-dib-image")
        nodepoolcmd.main()

        # Test that images show up in listing
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), 2)
            self.assertEquals(to_str.called, True)

    def test_image_update_all(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()

        # Test that image shows up in listing
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), 1)
            self.assertEquals(to_str.called, True)

    def test_image_list_empty(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(add_row.called, False)
            self.assertEquals(to_str.called, True)
