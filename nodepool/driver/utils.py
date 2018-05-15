# Copyright (C) 2018 Red Hat
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import threading
import time

from kazoo import exceptions as kze

from nodepool import stats
from nodepool import zk


class NodeLauncher(threading.Thread, stats.StatsReporter):
    '''
    Class to launch a single node.

    The NodeRequestHandler may return such object to manage asynchronous
    node creation.

    Subclasses are required to implement the launch method
    '''

    def __init__(self, handler, node):
        threading.Thread.__init__(self, name="NodeLauncher-%s" % node.id)
        stats.StatsReporter.__init__(self)
        self.log = logging.getLogger("nodepool.NodeLauncher-%s" % node.id)
        self.handler = handler
        self.node = node
        self.label = handler.pool.labels[node.type]
        self.pool = self.label.pool
        self.provider_config = self.pool.provider

    def storeNode(self):
        """Store the node state in Zookeeper"""
        self.handler.zk.storeNode(self.node)

    def run(self):
        start_time = time.monotonic()
        statsd_key = 'ready'

        try:
            self.launch()
        except kze.SessionExpiredError:
            # Our node lock is gone, leaving the node state as BUILDING.
            # This will get cleaned up in ZooKeeper automatically, but we
            # must still set our cached node state to FAILED for the
            # NodeLaunchManager's poll() method.
            self.log.error(
                "Lost ZooKeeper session trying to launch for node %s",
                self.node.id)
            self.node.state = zk.FAILED
            statsd_key = 'error.zksession'
        except Exception as e:
            self.log.exception("Launch failed for node %s:",
                               self.node.id)
            self.node.state = zk.FAILED
            self.handler.zk.storeNode(self.node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

        try:
            dt = int((time.monotonic() - start_time) * 1000)
            self.recordLaunchStats(statsd_key, dt)
            self.updateNodeStats(self.handler.zk, self.provider_config)
        except Exception:
            self.log.exception("Exception while reporting stats:")
