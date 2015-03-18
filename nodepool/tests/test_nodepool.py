# Copyright (C) 2014 OpenStack Foundation
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

from nodepool import tests
from nodepool import nodedb
import nodepool.nodepool


class TestNodepool(tests.DBTestCase):
    def test_db(self):
        db = nodedb.NodeDatabase(self.dburi)
        with db.getSession() as session:
            session.getNodes()

    def test_node(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)

    def test_dib_node(self):
        """Test that a dib image and node are created"""
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodePool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-dib-provider',
                                     label_name='fake-dib-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
        self.assertEqual(len(nodes), 1)

    def test_dib_and_snap_label(self):
        """Test that a label with dib and snapshot images build."""
        configfile = self.setup_config('node_dib_and_snap.yaml')
        pool = self.useNodePool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider1', 'fake-dib-image')
        self.waitForImage(pool, 'fake-provider2', 'fake-dib-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider1',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)
            nodes = session.getNodes(provider_name='fake-provider2',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)

    def test_dib_and_snap_fail(self):
        """Test that snap based nodes build when dib fails."""
        configfile = self.setup_config('node_dib_and_snap_fail.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
        # fake-provider1 will fail to build fake-dib-image
        self.waitForImage(pool, 'fake-provider2', 'fake-dib-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            # fake-provider1 uses dib.
            nodes = session.getNodes(provider_name='fake-provider1',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 0)
            # fake-provider2 uses snapshots.
            nodes = session.getNodes(provider_name='fake-provider2',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 2)
        # The fake disk image create script will return 127 with
        # SHOULD_FAIL flag set to true.
        self.assertEqual(self.subprocesses[0].returncode, 127)
        self.assertEqual(self.subprocesses[-1].returncode, 127)

    def test_dib_upload_fail(self):
        """Test that a dib and snap image upload failure is contained."""
        configfile = self.setup_config('node_dib_and_snap_upload_fail.yaml')
        pool = self.useNodePool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider2', 'fake-dib-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            # fake-provider1 uses dib.
            nodes = session.getNodes(provider_name='fake-provider1',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 0)
            # fake-provider2 uses snapshots.
            nodes = session.getNodes(provider_name='fake-provider2',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 2)

    def test_subnodes(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('subnodes.yaml')
        pool = self.useNodePool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 2)
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='multi-fake',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 2)
            for node in nodes:
                self.assertEqual(len(node.subnodes), 2)
                for subnode in node.subnodes:
                    self.assertEqual(subnode.state, nodedb.READY)

    def test_node_az(self):
        """Test that an image and node are created with az specified"""
        configfile = self.setup_config('node_az.yaml')
        pool = self.useNodePool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].az, 'az1')
