# Copyright 2020 AMD
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

import base64
import json
import logging

from kazoo import exceptions as kze

from nodepool import exceptions
from nodepool import zk
from nodepool.driver.utils import NodeLauncher
from nodepool.driver import NodeRequestHandler


class CobblerLauncher(NodeLauncher):
    def _launchLabel(self):
        self.log.debug("Creating resource for label %s", self.node.type[0])

        name, machine = self.handler.manager \
            .assign_machine(self.node.type[0],
                            self.handler.pool.name, self.node.id)

        if name is None and machine is None:
            raise exceptions.QuotaException("No machine available for %s" %
                                            self.node.type[0])

        self.node.state = zk.READY
        self.node.pool = self.handler.pool.name
        self.node.hostname = name

        resource = {
            "cobbler_server": self.provider_config.name,
            "node": name,
            "bmc": machine['details']['power_address'],
            "token": machine['token'],
            "profile": machine['profile'],
            "ks_meta": machine['details']['ks_meta'],
        }

        # The following is use for clean up
        self.node.external_id = name
        self.node.username = machine['token']
        self.node.connection_port = {
            "namespace":
                base64.b64encode(json.dumps(resource).encode()).decode(),
            "name": name,
            "host": "https://fakehost:443",
            "skiptls": False,
            "token": "faketoken",
            "user": "fakeuser",
            "ca_crt": "fakecert",
        }
        # using namespace as a hack for now to take advantage of k8s mechanism
        self.node.connection_type = "namespace"
        self.node.python_path = "auto"
        self.node.attributes = self.handler.pool.node_attributes

        self.zk.storeNode(self.node)

        self.log.info("Resource %s is ready" % name)

    def launch(self):
        attempts = 1
        while attempts <= self.provider_config.launch_retries:
            try:
                self._launchLabel()
                break
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our node lock
                # so there's no need to continue.
                raise
            except exceptions.QuotaException:
                self.log.exception(
                    "Not enough machine, attempt %d/%d failed for node %s:",
                    attempts, self.provider_config.launch_retries,
                    self.node.id)
                if attempts == self.provider_config.launch_retries:
                    raise
                attempts += 1


class CobblerNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.cobbler."
                            "CobblerNodeRequestHandler")

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
        return True

    def hasRemainingQuota(self, ntype):
        return self.manager.has_unassigned_machine(ntype)

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY, FAILED
        ABORTED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED, zk.ABORTED)
                   for s in node_states):
            return False

        return True

    def launch(self, node):
        thd = CobblerLauncher(self, node, self.provider)
        thd.start()
        self._threads.append(thd)
