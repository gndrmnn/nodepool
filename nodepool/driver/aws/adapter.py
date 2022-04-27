# Copyright 2018 Red Hat
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

import json
import logging
import math
import cachetools.func
import urllib.parse
import time
import re

import boto3

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine


def tag_dict_to_list(tagdict):
    # TODO: validate tag values are strings in config and deprecate
    # non-string values.
    return [{"Key": k, "Value": str(v)} for k, v in tagdict.items()]


def tag_list_to_dict(taglist):
    if taglist is None:
        return {}
    return {t["Key"]: t["Value"] for t in taglist}


class AwsInstance(statemachine.Instance):
    def __init__(self, instance, quota):
        super().__init__()
        self.external_id = instance.id
        self.metadata = tag_list_to_dict(instance.tags)
        self.private_ipv4 = instance.private_ip_address
        self.private_ipv6 = None
        self.public_ipv4 = instance.public_ip_address
        self.public_ipv6 = None
        self.az = ''
        self.quota = quota

        for iface in instance.network_interfaces[:1]:
            if iface.ipv6_addresses:
                v6addr = iface.ipv6_addresses[0]
                self.public_ipv6 = v6addr['Ipv6Address']
        self.interface_ip = (self.public_ipv4 or self.public_ipv6 or
                             self.private_ipv4 or self.private_ipv6)

    def getQuotaInformation(self):
        return self.quota


class AwsResource(statemachine.Resource):
    def __init__(self, metadata, type, id):
        super().__init__(metadata)
        self.type = type
        self.id = id


class AwsDeleteStateMachine(statemachine.StateMachine):
    VM_DELETING = 'deleting vm'
    NIC_DELETING = 'deleting nic'
    PIP_DELETING = 'deleting pip'
    DISK_DELETING = 'deleting disk'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id

    def advance(self):
        if self.state == self.START:
            self.instance = self.adapter._deleteInstance(
                self.external_id, self.log)
            self.state = self.VM_DELETING

        if self.state == self.VM_DELETING:
            self.instance = self.adapter._refreshDelete(self.instance)
            if self.instance is None:
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True


class AwsCreateStateMachine(statemachine.StateMachine):
    INSTANCE_CREATING = 'creating instance'
    INSTANCE_RETRY = 'retrying instance creation'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.attempts = 0
        self.image_external_id = image_external_id
        self.metadata = metadata
        self.tags = label.tags.copy() or {}
        self.tags.update(metadata)
        self.tags['Name'] = hostname
        self.hostname = hostname
        self.label = label
        self.public_ipv4 = None
        self.public_ipv6 = None
        self.nic = None
        self.instance = None

    def advance(self):
        if self.state == self.START:
            self.external_id = self.hostname

            self.instance = self.adapter._createInstance(
                self.label, self.image_external_id,
                self.tags, self.hostname, self.log)
            self.state = self.INSTANCE_CREATING

        if self.state == self.INSTANCE_CREATING:
            self.quota = self.adapter._getQuotaForInstanceType(
                self.instance.instance_type)
            self.instance = self.adapter._refresh(self.instance)

            if self.instance.state["Name"].lower() == "running":
                self.state = self.COMPLETE
            elif self.instance.state["Name"].lower() == "terminated":
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                self.instance = self.adapter._deleteInstance(
                    self.external_id, self.log)
                self.state = self.INSTANCE_RETRY
            else:
                return

        if self.state == self.INSTANCE_RETRY:
            self.instance = self.adapter._refreshDelete(self.instance)
            if self.instance is None:
                self.state = self.START
                return

        if self.state == self.COMPLETE:
            self.complete = True
            return AwsInstance(self.instance, self.quota)


