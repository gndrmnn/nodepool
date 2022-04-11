# Copyright 2021 Acme Gating, LLC
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

import math
import logging

import cachetools.func

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine


class Instance(statemachine.Instance):
    def __init__(self, vm):
        self.metadata = {}
        self.external_id = 'test'
        self.interface_ip = 'fake'
        self.public_ipv4 = 'fake'
        self.private_ipv4 = 'fake'
        self.public_ipv6 = 'fake'
        self.region = 'region'
        self.az = 'az'


class DeleteStateMachine(statemachine.StateMachine):
    VM_DELETING = 'deleting vm'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id):
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id

    def advance(self):
        if self.state == self.START:
            self.state = self.VM_DELETING

        if self.state == self.VM_DELETING:
            self.adapter._deleteVirtualMachine(self.external_id)
            self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            return True


class CreateStateMachine(statemachine.StateMachine):
    VM_CREATING = 'creating vm'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, metadata, retries):
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.metadata = metadata
        self.hostname = hostname
        self.label = label
        self.vm = None

    def advance(self):
        if self.state == self.START:
            self.external_id = self.hostname
            self.state = self.VM_CREATING

        if self.state == self.VM_CREATING:
            self.adapter._createVirtualMachine(self.hostname)
            self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            return Instance(self.vm)


class Adapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.example.Adapter")

    def __init__(self, provider_config):
        self.provider_config = provider_config
        self.rate_limiter = RateLimiter(provider_config.name,
                                        provider_config.rate_limit)
        self.cloud = object()

    def getCreateStateMachine(self, hostname, label, metadata, retries, log):
        return CreateStateMachine(self, hostname, label, metadata, retries)

    def getDeleteStateMachine(self, external_id, log):
        return DeleteStateMachine(self, external_id)

    def cleanupLeakedResources(self, known_nodes, metadata):
        pass

    def listInstances(self):
        for vm in self._listVirtualMachines():
            yield Instance(vm)

    def getQuotaLimits(self):
        return QuotaInformation(default=math.inf)

    def getQuotaForLabel(self, label):
        return QuotaInformation(instances=1)

    # Local implementation below

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listVirtualMachines(self):
        return []

    def _createVirtualMachine(self, hostname):
        pass

    def _deleteVirtualMachine(self, external_id):
        pass
