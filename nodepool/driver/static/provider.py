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

from nodepool.driver import ProviderManager


class StaticNodeProviderManager(ProviderManager):
    log = logging.getLogger("nodepool.driver.static.provider."
                            "StaticNodeProviderManager")

    def __init__(self, provider):
        self.provider = provider

    def start(self):
        self.log.debug("Starting...")

    def stop(self):
        self.log.debug("Stopping...")

    def listNodes(self):
        servers = []
        for pool in self.provider.pools.values():
            for node in pool.nodes:
                servers.append(node)
        return servers

    def cleanupNode(self, server_id):
        return True

    def waitForNodeCleanup(self, server_id):
        return True
