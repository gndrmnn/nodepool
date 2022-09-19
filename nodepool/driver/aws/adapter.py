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

from concurrent.futures import ThreadPoolExecutor
import cachetools.func
import functools
import json
import logging
import math
import re
import threading
import queue
import time
import urllib.parse

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine

import boto3
import botocore.exceptions


def tag_dict_to_list(tagdict):
    # TODO: validate tag values are strings in config and deprecate
    # non-string values.
    return [{"Key": k, "Value": str(v)} for k, v in tagdict.items()]


def tag_list_to_dict(taglist):
    if taglist is None:
        return {}
    return {t["Key"]: t["Value"] for t in taglist}


# This is a map of instance types to quota codes.  There does not
# appear to be an automated way to determine what quota code to use
# for an instance type, therefore this list was manually created by
# visiting
# https://us-west-1.console.aws.amazon.com/servicequotas/home/services/ec2/quotas
# and filtering by "Instances".  An example description is "Running
# On-Demand P instances" which we can infer means we should use that
# quota code for instance types starting with the letter "p".  All
# instance type names follow the format "([a-z\-]+)\d", so we can
# match the first letters (up to the first number) of the instance
# type name with the letters in the quota name.  The prefix "u-" for
# "Running On-Demand High Memory instances" was determined from
# https://aws.amazon.com/ec2/instance-types/high-memory/

QUOTA_CODES = {
    'a': 'L-1216C47A',
    'c': 'L-1216C47A',
    'd': 'L-1216C47A',
    'h': 'L-1216C47A',
    'i': 'L-1216C47A',
    'm': 'L-1216C47A',
    'r': 'L-1216C47A',
    't': 'L-1216C47A',
    'z': 'L-1216C47A',
    'dl': 'L-6E869C2A',
    'f': 'L-74FC7D96',
    'g': 'L-DB2E81BA',
    'vt': 'L-DB2E81BA',
    'u-': 'L-43DA4232',  # 'high memory'
    'inf': 'L-1945791B',
    'p': 'L-417A185B',
    'x': 'L-7295265B',
}

CACHE_TTL = 10


class AwsInstance(statemachine.Instance):
    def __init__(self, provider, instance, quota):
        super().__init__()
        self.external_id = instance.id
        self.metadata = tag_list_to_dict(instance.tags)
        self.private_ipv4 = instance.private_ip_address
        self.private_ipv6 = None
        self.public_ipv4 = instance.public_ip_address
        self.public_ipv6 = None
        self.cloud = 'AWS'
        self.region = provider.region_name
        self.az = instance.subnet.availability_zone
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
    INSTANCE_CREATING_SUBMIT = 'submit creating instance'
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
        self.tags = label.tags.copy() or {}
        for k, v in label.dynamic_tags.items():
            try:
                self.tags[k] = v.format(request=request.getSafeAttributes())
            except Exception:
                self.log.exception("Error formatting tag %s", k)
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
            self.create_future = self.adapter._submitCreateInstance(
                self.label, self.image_external_id,
                self.tags, self.hostname, self.log)
            self.state = self.INSTANCE_CREATING_SUBMIT

        if self.state == self.INSTANCE_CREATING_SUBMIT:
            instance = self.adapter._completeCreateInstance(self.create_future)
            if instance is None:
                return
            self.instance = instance
            self.quota = self.adapter._getQuotaForInstanceType(
                self.instance.instance_type)
            self.state = self.INSTANCE_CREATING

        if self.state == self.INSTANCE_CREATING:
            self.instance = self.adapter._refresh(self.instance)

            if self.instance.state["Name"].lower() == "running":
                self.state = self.COMPLETE
            elif self.instance.state["Name"].lower() == "terminated":
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                self.instance = self.adapter._deleteInstance(
                    self.external_id, self.log, immediate=True)
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
            return AwsInstance(self.adapter.provider, self.instance,
                               self.quota)


