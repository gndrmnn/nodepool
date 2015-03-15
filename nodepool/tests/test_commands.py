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
import time

import fixtures
import mock

from nodepool.cmd import nodepoolcmd
from nodepool import nodepool, tests


class TestNodepoolCMD(tests.DBTestCase):
    def patch_argv(self, *args):
        argv = ["nodepool"]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_images_listed(self, configfile, image_cnt):
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable') as mocked:
            instance = mocked.return_value
            add_row = instance.add_row
            to_str = instance.__str__
            to_str.return_value = 'Mocked image listing output'
            nodepoolcmd.main()
            self.assertEquals(mocked.called, True)
            self.assertEquals(len(add_row.mock_calls), image_cnt)
            self.assertEquals(to_str.called, True)

    def test_snapshot_image_update(self):
        configfile = self.setup_config("node.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider", "fake-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_dib_image_update(self):
        configfile = self.setup_config("node_dib.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-dib-provider", "fake-dib-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_dib_snapshot_image_update(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider1", "fake-dib-image")
        nodepoolcmd.main()
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider2", "fake-dib-image")
        nodepoolcmd.main()

        self.assert_images_listed(configfile, 2)

    def test_dib_snapshot_image_update_all(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-dib-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 2)

    def test_image_update_all(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)

    def test_image_delete_invalid(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-delete", "invalid-image")
        nodepoolcmd.main()

    def test_image_delete_snapshot(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        pool = nodepool.NodePool(configfile, watermark_sleep=1)
        # This gives us a nodepool with a working db but not running which
        # is important so we can control image building
        pool.updateConfig()
        self.addCleanup(pool.stop)
        self.waitForImage(pool, 'fake-provider1', 'fake-image1')

        self.patch_argv("-c", configfile, "image-delete", '1')
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 0)
