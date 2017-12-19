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

from nodepool.driver import Provider
from nodepool.driver.aws.handler import AwsNodeRequestHandler


class AwsInstance:
    def __init__(self, name, metadatas, provider):
        self.id = name
        self.name = name
        self.metadata = {}
        if metadatas:
            for metadata in metadatas:
                if metadata["Key"] == "nodepool_id":
                    self.metadata = {
                        'nodepool_provider_name': provider.name,
                        'nodepool_node_id': metadata["Value"],
                    }
                    break

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
        if self.ec2 is not None:
            return True
        self.log.debug("Starting")

        self.ec2 = boto3.resource('ec2', region_name=self.provider.region)

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        for instance in self.ec2.instances.all():
            if instance.state["Name"].lower() == "terminated":
                continue
            servers.append(AwsInstance(
                instance.id, instance.tags, self.provider))
        return servers

    def getImage(self, image_id):
        return self.ec2.Image(image_id)

    def labelReady(self, label):
        if not label.ami:
            raise Exception("An AMI must be supplied with the AWS driver.")

        image = self.getImage(label.ami)
        # Image loading is deferred, check if it's really there
        try:
            if image.state != 'available':
                self.log.warning(
                    "Provider %s is configured to use %s as the AMI for"
                    " label %s and that AMI is there but unavailable in the"
                    " cloud." % (self.provider.name,
                                label.ami,
                                label.name))
                return False
        except botocore.exceptions.ClientError as e:
            self.log.warning(
                "Provider %s is configured to use %s as the AMI for"
                " label %s and that AMI could not be found in the"
                " cloud." % (self.provider.name,
                             label.ami,
                             label.name))
            return False
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

    def cleanupNode(self, server_id):
        if self.ec2 is None:
            return False
        instance = self.ec2.Instance(server_id)
        instance.terminate()

    def waitForNodeCleanup(self, server_id):
        # TODO: track instance deletion
        return True

    def createInstance(self, label):
        # Default block device mapping parameters are embedded in AMIs.
        # We might need to supply our own mapping before lauching the instance.
        block_device_mappings = self.getImage(label.ami).block_device_mappings
        # We're only expecting one block device
        if len(block_device_mappings) > 1:
            raise Exception("The AMI %s has more than one block device")

        mapping = block_device_mappings[0]
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

        args = dict(
            ImageId=label.ami,
            MinCount=1,
            MaxCount=1,
            KeyName=label.key_name,
            InstanceType=label.flavor,
            BlockDeviceMappings=[mapping],
            NetworkInterfaces=[{
                'AssociatePublicIpAddress': True,
                'DeviceIndex': 0}])
        if label.pool.security_group:
            args['NetworkInterfaces'][0]['Groups'] = [
                label.pool.security_group
            ]
            args['NetworkInterfaces'][0]['SubnetId'] = label.pool.subnet
        instances = self.ec2.create_instances(**args)
        return self.ec2.Instance(instances[0].id)
