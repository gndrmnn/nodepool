# Copyright 2019 Red Hat
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

import googleapiclient.discovery

from nodepool.driver import Provider
from nodepool.driver.simple import SimpleTaskManagerAdapter, SimpleTaskManagerInstance
from nodepool.nodeutils import iterate_timeout
import nodepool.exceptions

class GcloudInstance(SimpleTaskManagerInstance):
    def load(self, data):
        if data['status'] == 'TERMINATED':
            self.deleted = True
        elif data['status'] == 'RUNNING':
            self.ready = True
        self.external_id = data['name']

        iface = data.get('networkInterfaces', [])
        if len(iface):
            access = iface[0].get('accessConfigs', [])
            if len(access):
                self.ip_address = access[0].get('natIP')

        if data.get('metadata'):
            for item in data['metadata'].get('items', []):
                self.metadata[item['key']] = item['value']


class GcloudAdapter(SimpleTaskManagerAdapter):
    log = logging.getLogger("nodepool.driver.gcloud.GcloudAdapter")

    def __init__(self, provider):
        self.provider = provider
        self.compute = googleapiclient.discovery.build('compute', 'v1')

    def listInstances(self, task_manager):
        servers = []

        q = self.compute.instances().list(project=self.provider.project,
                                          zone=self.provider.zone)
        with task_manager.rateLimit():
            result = q.execute()

        for instance in result.get('items', []):
            servers.append(GcloudInstance(instance))
        return servers

    def deleteInstance(self, server_id):
        q = self.compute.instances().delete(project=self.provider.project,
                                            zone=self.provider.zone,
                                            instance=server_id)
        with task_manager.rateLimit():
            q.execute()

    def _getImageId(self, task_manager, cloud_image):
        image_id = cloud_image.image_id

        if image_id:
            return image_id

        if cloud_image.image_family:
            q = self.compute.images().getFromFamily(
                project=cloud_image.image_project,
                family=cloud_image.image_family)
            with task_manager.rateLimit():
                result = q.execute()
            image_id = result['selfLink']

        return image_id

    def createInstance(self, task_manager, hostname, node_id, label):
        image_id = self._getImageId(task_manager, label.cloud_image)
        disk = dict(boot=True,
                    autoDelete=True,
                    initializeParams=dict(sourceImage=image_id))
        machine_type = 'zones/{}/machineTypes/{}'.format(
            self.provider.zone, label.instance_type)
        network = dict(network='global/networks/default',
                       accessConfigs=[dict(
                           type='ONE_TO_ONE_NAT',
                           name='External NAT')])
        metadata_items = []
        metadata_items.append(dict(key='nodepool_node_id',
                                   value=node_id))
        metadata_items.append(dict(key='nodepool_provider_name',
                                   value=self.provider.name))
        meta = dict(items=metadata_items)
        args = dict(
            name=hostname,
            machineType=machine_type,
            disks=[disk],
            networkInterfaces=[network],
            serviceAccounts=[],
            metadata=meta)
        q = self.compute.instances().insert(
            project=self.provider.project,
            zone=self.provider.zone,
            body=args)
        with task_manager.rateLimit():
            q.execute()
        return hostname
