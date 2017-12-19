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
import boto3

from nodepool.driver import Provider

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
        self.zuul_public_key = provider.zuul_public_key
        self.ec2 = None

    def start(self):
        if self.ec2 is not None:
            return True
        self.log.debug("Starting")

        raw_ec2_client = boto3.client('ec2', region_name=self.provider.region)

        keypairs = raw_ec2_client.describe_key_pairs()['KeyPairs']
        if not [True for keypair in keypairs if keypair['KeyName'] == 'Zuul']:
            # Create key pair
            raw_ec2_client.import_key_pair(
                KeyName='Zuul',
                PublicKeyMaterial=self.zuul_public_key.encode('ascii'))

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

    def labelReady(self, name):
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
        instances = self.ec2.create_instances(
            ImageId=label.ami,
            MinCount=1,
            MaxCount=1,
            KeyName="Zuul",
            InstanceType=label.flavor,
            NetworkInterfaces=[{
                'AssociatePublicIpAddress': True,
                'DeviceIndex': 0}])
        return self.ec2.Instance(instances[0].id)
