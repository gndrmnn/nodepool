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
import time

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
        self.zk = None

    def getRequestHandler(self, poolworker, request):
        return AwsNodeRequestHandler(poolworker, request)

    def start(self, zk_conn):
        self.zk = zk_conn
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
        if label.diskimage:
            diskimage = self.provider.diskimages[label.diskimage.name]
            image_upload = self.zk.getMostRecentImageUpload(
                diskimage.name, self.provider.name)
            if not image_upload:
                self.log.debug("Label %s not yet uploaded in %s",
                               label.name, self.provider.name)
                return False
            image = self.getImage(image_upload.external_id)
        elif label.cloud_image:
            image = self.getImage(label.cloud_image)
        else:
            return False

        # Image loading is deferred, check if it's really there
        if image.state != 'available':
            self.log.warning(
                "Provider %s is configured to use %s as the AMI for"
                " label %s and that AMI is there but unavailable in the"
                " cloud." % (self.provider.name,
                             image.image_id,
                             label.name))
            return False
        return True

    def uploadImage(self, image_name, filename, image_type=None, meta=None,
                    md5=None, sha256=None):
        s3_client = self.aws.client("s3")
        dest = image_name
        if self.provider.s3_image_basedir:
            dest = self.provider.s3_image_basedir + '/' + dest
        self.log.debug("Uploading image %s to %s/%s",
                       filename,
                       self.provider.s3_image_bucket,
                       dest)
        s3_client.upload_file(
            filename,
            self.provider.s3_image_bucket,
            dest)
        args = dict(
            DiskContainers=[dict(
                UserBucket=dict(
                    S3Bucket=self.provider.s3_image_bucket,
                    S3Key=dest,
                )
            )],
            RoleName=self.provider.ec2_vm_import_role
        )

        import_image_task = self.ec2_client.import_image(**args)
        import_task_id = import_image_task['ImportTaskId']

        progress = ''
        import_task_running = True
        while import_task_running:
            status = import_image_task['Status']
            if status in ('completed', 'deleted'):
                import_task_running = False
            else:
                if progress != import_image_task['Progress']:
                    statusmessage = import_image_task['StatusMessage']
                    progress = import_image_task['Progress']
                    self.log.debug("Importing image: %s %s%%: %s",
                                   status, progress, statusmessage)
                time.sleep(30)
                response = self.ec2_client.describe_import_image_tasks(
                    ImportTaskIds=[import_task_id]
                )
                import_image_task = response['ImportImageTasks'][0]

        s3_client.delete_object(Bucket=self.provider.s3_image_bucket,
                                Key=dest)

        if status == 'deleted':
            raise ValueError(import_image_task['StatusMessage'])

        image_id = import_image_task['ImageId']

        if status == 'completed':
            tags = [{'Key': k, 'Value': v} for k, v in meta.items()]
            tags.append({'Key': 'Name', 'Value': image_name})
            self.ec2_client.create_tags(
                Resources=[image_id],
                Tags=tags
            )

        return image_id

    def deleteImage(self, image_name, image_id):
        self.log.debug("Deregistering image %s: %s", image_name, image_id)
        self.ec2_client.deregister_image(image_id)

    def join(self):
        return True

    def cleanupLeakedResources(self):
        # TODO: remove leaked resources if any
        pass

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