class AwsAdapter(statemachine.Adapter):
    IMAGE_UPLOAD_SLEEP = 30

    def __init__(self, provider_config):
        self.log = logging.getLogger(
            f"nodepool.AwsAdapter.{provider_config.name}")
        self.provider = provider_config
        # The standard rate limit, this might be 1 request per second
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)
        # Non mutating requests can be made more often at 10x the rate
        # of mutating requests by default.
        self.non_mutating_rate_limiter = RateLimiter(self.provider.name,
                                                     self.provider.rate * 10.0)
        self.image_id_by_filter_cache = cachetools.TTLCache(
            maxsize=8192, ttl=(5 * 60))
        self.aws = boto3.Session(
            region_name=self.provider.region_name,
            profile_name=self.provider.profile_name)
        self.ec2 = self.aws.resource('ec2')
        self.ec2_client = self.aws.client("ec2")
        self.s3 = self.aws.resource('s3')
        self.s3_client = self.aws.client('s3')
        self.aws_quotas = self.aws.client("service-quotas")
        # In listResources, we reconcile AMIs which appear to be
        # imports but have no nodepool tags, however it's possible
        # that these aren't nodepool images.  If we determine that's
        # the case, we'll add their ids here so we don't waste our
        # time on that again.
        self.not_our_images = set()
        self.not_our_snapshots = set()

    def getCreateStateMachine(self, hostname, label,
                              image_external_id, metadata, retries, log):
        return AwsCreateStateMachine(self, hostname, label,
                                     image_external_id, metadata, retries, log)

    def getDeleteStateMachine(self, external_id, log):
        return AwsDeleteStateMachine(self, external_id, log)

    def listResources(self):
        self._tagAmis()
        self._tagSnapshots()
        for instance in self._listInstances():
            if instance.state["Name"].lower() == "terminated":
                continue
            yield AwsResource(tag_list_to_dict(instance.tags),
                              'instance', instance.id)
        for volume in self._listVolumes():
            if volume.state.lower() == "deleted":
                continue
            yield AwsResource(tag_list_to_dict(volume.tags),
                              'volume', volume.id)
        for ami in self._listAmis():
            if ami.state.lower() == "deleted":
                continue
            yield AwsResource(tag_list_to_dict(ami.tags),
                              'ami', ami.id)
        for snap in self._listSnapshots():
            if snap.state.lower() == "deleted":
                continue
            yield AwsResource(tag_list_to_dict(snap.tags),
                              'snapshot', snap.id)
        if self.provider.object_storage:
            for obj in self._listObjects():
                with self.non_mutating_rate_limiter:
                    tags = self.s3_client.get_object_tagging(
                        Bucket=obj.bucket_name, Key=obj.key)
                yield AwsResource(tag_list_to_dict(tags['TagSet']),
                                  'object', obj.key)

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.id}")
        if resource.type == 'instance':
            self._deleteInstance(resource.id)
        if resource.type == 'volume':
            self._deleteVolume(resource.id)
        if resource.type == 'ami':
            self._deleteAmi(resource.id)
        if resource.type == 'snapshot':
            self._deleteSnapshot(resource.id)
        if resource.type == 'object':
            self._deleteObject(resource.id)

    def listInstances(self):
        for instance in self._listInstances():
            if instance.state["Name"].lower() == "terminated":
                continue
            quota = self._getQuotaForInstanceType(instance.instance_type)
            yield AwsInstance(instance, quota)

    def getQuotaLimits(self):
        with self.non_mutating_rate_limiter:
            self.log.debug("Getting quota limits")
            response = self.aws_quotas.get_service_quota(
                ServiceCode='ec2',
                QuotaCode='L-1216C47A'
            )
            cores = response['Quota']['Value']
        return QuotaInformation(cores=cores,
                                default=math.inf)

    def getQuotaForLabel(self, label):
        return self._getQuotaForInstanceType(label.instance_type)

    def uploadImage(self, provider_image, image_name, filename,
                    image_format, metadata, md5, sha256):
        self.log.debug(f"Uploading image {image_name}")

        # Upload image to S3
        bucket_name = self.provider.object_storage['bucket-name']
        bucket = self.s3.Bucket(bucket_name)
        object_filename = f'{image_name}.{image_format}'
        extra_args = {'Tagging': urllib.parse.urlencode(metadata)}
        with open(filename, "rb") as fobj:
            with self.rate_limiter:
                bucket.upload_fileobj(fobj, object_filename,
                                      ExtraArgs=extra_args)

        # Import image as AMI
        self.log.debug(f"Importing {image_name}")
        import_image_task = self._import_image(
            Architecture=provider_image.architecture,
            DiskContainers=[
                {
                    'Format': image_format,
                    'UserBucket': {
                        'S3Bucket': bucket_name,
                        'S3Key': object_filename,
                    }
                },
            ],
            TagSpecifications=[
                {
                    'ResourceType': 'import-image-task',
                    'Tags': tag_dict_to_list(metadata),
                },
            ]
        )
        task_id = import_image_task['ImportTaskId']

        paginator = self._get_paginator('describe_import_image_tasks')
        done = False
        while not done:
            time.sleep(self.IMAGE_UPLOAD_SLEEP)
            with self.non_mutating_rate_limiter:
                for page in paginator.paginate(ImportTaskIds=[task_id]):
                    for task in page['ImportImageTasks']:
                        if task['Status'].lower() in ('completed', 'deleted'):
                            done = True
                            break

        self.log.debug(f"Deleting {image_name} from S3")
        with self.rate_limiter:
            self.s3.Object(bucket_name, object_filename).delete()

        if task['Status'].lower() != 'completed':
            raise Exception(f"Error uploading image: {task}")

        # Tag the AMI
        try:
            with self.non_mutating_rate_limiter:
                ami = self.ec2.Image(task['ImageId'])
            with self.rate_limiter:
                ami.create_tags(Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging AMI:")

        # Tag the snapshot
        try:
            with self.non_mutating_rate_limiter:
                snap = self.ec2.Snapshot(
                    task['SnapshotDetails'][0]['SnapshotId'])
            with self.rate_limiter:
                snap.create_tags(Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging snapshot:")

        self.log.debug(f"Upload of {image_name} complete as {task['ImageId']}")
        # Last task returned from paginator above
        return task['ImageId']

    def deleteImage(self, external_id):
        snaps = set()
        self.log.debug(f"Deleting image {external_id}")
        for ami in self._listAmis():
            if ami.id == external_id:
                for bdm in ami.block_device_mappings:
                    snapid = bdm.get('Ebs', {}).get('SnapshotId')
                    if snapid:
                        snaps.add(snapid)
        self._deleteAmi(external_id)
        for snapshot_id in snaps:
            self._deleteSnapshot(snapshot_id)

    # Local implementation below

    def _tagAmis(self):
        # There is no way to tag imported AMIs, so this routine
        # "eventually" tags them.  We look for any AMIs without tags
        # which correspond to import tasks, and we copy the tags from
        # those import tasks to the AMI.
        for ami in self._listAmis():
            if (ami.name.startswith('import-ami-') and
                not ami.tags and
                ami.id not in self.not_our_images):
                # This image was imported but has no tags, which means
                # it's either not a nodepool image, or it's a new one
                # which doesn't have tags yet.  Copy over any tags
                # from the import task; otherwise, mark it as an image
                # we can ignore in future runs.
                task = self._getImportImageTask(ami.name)
                tags = tag_list_to_dict(task.get('Tags'))
                if (tags.get('nodepool_provider_name') == self.provider.name):
                    # Copy over tags
                    self.log.debug(
                        f"Copying tags from import task {ami.name} to AMI")
                    with self.rate_limiter:
                        ami.create_tags(Tags=task['Tags'])
                else:
                    self.not_our_images.add(ami.id)

    def _tagSnapshots(self):
        # See comments for _tagAmis
        for snap in self._listSnapshots():
            if ('import-ami-' in snap.description and
                not snap.tags and
                snap.id not in self.not_our_snapshots):

                match = re.match(r'.*?(import-ami-\w*)', snap.description)
                if not match:
                    self.not_our_snapshots.add(snap.id)
                    continue
                task_id = match.group(1)
                task = self._getImportImageTask(task_id)
                tags = tag_list_to_dict(task.get('Tags'))
                if (tags.get('nodepool_provider_name') == self.provider.name):
                    # Copy over tags
                    self.log.debug(
                        f"Copying tags from import task {task_id} to snapshot")
                    with self.rate_limiter:
                        snap.create_tags(Tags=task['Tags'])
                else:
                    self.not_our_snapshots.add(snap.id)

    def _getImportImageTask(self, task_id):
        paginator = self._get_paginator('describe_import_image_tasks')
        with self.non_mutating_rate_limiter:
            for page in paginator.paginate(ImportTaskIds=[task_id]):
                for task in page['ImportImageTasks']:
                    # Return the first and only task
                    return task

    def _getQuotaForInstanceType(self, instance_type):
        itype = self._getInstanceType(instance_type)
        cores = itype['InstanceTypes'][0]['VCpuInfo']['DefaultCores']
        ram = itype['InstanceTypes'][0]['MemoryInfo']['SizeInMiB']
        return QuotaInformation(cores=cores,
                                ram=ram,
                                instances=1)

    @cachetools.func.lru_cache(maxsize=None)
    def _getInstanceType(self, instance_type):
        with self.non_mutating_rate_limiter:
            self.log.debug(
                f"Getting information for instance type {instance_type}")
            return self.ec2_client.describe_instance_types(
                InstanceTypes=[instance_type])

    def _refresh(self, obj):
        for instance in self._listInstances():
            if instance.id == obj.id:
                return instance

    def _refreshDelete(self, obj):
        if obj is None:
            return obj

        for instance in self._listInstances():
            if instance.id == obj.id:
                if instance.state["Name"].lower() == "terminated":
                    return None
                return instance
        return None

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listInstances(self):
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed instances"):
            return self.ec2.instances.all()

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listVolumes(self):
        with self.non_mutating_rate_limiter:
            return self.ec2.volumes.all()

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listAmis(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter:
            return self.ec2.images.filter(Owners=['self'])

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listSnapshots(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter:
            return self.ec2.snapshots.filter(OwnerIds=['self'])

    @cachetools.func.ttl_cache(maxsize=1, ttl=10)
    def _listObjects(self):
        bucket_name = self.provider.object_storage.get('bucket-name')
        if not bucket_name:
            return []

        bucket = self.s3.Bucket(bucket_name)
        with self.non_mutating_rate_limiter:
            return bucket.objects.all()

    def _getLatestImageIdByFilters(self, image_filters):
        # Normally we would decorate this method, but our cache key is
        # complex, so we serialize it to JSON and manage the cache
        # ourselves.
        cache_key = json.dumps(image_filters)
        val = self.image_id_by_filter_cache.get(cache_key)
        if val:
            return val

        with self.non_mutating_rate_limiter:
            res = self.ec2_client.describe_images(
                Filters=image_filters
            ).get("Images")

        images = sorted(
            res,
            key=lambda k: k["CreationDate"],
            reverse=True
        )

        if not images:
            raise Exception(
                "No cloud-image (AMI) matches supplied image filters")
        else:
            val = images[0].get("ImageId")
            self.image_id_by_filter_cache[cache_key] = val
            return val

    def _getImageId(self, cloud_image):
        image_id = cloud_image.image_id
        image_filters = cloud_image.image_filters

        if image_filters is not None:
            return self._getLatestImageIdByFilters(image_filters)

        return image_id

    @cachetools.func.lru_cache(maxsize=None)
    def _getImage(self, image_id):
        with self.non_mutating_rate_limiter:
            return self.ec2.Image(image_id)

    def _createInstance(self, label, image_external_id,
                        tags, hostname, log):
        if image_external_id:
            image_id = image_external_id
        else:
            image_id = self._getImageId(label.cloud_image)

        args = dict(
            ImageId=image_id,
            MinCount=1,
            MaxCount=1,
            KeyName=label.key_name,
            EbsOptimized=label.ebs_optimized,
            InstanceType=label.instance_type,
            NetworkInterfaces=[{
                'AssociatePublicIpAddress': label.pool.public_ipv4,
                'DeviceIndex': 0}],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': tag_dict_to_list(tags),
                },
                {
                    'ResourceType': 'volume',
                    'Tags': tag_dict_to_list(tags),
                },
            ]
        )

        if label.pool.security_group_id:
            args['NetworkInterfaces'][0]['Groups'] = [
                label.pool.security_group_id
            ]
        if label.pool.subnet_id:
            args['NetworkInterfaces'][0]['SubnetId'] = label.pool.subnet_id

        if label.pool.public_ipv6:
            args['NetworkInterfaces'][0]['Ipv6AddressCount'] = 1

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
        image = self._getImage(image_id)
        # TODO: Flavors can also influence whether or not the VM spawns with a
        # volume -- we basically need to ensure DeleteOnTermination is true.
        # However, leaked volume detection may mitigate this.
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

        with self.rate_limiter(log.debug, "Created instance"):
            log.debug(f"Creating VM {hostname}")
            instances = self.ec2.create_instances(**args)
            log.debug(f"Created VM {hostname} as instance {instances[0].id}")
            return self.ec2.Instance(instances[0].id)

    def _deleteInstance(self, external_id, log=None):
        if log is None:
            log = self.log
        for instance in self._listInstances():
            if instance.id == external_id:
                break
        else:
            log.warning(f"Instance not found when deleting {external_id}")
            return None
        with self.rate_limiter(log.debug, "Deleted instance"):
            log.debug(f"Deleting instance {external_id}")
            instance.terminate()
        return instance

    def _deleteVolume(self, external_id):
        for volume in self._listVolumes():
            if volume.id == external_id:
                break
        else:
            self.log.warning(f"Volume not found when deleting {external_id}")
            return None
        with self.rate_limiter(self.log.debug, "Deleted volume"):
            self.log.debug(f"Deleting volume {external_id}")
            volume.delete()
        return volume

    def _deleteAmi(self, external_id):
        for ami in self._listAmis():
            if ami.id == external_id:
                break
        else:
            self.log.warning(f"AMI not found when deleting {external_id}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting AMI {external_id}")
            ami.deregister()
        return ami

    def _deleteSnapshot(self, external_id):
        for snap in self._listSnapshots():
            if snap.id == external_id:
                break
        else:
            self.log.warning(f"Snapshot not found when deleting {external_id}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting Snapshot {external_id}")
            snap.delete()
        return snap

    def _deleteObject(self, external_id):
        bucket_name = self.provider.object_storage.get('bucket-name')
        with self.rate_limiter:
            self.log.debug(f"Deleting object {external_id}")
            self.s3.Object(bucket_name, external_id).delete()

    # These methods allow the tests to patch our use of boto to
    # compensate for missing methods in the boto mocks.
    def _import_image(self, *args, **kw):
        return self.ec2_client.import_image(*args, **kw)

    def _get_paginator(self, *args, **kw):
        return self.ec2_client.get_paginator(*args, **kw)
    # End test methods
