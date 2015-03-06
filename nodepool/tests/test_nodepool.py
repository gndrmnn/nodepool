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

import os
import tempfile
import threading
import time

import fixtures

from nodepool import allocation
from nodepool import tests
from nodepool import nodedb
import nodepool.nodepool


class TestNodepool(tests.DBTestCase):
    def setup_config(self, filename, **kwargs):
        configfile = os.path.join(os.path.dirname(tests.__file__),
                                  'fixtures', filename)
        config = open(configfile).read()
        (fd, path) = tempfile.mkstemp()
        os.write(fd, config.format(**kwargs))
        os.close(fd)
        return path

    def wait_for_threads(self):
        whitelist = ['APScheduler',
                     'MainThread',
                     'NodePool',
                     'NodeUpdateListener',
                     'Gearman client connect',
                     'Gearman client poll',
                     'fake-provider',
                     'fake-provider1',
                     'fake-provider2',
                     'fake-dib-provider',
                     'fake-jenkins',
                     'fake-target',
                     'DiskImageBuilder queue'
                     ]

        while True:
            done = True
            for t in threading.enumerate():
                if t.name not in whitelist:
                    done = False
            if done:
                return
            time.sleep(0.1)

    def wait_for_config(self, pool):
        for x in range(300):
            if pool.config is not None:
                return
            time.sleep(0.1)

    def test_db(self):
        db = nodedb.NodeDatabase(self.dburi)
        with db.getSession() as session:
            session.getNodes()

    def waitForImage(self, pool, provider_name, image_name):
        self.wait_for_config(pool)
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                image = session.getCurrentSnapshotImage(provider_name,
                                                        image_name)
                if image:
                    break
                time.sleep(1)
        self.wait_for_threads()

    def waitForNodes(self, pool):
        self.wait_for_config(pool)
        allocation_history = allocation.AllocationHistory()
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                needed = pool.getNeededNodes(session, allocation_history)
                if not needed:
                    break
                time.sleep(1)
        self.wait_for_threads()

    def test_node(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('node.yaml', dburi=self.dburi)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
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
        configfile = self.setup_config('node_dib.yaml', dburi=self.dburi)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
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
        configfile = self.setup_config('node_dib_and_snap.yaml',
                                       dburi=self.dburi)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
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
        tmpdir = self.useFixture(fixtures.TempDir())
        configfile = self.setup_config('node_dib_and_snap_fail.yaml',
                                       dburi=self.dburi,
                                       tmpdir=tmpdir.path)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        # Set env vars to make fake-diskimage-create write failure to tmpdir
        pool.start()
        self.addCleanup(pool.stop)
        # fake-provider1 will fail to build fake-dib-image
        self.waitForImage(pool, 'fake-provider2', 'fake-dib-image')
        self.waitForNodes(pool)
        self.assertTrue(os.path.exists(os.path.join(tmpdir.path, 'fail')))

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
        configfile = self.setup_config('subnodes.yaml', dburi=self.dburi)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
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
        configfile = self.setup_config('node_az.yaml', dburi=self.dburi)
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].az, 'az1')
