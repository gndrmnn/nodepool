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

import re
import logging
import digitalocean

from nodepool.driver.simple import SimpleTaskManagerAdapter
from nodepool.driver.simple import SimpleTaskManagerInstance
from nodepool.driver.utils import QuotaInformation


class Droplet(SimpleTaskManagerInstance):

    metadata_matcher = re.compile(
        '^metadata:key:(?P<key>).*:value:(?P<value>.*)$')

    def load(self, data):
        if data.status == 'active':
            self.ready = True
        elif data.status == 'off':
            self.deleted = True

        self.external_id = data.id

        for network in data.networks.get('v4', []):
            if network['type'] == 'public':
                self.public_ipv4 = network['ip_address']
            elif network['type'] == 'private':
                self.private_ipv4 = network['ip_address']

        for network in data.networks.get('v6', []):
            if network['type'] == 'public':
                self.public_ipv6 = network['ip_address']
            elif network['type'] == 'private':
                self.private_ipv6 = network['ip_address']

        self.interface_ip = self.public_ipv4 or self.private_ipv4

        self.region = data.region['slug']

        for tag in data.tags:
            if tag.startswith('metadata:'):
                match = self.metadata_matcher.match(tag)
                if match:
                    self.metadata[match.group('key')] = match.group('value')

    def getQuotaInformation(self):
        return QuotaInformation(
            cores=1,
            instances=1,
            ram=1)


class DigitalOceanAdapter(SimpleTaskManagerAdapter):
    log = logging.getLogger("nodepool.driver.digitalocean.DigitalOceanAdapter")

    def __init__(self, provider):
        self.provider = provider
        self.manager = digitalocean.Manager()

    def listInstances(self, task_manager):
        droplets = []
        with task_manager.rateLimit():
            result = self.manager.get_all_droplets(
                tag_name='nodepool-managed')
        for instance in result:
            droplets.append(Droplet(instance))
        return droplets

    def createInstance(self, task_manager, hostname, metadata, label_config):
        tags = ["metadata:key:{}:value:{}".format(key, value)
                for key, value in metadata.items()]
        tags.extend([
            "nodepool-managed",
            label_config.name])
        droplet = digitalocean.Droplet(
            name=hostname,
            region=self.provider.region,
            image=label_config.cloud_image.image_id,
            size=label_config.size,
            tags=tags)

        with task_manager.rateLimit():
            droplet.create()

        return hostname

    def getQuotaForLabel(self, task_manager, label_config):
        return QuotaInformation(
            cores=1,
            instances=1,
            ram=1)

    def getQuotaLimits(self, task_manager):
        return QuotaInformation(
            cores=1,
            instances=1,
            ram=1)

    def deleteInstance(self, task_manager, droplet_id):
        with task_manager.rateLimit():
            droplets = self.manager.get_all_droplets(
                tag_name='nodepool-managed')
            for droplet in droplets:
                if droplet.id == droplet_id:
                    droplet.destroy()
                    break