class AwsAdapter(statemachine.Adapter):
    IMAGE_UPLOAD_SLEEP = 30

    def __init__(self, provider_config):
        # Wrap these instance methods with a per-instance LRU cache so
        # that we don't leak memory over time when the adapter is
        # occasionally replaced.
        self._getInstanceType = functools.lru_cache(maxsize=None)(
            self._getInstanceType)
        self._getImage = functools.lru_cache(maxsize=None)(
            self._getImage)

        self.log = logging.getLogger(
            f"nodepool.AwsAdapter.{provider_config.name}")
        self.provider = provider_config
        self._running = True

        # AWS has a default rate limit for creating instances that
        # works out to a sustained 2 instances/sec, but the actual
        # create instance API call takes 1 second or more.  If we want
        # to achieve faster than 1 instance/second throughput, we need
        # to parallelize create instance calls, so we set up a
        # threadworker to do that.

        # A little bit of a heuristic here to set the worker count.
        # It appears that AWS typically takes 1-1.5 seconds to execute
        # a create API call.  Figure out how many we have to do in
        # parallel in order to run at the rate limit, then quadruple
        # that for headroom.  Max out at 8 so we don't end up with too
        # many threads.  In practice, this will be 8 with the default
        # values, and only less if users slow down the rate.
        workers = max(min(int(self.provider.rate * 4), 8), 1)
        self.log.info("Create executor with max workers=%s", workers)
        self.create_executor = ThreadPoolExecutor(max_workers=workers)

        # We can batch delete instances using the AWS API, so to do
        # that, create a queue for deletes, and a thread to process
        # the queue.  It will be greedy and collect as many pending
        # instance deletes as possible to delete together.  Typically
        # under load, that will mean a single instance delete followed
        # by larger batches.  That strikes a balance between
        # responsiveness and efficiency.  Reducing the overall number
        # of requests leaves more time for create instance calls.
        self.delete_queue = queue.Queue()
        self.delete_thread = threading.Thread(target=self._deleteThread)
        self.delete_thread.daemon = True
        self.delete_thread.start()

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

    def stop(self):
        self.create_executor.shutdown()
        self._running = False

    def getCreateStateMachine(self, hostname, label, image_external_id,
                              metadata, retries, request, log):
        return AwsCreateStateMachine(self, hostname, label, image_external_id,
                                     metadata, retries, request, log)

    def getDeleteStateMachine(self, external_id, log):
        return AwsDeleteStateMachine(self, external_id, log)

    def listResources(self):
        self._tagSnapshots()
        self._tagAmis()
        for instance in self._listInstances():
            try:
                if instance.state["Name"].lower() == "terminated":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(instance.tags),
                              'instance', instance.id)
        for volume in self._listVolumes():
            try:
                if volume.state.lower() == "deleted":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(volume.tags),
                              'volume', volume.id)
        for ami in self._listAmis():
            try:
                if ami.state.lower() == "deleted":
                    continue
            except (botocore.exceptions.ClientError, AttributeError):
                continue
            yield AwsResource(tag_list_to_dict(ami.tags),
                              'ami', ami.id)
        for snap in self._listSnapshots():
            try:
                if snap.state.lower() == "deleted":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(snap.tags),
                              'snapshot', snap.id)
        if self.provider.object_storage:
            for obj in self._listObjects():
                with self.non_mutating_rate_limiter:
                    try:
                        tags = self.s3_client.get_object_tagging(
                            Bucket=obj.bucket_name, Key=obj.key)
                    except botocore.exceptions.ClientError:
                        continue
                yield AwsResource(tag_list_to_dict(tags['TagSet']),
                                  'object', obj.key)

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.id}")
        if resource.type == 'instance':
            self._deleteInstance(resource.id, immediate=True)
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
            yield AwsInstance(self.provider, instance, quota)

    def getQuotaLimits(self):
        # Get the instance types that this provider handles
        instance_types = set()
        for pool in self.provider.pools.values():
            for label in pool.labels.values():
                instance_types.add(label.instance_type)
        args = dict(default=math.inf)
        for instance_type in instance_types:
            code = self._getQuotaCodeForInstanceType(instance_type)
            if code in args:
                continue
            if not code:
                self.log.warning("Unknown quota code for instance type: %s",
                                 instance_type)
                continue
            with self.non_mutating_rate_limiter:
                self.log.debug("Getting quota limits for %s", code)
                response = self.aws_quotas.get_service_quota(
                    ServiceCode='ec2',
                    QuotaCode=code,
                )
                args[code] = response['Quota']['Value']
        return QuotaInformation(**args)

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

        # Import snapshot
        self.log.debug(f"Importing {image_name}")
        with self.rate_limiter:
            import_snapshot_task = self.ec2_client.import_snapshot(
                DiskContainer={
                    'Format': image_format,
                    'UserBucket': {
                        'S3Bucket': bucket_name,
                        'S3Key': object_filename,
                    },
                },
                TagSpecifications=[
                    {
                        'ResourceType': 'import-snapshot-task',
                        'Tags': tag_dict_to_list(metadata),
                    },
                ]
            )
        task_id = import_snapshot_task['ImportTaskId']

        paginator = self.ec2_client.get_paginator(
            'describe_import_snapshot_tasks')
        done = False
        while not done:
            time.sleep(self.IMAGE_UPLOAD_SLEEP)
            with self.non_mutating_rate_limiter:
                for page in paginator.paginate(ImportTaskIds=[task_id]):
                    for task in page['ImportSnapshotTasks']:
                        if task['SnapshotTaskDetail']['Status'].lower() in (
                                'completed', 'deleted'):
                            done = True
                            break

        self.log.debug(f"Deleting {image_name} from S3")
        with self.rate_limiter:
            self.s3.Object(bucket_name, object_filename).delete()

        if task['SnapshotTaskDetail']['Status'].lower() != 'completed':
            raise Exception(f"Error uploading image: {task}")

        # Tag the snapshot
        try:
            with self.non_mutating_rate_limiter:
                snap = self.ec2.Snapshot(
                    task['SnapshotTaskDetail']['SnapshotId'])
            with self.rate_limiter:
                snap.create_tags(Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging snapshot:")

        volume_size = provider_image.volume_size or snap.volume_size
        # Register the snapshot as an AMI
        with self.rate_limiter:
            bdm = {
                'DeviceName': '/dev/sda1',
                'Ebs': {
                    'DeleteOnTermination': True,
                    'SnapshotId': task[
                        'SnapshotTaskDetail']['SnapshotId'],
                    'VolumeSize': volume_size,
                    'VolumeType': provider_image.volume_type,
                },
            }
            if provider_image.iops:
                bdm['Ebs']['Iops'] = provider_image.iops
            if provider_image.throughput:
                bdm['Ebs']['Throughput'] = provider_image.throughput

            register_response = self.ec2_client.register_image(
                Architecture=provider_image.architecture,
                BlockDeviceMappings=[bdm],
                RootDeviceName='/dev/sda1',
                VirtualizationType='hvm',
                EnaSupport=provider_image.ena_support,
                Name=image_name,
            )

        # Tag the AMI
        try:
            with self.non_mutating_rate_limiter:
                ami = self.ec2.Image(register_response['ImageId'])
            with self.rate_limiter:
                ami.create_tags(Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging AMI:")

        self.log.debug(f"Upload of {image_name} complete as "
                       f"{register_response['ImageId']}")
        # Last task returned from paginator above
        return register_response['ImageId']

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
        # and we copy the tags from the associated snapshot import
        # task.
        to_examine = []
        for ami in self._listAmis():
            if ami.id in self.not_our_images:
                continue
            try:
                if ami.tags:
                    continue
            except (botocore.exceptions.ClientError, AttributeError):
                continue
            # This has no tags, which means it's either not a nodepool
            # image, or it's a new one which doesn't have tags yet.
            # Copy over any tags from the snapshot import task,
            # otherwise, mark it as an image we can ignore in future
            # runs.
            if len(ami.block_device_mappings) < 1:
                self.not_our_images.add(ami.id)
                continue
            bdm = ami.block_device_mappings[0]
            ebs = bdm.get('Ebs')
            if not ebs:
                self.not_our_images.add(ami.id)
                continue
            snapshot_id = ebs.get('SnapshotId')
            if not snapshot_id:
                self.not_our_images.add(ami.id)
                continue
            to_examine.append((ami, snapshot_id))
        if not to_examine:
            return

        # We have images to examine; get a list of import tasks so
        # we can copy the tags from the import task that resulted in
        # this image.
        task_map = {}
        for task in self._listImportSnapshotTasks():
            detail = task['SnapshotTaskDetail']
            task_snapshot_id = detail.get('SnapshotId')
            if not task_snapshot_id:
                continue
            task_map[task_snapshot_id] = task['Tags']

        for ami, snapshot_id in to_examine:
            tags = task_map.get(snapshot_id)
            if not tags:
                self.not_our_images.add(ami.id)
                continue
            metadata = tag_list_to_dict(tags)
            if (metadata.get('nodepool_provider_name') == self.provider.name):
                # Copy over tags
                self.log.debug(
                    f"Copying tags from import task to image {ami.id}")
                with self.rate_limiter:
                    ami.create_tags(Tags=tags)
            else:
                self.not_our_images.add(ami.id)

    def _tagSnapshots(self):
        # See comments for _tagAmis
        to_examine = []
        for snap in self._listSnapshots():
            try:
                if (snap.id not in self.not_our_snapshots and
                    not snap.tags):
                    to_examine.append(snap)
            except botocore.exceptions.ClientError:
                # We may have cached a snapshot that doesn't exist
                continue
        if not to_examine:
            return

        # We have snapshots to examine; get a list of import tasks so
        # we can copy the tags from the import task that resulted in
        # this snapshot.
        task_map = {}
        for task in self._listImportSnapshotTasks():
            detail = task['SnapshotTaskDetail']
            task_snapshot_id = detail.get('SnapshotId')
            if not task_snapshot_id:
                continue
            task_map[task_snapshot_id] = task['Tags']

        for snap in to_examine:
            tags = task_map.get(snap.id)
            if not tags:
                self.not_our_snapshots.add(snap.id)
                continue
            metadata = tag_list_to_dict(tags)
            if (metadata.get('nodepool_provider_name') == self.provider.name):
                # Copy over tags
                self.log.debug(
                    f"Copying tags from import task to snapshot {snap.id}")
                with self.rate_limiter:
                    snap.create_tags(Tags=tags)
            else:
                self.not_our_snapshots.add(snap.id)

    def _listImportSnapshotTasks(self):
        paginator = self.ec2_client.get_paginator(
            'describe_import_snapshot_tasks')
        with self.non_mutating_rate_limiter:
            for page in paginator.paginate():
                for task in page['ImportSnapshotTasks']:
                    yield task

    instance_key_re = re.compile(r'([a-z\-]+)\d.*')

    def _getQuotaCodeForInstanceType(self, instance_type):
        m = self.instance_key_re.match(instance_type)
        if m:
            key = m.group(1)
            return QUOTA_CODES.get(key)

    def _getQuotaForInstanceType(self, instance_type):
        itype = self._getInstanceType(instance_type)
        cores = itype['InstanceTypes'][0]['VCpuInfo']['DefaultCores']
        ram = itype['InstanceTypes'][0]['MemoryInfo']['SizeInMiB']
        code = self._getQuotaCodeForInstanceType(instance_type)
        # We include cores twice: one to match the overall cores quota
        # (which may be set as a tenant resource limit), and a second
        # time as the specific AWS quota code which in for a specific
        # instance type.
        args = dict(cores=cores, ram=ram, instances=1)
        if code:
            args[code] = cores
        return QuotaInformation(**args)

    # This method is wrapped with an LRU cache in the constructor.
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
        return obj

    def _refreshDelete(self, obj):
        if obj is None:
            return obj

        for instance in self._listInstances():
            if instance.id == obj.id:
                if instance.state["Name"].lower() == "terminated":
                    return None
                return instance
        return None

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listInstances(self):
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed instances"):
            return list(self.ec2.instances.all())

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listVolumes(self):
        with self.non_mutating_rate_limiter:
            return list(self.ec2.volumes.all())

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listAmis(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter:
            return list(self.ec2.images.filter(Owners=['self']))

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listSnapshots(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter:
            return list(self.ec2.snapshots.filter(OwnerIds=['self']))

    @cachetools.func.ttl_cache(maxsize=1, ttl=CACHE_TTL)
    def _listObjects(self):
        bucket_name = self.provider.object_storage.get('bucket-name')
        if not bucket_name:
            return []

        bucket = self.s3.Bucket(bucket_name)
        with self.non_mutating_rate_limiter:
            return list(bucket.objects.all())

    def _getLatestImageIdByFilters(self, image_filters):
        # Normally we would decorate this method, but our cache key is
        # complex, so we serialize it to JSON and manage the cache
        # ourselves.
        cache_key = json.dumps(image_filters)
        val = self.image_id_by_filter_cache.get(cache_key)
        if val:
            return val

        with self.non_mutating_rate_limiter:
            res = list(self.ec2_client.describe_images(
                Filters=image_filters
            ).get("Images"))

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

    # This method is wrapped with an LRU cache in the constructor.
    def _getImage(self, image_id):
        with self.non_mutating_rate_limiter:
            return self.ec2.Image(image_id)

    def _submitCreateInstance(self, label, image_external_id,
                              tags, hostname, log):
        return self.create_executor.submit(
            self._createInstance,
            label, image_external_id,
            tags, hostname, log)

    def _completeCreateInstance(self, future):
        if not future.done():
            return None
        return future.result()

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
                if label.iops:
                    mapping['Ebs']['Iops'] = label.iops
                if label.throughput:
                    mapping['Ebs']['Throughput'] = label.throughput
                # If the AMI is a snapshot, we cannot supply an "encrypted"
                # parameter
                if 'Encrypted' in mapping['Ebs']:
                    del mapping['Ebs']['Encrypted']
                args['BlockDeviceMappings'] = [mapping]

        with self.rate_limiter(log.debug, "Created instance"):
            log.debug(f"Creating VM {hostname}")
            instances = self.ec2.create_instances(**args)
            log.debug(f"Created VM {hostname} as instance {instances[0].id}")
            return instances[0]

    def _deleteThread(self):
        while self._running:
            try:
                self._deleteThreadInner()
            except Exception:
                self.log.exception("Error in delete thread:")
                time.sleep(5)

    def _deleteThreadInner(self):
        records = []
        try:
            records.append(self.delete_queue.get(block=True, timeout=10))
        except queue.Empty:
            return
        while True:
            try:
                records.append(self.delete_queue.get(block=False))
            except queue.Empty:
                break
            # The terminate call has a limit of 1k, but AWS recommends
            # smaller batches.  We limit to 50 here.
            if len(records) >= 50:
                break
        ids = []
        for (del_id, log) in records:
            ids.append(del_id)
            log.debug(f"Deleting instance {del_id}")
        count = len(ids)
        with self.rate_limiter(log.debug, f"Deleted {count} instances"):
            self.ec2_client.terminate_instances(InstanceIds=ids)

    def _deleteInstance(self, external_id, log=None, immediate=False):
        if log is None:
            log = self.log
        for instance in self._listInstances():
            if instance.id == external_id:
                break
        else:
            log.warning(f"Instance not found when deleting {external_id}")
            return None
        if immediate:
            with self.rate_limiter(log.debug, "Deleted instance"):
                log.debug(f"Deleting instance {external_id}")
                instance.terminate()
        else:
            self.delete_queue.put((external_id, log))
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
