# Copyright 2018 Red Hat
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
import boto3
import botocore.exceptions
import nodepool.exceptions

from nodepool.driver import Provider
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.aws.handler import AwsNodeRequestHandler


class AwsInstance:
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


class AwsProvider(Provider):
    log = logging.getLogger("nodepool.driver.aws.AwsProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ec2 = None

    def getRequestHandler(self, poolworker, request):
        return AwsNodeRequestHandler(poolworker, request)

    def start(self, zk_conn):
        self._zk = zk_conn
        if self.ec2 is not None:
            return True
        self.log.debug("Starting")
        self.aws = boto3.Session(
            region_name=self.provider.region_name,
            profile_name=self.provider.profile_name)
        self.ec2 = self.aws.resource('ec2')
        self.ec2_client = self.aws.client("ec2")

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        for instance in self.ec2.instances.all():
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
            servers.append(AwsInstance(
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
        res = self.ec2_client.describe_images(
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
        image_filters = cloud_image.image_filters

        if image_filters is not None:
            if image_id is not None:
                msg = "image-id and image-filters cannot by used together"
                raise Exception(msg)
            else:
                return self.getLatestImageIdByFilters(image_filters)

        return image_id

    def getImage(self, cloud_image):
        return self.ec2.Image(self.getImageId(cloud_image))

    def labelReady(self, label):
        if not label.cloud_image:
            msg = "A cloud-image (AMI) must be supplied with the AWS driver."
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

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        if self.ec2 is None:
            return False
        instance = self.ec2.Instance(server_id)
        try:
            instance.terminate()
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == "InvalidInstanceID.NotFound":
                raise nodepool.exceptions.NotFound()
            raise e

    def waitForNodeCleanup(self, server_id):
        # TODO: track instance deletion
        return True

    def createInstance(self, label):
        image_id = self.getImageId(label.cloud_image)
        tags = label.tags
        if not [tag for tag in label.tags if tag["Key"] == "Name"]:
            tags.append(
                {"Key": "Name", "Value": str(label.name)}
            )
        args = dict(
            ImageId=image_id,
            MinCount=1,
            MaxCount=1,
            KeyName=label.key_name,
            EbsOptimized=label.ebs_optimized,
            InstanceType=label.instance_type,
            NetworkInterfaces=[{
                'AssociatePublicIpAddress': label.pool.public_ip,
                'DeviceIndex': 0}],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': tags
            }]
        )

        if label.pool.security_group_id:
            args['NetworkInterfaces'][0]['Groups'] = [
                label.pool.security_group_id
            ]
        if label.pool.subnet_id:
            args['NetworkInterfaces'][0]['SubnetId'] = label.pool.subnet_id

        if label.userdata:
            args['UserData'] = label.userdata

        if label.iam_instance_profile:
            if 'name' in label.iam_instance_profile:
                args['IamInstanceProfile'] = {
                    'Name': label.iam_instance_profile['name']
                }
            elif 'arn' in label.iam_instance_profile:
                args['IamInstanceProfile'] = {
                    'Arn': label.iam_instance_profile['arn']
                }

        # Default block device mapping parameters are embedded in AMIs.
        # We might need to supply our own mapping before lauching the instance.
        # We basically want to make sure DeleteOnTermination is true and be
        # able to set the volume type and size.
        image = self.getImage(label.cloud_image)
        # TODO: Flavors can also influence whether or not the VM spawns with a
        # volume -- we basically need to ensure DeleteOnTermination is true
        if hasattr(image, 'block_device_mappings'):
            bdm = image.block_device_mappings
            mapping = bdm[0]
            if 'Ebs' in mapping:
                mapping['Ebs']['DeleteOnTermination'] = True
                if label.volume_size:
                    mapping['Ebs']['VolumeSize'] = label.volume_size
                if label.volume_type:
                    mapping['Ebs']['VolumeType'] = label.volume_type
                # If the AMI is a snapshot, we cannot supply an "encrypted"
                # parameter
                if 'Encrypted' in mapping['Ebs']:
                    del mapping['Ebs']['Encrypted']
                args['BlockDeviceMappings'] = [mapping]

        instances = self.ec2.create_instances(**args)
        return self.ec2.Instance(instances[0].id)
