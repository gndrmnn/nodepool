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

import logging
import os
import tempfile
import threading
import time

from nodepool import tests
from nodepool import nodedb
import nodepool.nodepool

log = logging.getLogger(__name__)


class TestNodepool(tests.DBTestCase):

    def setup_config(self, filename):
        configfile = os.path.join(os.path.dirname(tests.__file__),
                                  'fixtures', filename)
        config = open(configfile).read()
        (fd, path) = tempfile.mkstemp()
        os.write(fd, config.format(dburi=self.dburi))
        os.close(fd)
        return path

    def wait_for_threads(self):
        whitelist = ['APScheduler',
                     'MainThread',
                     'NodePool',
                     'NodeUpdateListener',
                     'Gearman client connect',
                     'Gearman client poll',
                     'fake-provider', 'fake-provider1', 'fake-provider2',
                     'fake-dib-provider',
                     'fake-jenkins',
                     'fake-target',
                     'DiskImageBuilder queue',
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

    def waitForNodes(self, pool):
        self.wait_for_config(pool)
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                needed = pool.getNeededNodes(session)
                if not needed:
                    break
                time.sleep(1)
        self.wait_for_threads()

    def test_node(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('node.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        time.sleep(3)
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)
        pool.stop()

    def test_dib_node(self):
        """Test that a dib image and node are created"""
        configfile = self.setup_config('node_dib.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        time.sleep(3)
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-dib-provider',
                                     label_name='fake-dib-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
        self.assertEqual(len(nodes), 1)
        pool.stop()

    def test_subnodes(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('subnodes.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        time.sleep(3)
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
        pool.stop()

    def test_node_az(self):
        """Test that an image and node are created with az specified"""
        configfile = self.setup_config('node_az.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        time.sleep(3)
        self.waitForNodes(pool)

        with pool.getDB().getSession() as session:
            nodes = session.getNodes(provider_name='fake-provider',
                                     label_name='fake-label',
                                     target_name='fake-target',
                                     state=nodedb.READY)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].az, 'az1')
        pool.stop()

    def test_capacity(self):
        """Test providers at capacity"""
        configfile = self.setup_config('capacity.yaml')
        pool = nodepool.nodepool.NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
        time.sleep(3)
        self.waitForNodes(pool)

        seen = { 'fake-precise': 0,
                 'fake-fedora': 0,
                 'fake-trusty': 0,
                 'fake-centos': 0,
        }

        while True:

            nodes = []

            with pool.getDB().getSession() as session:
                for label in seen.keys():

                    log.debug("Checking for READY %s nodes" % label)

                    l = session.getNodes(provider_name='fake-provider1',
                                         label_name=label,
                                         target_name='fake-target',
                                         state=nodedb.READY)

                    log.debug(" ... %d ready on provider1" % len(l))
                    seen[label] += len(l)
                    nodes += l

                    l = session.getNodes(provider_name='fake-provider2',
                                         label_name=label,
                                         target_name='fake-target',
                                         state=nodedb.READY)

                    log.debug(" ... %d ready after provider2" % len(l))
                    seen[label] += len(l)
                    nodes += l

                done = True
                for k in seen.keys():
                    if seen[k] != 5:
                        log.debug("only seen %d %s nodes" % (seen[k], k))
                        done = False
                    if done:
                        return
                log.debug("not done, try again")

                log.debug("Clearing out seen nodes")
                for n in nodes:
                    log.debug(" ... delete node %d" % n.id)
                    pool._deleteNode(session, n)
