# Copyright (C) 2017 Red Hat, Inc.
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
import mock

from nodepool import tests
from nodepool.nodepool import NodeLaunchManager


class TestNodeLaunchManager(tests.BaseTestCase):
    log = logging.getLogger("nodepool.TestNodeLaunchManager")

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_successful_launch(self, mock_launch):
        mgr = NodeLaunchManager(0)
        mgr.launch('aaa')
        mgr.launch('bbb')
        mgr.wait()
        self.assertEqual(len(mgr.ready_nodes), 2)
        self.assertEqual(len(mgr.failed_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_failed_launch(self, mock_launch):
        mock_launch.side_effect = Exception()
        mgr = NodeLaunchManager(0)
        mgr.launch('aaa')
        mgr.launch('bbb')
        mgr.wait()
        self.assertEqual(len(mgr.failed_nodes), 2)
        self.assertEqual(len(mgr.ready_nodes), 0)

    @mock.patch('nodepool.nodepool.NodeLauncher._launchNode')
    def test_mixed_launch(self, mock_launch):
        mock_launch.side_effect = [None, Exception()]
        mgr = NodeLaunchManager(0)
        mgr.launch('aaa')
        mgr.launch('bbb')
        mgr.wait()
        self.assertEqual(len(mgr.failed_nodes), 1)
        self.assertEqual(len(mgr.ready_nodes), 1)
