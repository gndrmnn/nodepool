# Copyright 2020 Albin Vass
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
import digitalocean

from nodepool.driver import statemachine
from nodepool.driver.utils import QuotaInformation


class Droplet(statemachine.Instance):

    def __init__(self, vm):
        self.status = vm.status
        self.name = vm.name
        self.external_id = vm.id
        self.metadata = {}
        self.public_ipv4 = None
        self.private_ipv4 = None
        self.public_ipv6 = None
        self.private_ipv6 = None

        for network in vm.networks.get('v4', []):
            if network['type'] == 'public':
                self.public_ipv4 = network['ip_address']
            elif network['type'] == 'private':
                self.private_ipv4 = network['ip_address']

        for network in vm.networks.get('v6', []):
            if network['type'] == 'public':
                self.public_ipv6 = network['ip_address']
            elif network['type'] == 'private':
                self.private_ipv6 = network['ip_address']

        self.interface_ip = self.public_ipv4 or self.private_ipv4

        # Digital ocean doesn't have a concept of availability zones
        # separate from regions so set both region and az to region slug.
        self.region = vm.region['slug']
        self.az = vm.region['slug']

        for tag in vm.tags:
            metadata = tag.split(':', 1)
            if len(metadata) > 1:
                self.metadata[metadata[0]] = metadata[1]
            else:
                self.metadata[metadata[0]] = ""

    def getQuotaInformation(self):
        return QuotaInformation(instances=1)


class DeleteStateMachine(statemachine.StateMachine):
    VM_DELETING = 'deleting vm'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id):
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id

    def advance(self):
        if self.state == self.START:
            try:
                self.vm = self.adapter._deleteDroplet(self.external_id)
                self.state = self.VM_DELETING
            except digitalocean.NotFoundError:
                self.state = self.COMPLETE
        if self.state == self.VM_DELETING:
            try:
                self.adapter._refresh(self.vm)
            except digitalocean.NotFoundError:
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
        self.tags = label.tags or []
        metadata_tags = ["{}:{}".format(key, value)
                         for key, value in self.metadata.items()]
        self.tags.extend(metadata_tags)
        self.hostname = hostname
        self.label = label
        self.vm = None

    def advance(self):
        if self.state == self.START:
            self.vm = self.adapter._createDroplet(
                self.label, self.tags, self.hostname)
            self.external_id = self.vm.id
            self.state = self.VM_CREATING
        if self.state == self.VM_CREATING:
            self.vm = self.adapter._refresh(self.vm)
            if self.vm.status == 'active':
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True
            return Droplet(self.vm)


class DigitalOceanAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.digitalocean.DigitalOceanAdapter")

    def __init__(self, provider):
        self.provider = provider
        self.manager = digitalocean.Manager()

    def getCreateStateMachine(self, hostname, label, metadata, retries):
        return CreateStateMachine(self, hostname, label, metadata, retries)

    def getDeleteStateMachine(self, external_id):
        return DeleteStateMachine(self, external_id)

    def _createDroplet(self, label, tags, hostname):
        tags.append("nodepool")
        droplet = digitalocean.Droplet(
            name=hostname,
            region=self.provider.region,
            image=label.cloud_image.image_id,
            ssh_keys=label.ssh_keys,
            size=label.size,
            tags=tags,
            user_data=label.user_data)

        droplet.create()

        return droplet

    def _deleteDroplet(self, external_id):
        droplet = self.manager.get_droplet(external_id)
        droplet.destroy()
        return droplet

    def _refresh(self, droplet):
        return self.manager.get_droplet(droplet.id)

    def listInstances(self):
        result = self.manager.get_all_droplets(
            tag_name='nodepool-managed')
        for instance in result:
            yield Droplet(instance)

    def cleanupLeakedResources(self, known_nodes, metadata):
        for vm in self.listInstances():
            node_id = self._metadataMatches(vm, metadata)
            if (node_id and node_id not in known_nodes):
                self.log.info(
                    f"Deleting leaked vm - id: {vm.id}, name: {vm.name}")
                self._deleteDroplet(vm.id)

    def _metadataMatches(self, droplet, metadata):
        if not droplet.metadata:
            return None
        for k, v in metadata.items():
            if droplet.metadata.get(k) != v:
                return None
        return droplet.metadata["nodepool_node_id"]

    def getQuotaForLabel(self, label):
        return QuotaInformation(instances=1)

    def getQuotaLimits(self):
        account = self.manager.get_account()
        return QuotaInformation(instances=account.droplet_limit)
