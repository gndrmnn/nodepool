# Copyright 2019 Red Hat
# Copyright 2022 Acme Gating, LLC
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

import cachetools.func
import functools
import logging
import math

from nodepool.driver import statemachine
from nodepool.driver.utils import QuotaInformation, RateLimiter

import googleapiclient.discovery


CACHE_TTL = 10


def gce_metadata_to_dict(metadata):
    if metadata is None:
        return {}
    return {item['key']: item['value'] for item in metadata.get('items', [])}


def dict_to_gce_metadata(metadata):
    metadata_items = []
    for (k, v) in metadata.items():
        metadata_items.append(dict(key=k, value=v))
    return dict(items=metadata_items)


class GceInstance(statemachine.Instance):
    def __init__(self, data, quota):
        super().__init__()
        self.external_id = data['name']
        self.cloud = 'Google'
        zone = data['zone'].rsplit('/', 1)[1]
        self.region = zone.rsplit('-', 1)[0]
        self.az = zone

        iface = data.get('networkInterfaces', [])
        if len(iface):
            self.private_ipv4 = iface[0].get('networkIP')
            access = iface[0].get('accessConfigs', [])
            if len(access):
                self.public_ipv4 = access[0].get('natIP')
        self.interface_ip = self.public_ipv4 or self.private_ipv4
        self.metadata = gce_metadata_to_dict(data.get('metadata'))
        self.quota = quota

    def getQuotaInformation(self):
        return self.quota


class GceResource(statemachine.Resource):
    def __init__(self, metadata, type, id):
        super().__init__(metadata)
        self.type = type
        self.id = id


class GceDeleteStateMachine(statemachine.StateMachine):
    INSTANCE_DELETING = 'deleting instance'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id

    def advance(self):
        if self.state == self.START:
            self.adapter._deleteInstance(self.external_id)
            self.state = self.INSTANCE_DELETING

        if self.state == self.INSTANCE_DELETING:
            data = self.adapter._getInstance(self.external_id)
            if data is None or data['status'] == 'TERMINATED':
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True


class GceCreateStateMachine(statemachine.StateMachine):
    INSTANCE_CREATING = 'creating instance'
    INSTANCE_RETRY = 'retrying instance creation'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries, request, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.attempts = 0
        self.image_external_id = image_external_id
        self.metadata = metadata
        self.hostname = hostname
        self.label = label
        self.instance = None
        self.quota = None

    def advance(self):
        if self.state == self.START:
            self.external_id = self.hostname
            self.adapter._createInstance(
                self.hostname, self.metadata, self.label)
            self.state = self.INSTANCE_CREATING

        if self.state == self.INSTANCE_CREATING:
            data = self.adapter._getInstance(self.hostname)
            if data is None:
                return
            if self.quota is None:
                machine_type = data['machineType'].split('/')[-1]
                self.quota = self.adapter._getQuotaForMachineType(machine_type)
            if data['status'] == 'RUNNING':
                self.instance = data
                self.state = self.COMPLETE
            elif data['status'] == 'TERMINATED':
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                self.state = self.START
                return
            else:
                return

        if self.state == self.COMPLETE:
            self.complete = True
            return GceInstance(self.instance, self.quota)


class GceAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.GceAdapter")

    def __init__(self, provider_config):
        # Wrap these instance methods with a per-instance LRU cache so
        # that we don't leak memory over time when the adapter is
        # occasionally replaced.
        self._getMachineType = functools.lru_cache(maxsize=None)(
            self._getMachineType)
        self._getImageId = functools.lru_cache(maxsize=None)(
            self._getImageId)

        self.provider = provider_config
        self.compute = googleapiclient.discovery.build('compute', 'v1')
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)

    def getCreateStateMachine(self, hostname, label, image_external_id,
                              metadata, retries, request, log):
        return GceCreateStateMachine(self, hostname, label, image_external_id,
                                     metadata, retries, request, log)

    def getDeleteStateMachine(self, external_id, log):
        return GceDeleteStateMachine(self, external_id, log)

    def listInstances(self):
        instances = []

        for instance in self._listInstances():
            machine_type = instance['machineType'].split('/')[-1]
            quota = self._getQuotaForMachineType(machine_type)
            instances.append(GceInstance(instance, quota))
        return instances

    def listResources(self):
        for instance in self._listInstances():
            if instance['status'] == 'TERMINATED':
                continue
            metadata = gce_metadata_to_dict(instance.get('metadata'))
            yield GceResource(metadata, 'instance', instance['name'])

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.id}")
        if resource.type == 'instance':
            self._deleteInstance(resource.id)

    def getQuotaLimits(self):
        q = self.compute.regions().get(project=self.provider.project,
                                       region=self.provider.region)
        with self.rate_limiter:
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

    def getQuotaForLabel(self, label):
        return self._getQuotaForMachineType(label.instance_type)

    # Local implementation below

    def _createInstance(self, hostname, metadata, label):
        metadata = metadata.copy()
        image_id = self._getImageId(label.cloud_image)
        disk_init = dict(sourceImage=image_id,
                         diskType='zones/{}/diskTypes/{}'.format(
                             self.provider.zone, label.volume_type),
                         diskSizeGb=str(label.volume_size))
        disk = dict(boot=True,
                    autoDelete=True,
                    initializeParams=disk_init)
        mtype = self._getMachineType(label.instance_type)
        machine_type = mtype['selfLink']
        network = dict(network='global/networks/default',
                       accessConfigs=[dict(
                           type='ONE_TO_ONE_NAT',
                           name='External NAT')])
        if label.cloud_image.key:
            metadata['ssh-keys'] = '{}:{}'.format(
                label.cloud_image.username,
                label.cloud_image.key)
        args = dict(
            name=hostname,
            machineType=machine_type,
            disks=[disk],
            networkInterfaces=[network],
            serviceAccounts=[],
            metadata=dict_to_gce_metadata(metadata))
        q = self.compute.instances().insert(
            project=self.provider.project,
            zone=self.provider.zone,
            body=args)
        with self.rate_limiter:
            q.execute()

    def _deleteInstance(self, server_id):
        q = self.compute.instances().delete(project=self.provider.project,
                                            zone=self.provider.zone,
                                            instance=server_id)
        with self.rate_limiter:
            q.execute()

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listInstances(self):
        q = self.compute.instances().list(project=self.provider.project,
                                          zone=self.provider.zone)
        with self.rate_limiter:
            result = q.execute()
        return result.get('items', [])

    # This method is wrapped with an LRU cache in the constructor.
    def _getImageId(self, cloud_image):
        image_id = cloud_image.image_id

        if image_id:
            return image_id

        if cloud_image.image_family:
            q = self.compute.images().getFromFamily(
                project=cloud_image.image_project,
                family=cloud_image.image_family)
            with self.rate_limiter:
                result = q.execute()
            image_id = result['selfLink']

        return image_id

    # This method is wrapped with an LRU cache in the constructor.
    def _getMachineType(self, machine_type):
        q = self.compute.machineTypes().get(
            project=self.provider.project,
            zone=self.provider.zone,
            machineType=machine_type)
        with self.rate_limiter:
            return q.execute()

    def _getQuotaForMachineType(self, machine_type):
        mtype = self._getMachineType(machine_type)
        return QuotaInformation(
            cores=mtype['guestCpus'],
            instances=1,
            ram=mtype['memoryMb'])

    def _getInstance(self, hostname):
        for instance in self._listInstances():
            if instance['name'] == hostname:
                return instance
        return None
