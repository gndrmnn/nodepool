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
import math

from nodepool.driver.simple import SimpleTaskManagerAdapter
from nodepool.driver.simple import SimpleTaskManagerInstance
from nodepool.driver.utils import QuotaInformation

import googleapiclient.discovery


class GCEInstance(SimpleTaskManagerInstance):
    def load(self, data):
        if data['status'] == 'TERMINATED':
            self.deleted = True
        elif data['status'] == 'RUNNING':
            self.ready = True
        self.external_id = data['name']
        self.az = data['zone']

        iface = data.get('networkInterfaces', [])
        if len(iface):
            self.private_ipv4 = iface[0].get('networkIP')
            access = iface[0].get('accessConfigs', [])
            if len(access):
                self.public_ipv4 = access[0].get('natIP')
        self.interface_ip = self.public_ipv4 or self.private_ipv4
        self._machine_type = data.get('_nodepool_gce_machine_type')

        if data.get('metadata'):
            for item in data['metadata'].get('items', []):
                self.metadata[item['key']] = item['value']

    def getQuotaInformation(self):
        return QuotaInformation(
            cores=self._machine_type['guestCpus'],
            instances=1,
            ram=self._machine_type['memoryMb'])


class GCEAdapter(SimpleTaskManagerAdapter):
    log = logging.getLogger("nodepool.driver.gce.GCEAdapter")

    def __init__(self, provider):
        self.provider = provider
        self.compute = googleapiclient.discovery.build('compute', 'v1')
        self._machine_types = {}

    def listInstances(self, task_manager):
        servers = []

        q = self.compute.instances().list(project=self.provider.project,
                                          zone=self.provider.zone)
        with task_manager.rateLimit():
            result = q.execute()

        for instance in result.get('items', []):
            instance_type = instance['machineType'].split('/')[-1]
            mtype = self._getMachineType(task_manager, instance_type)
            instance['_nodepool_gce_machine_type'] = mtype
            servers.append(GCEInstance(instance))
        return servers

    def deleteInstance(self, task_manager, server_id):
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

    def _getMachineType(self, task_manager, machine_type):
        if machine_type in self._machine_types:
            return self._machine_types[machine_type]
        q = self.compute.machineTypes().get(
            project=self.provider.project,
            zone=self.provider.zone,
            machineType=machine_type)
        with task_manager.rateLimit():
            result = q.execute()
        self._machine_types[machine_type] = result
        return result

    def createInstance(self, task_manager, hostname, metadata, label):
        image_id = self._getImageId(task_manager, label.cloud_image)
        disk_init = dict(sourceImage=image_id,
                         diskType='zones/{}/diskTypes/{}'.format(
                             self.provider.zone, label.volume_type),
                         diskSizeGb=str(label.volume_size))
        disk = dict(boot=True,
                    autoDelete=True,
                    initializeParams=disk_init)
        mtype = self._getMachineType(task_manager, label.instance_type)
        machine_type = mtype['selfLink']
        network = dict(network='global/networks/default',
                       accessConfigs=[dict(
                           type='ONE_TO_ONE_NAT',
                           name='External NAT')])
        metadata_items = []
        for (k, v) in metadata.items():
            metadata_items.append(dict(key=k, value=v))
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

    def getQuotaLimits(self, task_manager):
        q = self.compute.regions().get(project=self.provider.project,
                                       region=self.provider.region)
        with task_manager.rateLimit():
            ret = q.execute()

        cores = None
        instances = None
        for item in ret['quotas']:
            if item['metric'] == 'CPUS':
                cores = item['limit']
                continue
            if item['metric'] == 'INSTANCES':
                instances = item['limit']
                continue
        return QuotaInformation(
            cores=cores,
            instances=instances,
            default=math.inf)

    def getQuotaForLabel(self, task_manager, label):
        mtype = self._getMachineType(task_manager, label.instance_type)
        return QuotaInformation(
            cores=mtype['guestCpus'],
            instances=1,
            ram=mtype['memoryMb'])
