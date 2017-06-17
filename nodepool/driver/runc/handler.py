# Copyright 2017 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import random
import time

from nodepool import zk
from nodepool.driver.utils import NodeLauncher
from nodepool.driver import NodeRequestHandler


class RuncLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.label = provider_label
        self.pool = handler.manager.pools[provider_label.pool.name]
        self.handler = handler
        self.zk = handler.zk
        self.retries = 3

    def launch(self):
        self.log.debug("Starting %s container" % self.node.type[0])
        attempts = 1
        while attempts <= self.retries:
            port = random.randint(22022, 52022)
            hostid = "%s-%s-%s" % (self.node.id,
                                   self.node.type[0],
                                   self.handler.request.id)
            try:
                key = self.handler.manager.createContainer(
                    self.pool, hostid, port, self.label)
                break
            except Exception:
                if attempts <= self.retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self.retries, self.node.id)
                else:
                    raise
                attempts += 1
            time.sleep(1)

        self.node.state = zk.READY
        self.node.external_id = hostid
        self.node.hostname = self.pool.name
        self.node.interface_ip = self.pool.hostname
        self.node.public_ipv4 = self.pool.hostname
        self.node.host_keys = key
        self.node.username = self.label.username
        self.node.connection_port = port
        self.node.connection_type = "ssh"
        self.zk.storeNode(self.node)
        self.log.info("Container id %s is ready", self.node.id)


class RuncNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.runc."
                            "RuncNodeRequestHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    def imagesAvailable(self):
        '''
        This driver doesn't manage images, so always return True.
        '''
        return True

    def hasRemainingQuota(self, ntype):
        pool = self.provider.pools[self.pool.name]
        if pool.max_servers is None or \
           len(pool.containers) + 1 <= pool.max_servers:
            return True
        return False

    def hasProviderQuota(self, ntypes):
        pool = self.provider.pools[self.pool.name]
        if pool.max_servers is None or \
           len(pool.containers) + len(ntypes) <= pool.max_servers:
            return True
        return False

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = RuncLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
