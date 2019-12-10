# Copyright 2018-2019 Red Hat
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
from nodepool.driver.gcloud.handler import GcloudNodeRequestHandler
from nodepool.nodeutils import iterate_timeout
import nodepool.exceptions

class GcloudInstance:
    def __init__(self, name, metadatas, provider):
        self.id = name
        self.name = name
        self.metadata = {}
        if metadatas:
            for metadata in metadatas:
                if metadata["Key"] == "nodepool_id":
                    self.metadata['nodepool_node_id'] = metadata["Value"]
                    continue
                if metadata["Key"] == "nodepool_pool":
                    self.metadata['nodepool_pool_name'] = metadata["Value"]
                    continue
                if metadata["Key"] == "nodepool_provider":
                    self.metadata['nodepool_provider_name'] = metadata["Value"]
                    continue

    def get(self, name, default=None):
        return getattr(self, name, default)


class GcloudProvider(Provider):
    log = logging.getLogger("nodepool.driver.gcloud.GcloudProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.compute = None

    def getRequestHandler(self, poolworker, request):
        return GcloudNodeRequestHandler(poolworker, request)

    def start(self, zk_conn):
        if self.compute is not None:
            return True
        self.log.debug("Starting")
        self.compute = googleapiclient.discovery.build('compute', 'v1')

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        q = self.compute.instances().list(project=self.provider.project,
                                          zone=self.provider.zone).execute()
        for instance in q.get('items', []):
            print(instance)
            continue #XXX
            if instance.state["Name"].lower() == "terminated":
                continue
            ours = False
            if instance.tags:
                for tag in instance.tags:
                    if (tag["Key"] == 'nodepool_provider'
                        and tag["Value"] == self.provider.name):
                        ours = True
                        break
            if not ours:
                continue
            servers.append(GcloudInstance(
                instance.id, instance.tags, self.provider))
        return servers

    def countNodes(self, pool=None):
        n = 0
        for instance in self.listNodes():
            if pool is not None:
                if 'nodepool_pool_name' not in instance.metadata:
                    continue
                if pool != instance.metadata['nodepool_pool_name']:
                    continue
            n += 1
        return n

    def getLatestImageIdByFilters(self, image_filters):
        res = self.gcloud.describe_images(
            Filters=image_filters
        ).get("Images")

        images = sorted(
            res,
            key=lambda k: k["CreationDate"],
            reverse=True
        )

        if not images:
            msg = "No cloud-image (AMI) matches supplied image filters"
            raise Exception(msg)
        else:
            return images[0].get("ImageId")

    def getImageId(self, cloud_image):
        image_id = cloud_image.image_id

        if image_id:
            return image_id

        if cloud_image.image_family:
            q = self.compute.images().getFromFamily(
                project=cloud_image.image_project,
                family=cloud_image.image_family).execute()
            image_id = q['selfLink']

        return image_id

    def labelReady(self, label):
        if not label.cloud_image:
            msg = "A cloud-image (AMI) must be supplied with the GCLOUD driver."
            raise Exception(msg)

        image = self.getImage(label.cloud_image)
        # Image loading is deferred, check if it's really there
        if image.state != 'available':
            self.log.warning(
                "Provider %s is configured to use %s as the AMI for"
                " label %s and that AMI is there but unavailable in the"
                " cloud." % (self.provider.name,
                             label.cloud_image.external_name,
                             label.name))
            return False
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def getInstance(self, server_id):
        try:
            return self.compute.instances().get(
                project=self.provider.project,
                zone=self.provider.zone,
                instance=server_id).execute()
        except googleapiclient.errors.HttpError as e:
            if e.resp.status == 404:
                return None
            raise

    def cleanupNode(self, server_id):
        if self.getInstance(server_id) is None:
            return

        self.compute.instances().delete(project=self.provider.project,
                                        zone=self.provider.zone,
                                        instance=server_id).execute()

    def waitForNodeCleanup(self, server_id, timeout=600):
        for count in iterate_timeout(
                timeout, nodepool.exceptions.ServerDeleteException,
                "server %s deletion" % server_id):
            if self.getInstance(server_id) is None:
                return

    def createInstance(self, hostname, label,
                       nodepool_node_id):
        image_id = self.getImageId(label.cloud_image)
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
                                   value=nodepool_node_id))
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
            body=args).execute()
        return q

    def checkOperation(self, name):
        q = self.compute.zoneOperations().get(project=self.provider.project,
                                              zone=self.provider.zone,
                                              operation=name).execute()
        if q['status'] == 'Done':
            if 'error' in q:
                raise Exception(q['error'])
            return True
        return False
