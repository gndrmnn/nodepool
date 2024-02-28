# Copyright 2018 Red Hat
# Copyright 2022-2023 Acme Gating, LLC
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
import copy
import functools
import json
import logging
import math
import re
import threading
import queue
import time
import urllib.parse

from nodepool.driver.utils import (
    QuotaInformation,
    LazyExecutorTTLCache,
    RateLimiter,
)
from nodepool.driver import statemachine
from nodepool import exceptions

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
    # INSTANCE FAMILY: [ON-DEMAND, SPOT]
    'a': ['L-1216C47A', 'L-34B43A08'],
    'c': ['L-1216C47A', 'L-34B43A08'],
    'd': ['L-1216C47A', 'L-34B43A08'],
    'h': ['L-1216C47A', 'L-34B43A08'],
    'i': ['L-1216C47A', 'L-34B43A08'],
    'm': ['L-1216C47A', 'L-34B43A08'],
    'r': ['L-1216C47A', 'L-34B43A08'],
    't': ['L-1216C47A', 'L-34B43A08'],
    'z': ['L-1216C47A', 'L-34B43A08'],
    'dl': ['L-6E869C2A', 'L-85EED4F7'],
    'f': ['L-74FC7D96', 'L-88CF9481'],
    'g': ['L-DB2E81BA', 'L-3819A6DF'],
    'vt': ['L-DB2E81BA', 'L-3819A6DF'],
    'u-': ['L-43DA4232', ''],          # 'high memory'
    'inf': ['L-1945791B', 'L-B5D1601B'],
    'p': ['L-417A185B', 'L-7212CCBC'],
    'x': ['L-7295265B', 'L-E3A00192'],
    'trn': ['L-2C3B7624', 'L-6B0D517C'],
    'hpc': ['L-F7808C92', '']
}

VOLUME_QUOTA_CODES = {
    'io1': dict(iops='L-B3A130E6', storage='L-FD252861'),
    'io2': dict(iops='L-8D977E7E', storage='L-09BD8365'),
    'sc1': dict(storage='L-17AF77E8'),
    'gp2': dict(storage='L-D18FCD1D'),
    'gp3': dict(storage='L-7A658B76'),
    'standard': dict(storage='L-9CF3C2EB'),
    'st1': dict(storage='L-82ACEF56'),
}

CACHE_TTL = 10
SERVICE_QUOTA_CACHE_TTL = 300
ON_DEMAND = 0
SPOT = 1


class AwsInstance(statemachine.Instance):
    def __init__(self, provider, instance, quota):
        super().__init__()
        self.external_id = instance['InstanceId']
        self.metadata = tag_list_to_dict(instance.get('Tags'))
        self.private_ipv4 = instance.get('PrivateIpAddress')
        self.private_ipv6 = None
        self.public_ipv4 = instance.get('PublicIpAddress')
        self.public_ipv6 = None
        self.cloud = 'AWS'
        self.region = provider.region_name
        self.az = None
        self.quota = quota

        self.az = instance.get('Placement', {}).get('AvailabilityZone')

        for iface in instance.get('NetworkInterfaces', [])[:1]:
            if iface.get('Ipv6Addresses'):
                v6addr = iface['Ipv6Addresses'][0]
                self.public_ipv6 = v6addr.get('Ipv6Address')
        self.interface_ip = (self.public_ipv4 or self.public_ipv6 or
                             self.private_ipv4 or self.private_ipv6)

    def getQuotaInformation(self):
        return self.quota


class AwsResource(statemachine.Resource):
    TYPE_INSTANCE = 'instance'
    TYPE_AMI = 'ami'
    TYPE_SNAPSHOT = 'snapshot'
    TYPE_VOLUME = 'volume'
    TYPE_OBJECT = 'object'

    def __init__(self, metadata, type, id):
        super().__init__(metadata, type)
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
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, request, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
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
            self.create_future = self.adapter._submitCreateInstance(
                self.label, self.image_external_id,
                self.tags, self.hostname, self.log)
            self.state = self.INSTANCE_CREATING_SUBMIT

        if self.state == self.INSTANCE_CREATING_SUBMIT:
            instance = self.adapter._completeCreateInstance(self.create_future)
            if instance is None:
                return
            self.instance = instance
            self.external_id = instance['InstanceId']
            self.quota = self.adapter.getQuotaForLabel(self.label)
            self.state = self.INSTANCE_CREATING

        if self.state == self.INSTANCE_CREATING:
            self.instance = self.adapter._refresh(self.instance)

            if self.instance['State']['Name'].lower() == "running":
                self.state = self.COMPLETE
            elif self.instance['State']['Name'].lower() == "terminated":
                raise exceptions.LaunchStatusException(
                    "Instance in terminated state")
            else:
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
        # Experimentally, this rate limit refreshes tokens at
        # something like 0.16/second, so if we operated at the rate
        # limit, it would take us almost a minute to determine the
        # quota.  Instead, we're going to just use the normal provider
        # rate and rely on caching to avoid going over the limit.  At
        # the of writing, we'll issue bursts of 5 requests every 5
        # minutes.
        self.quota_service_rate_limiter = RateLimiter(self.provider.name,
                                                      self.provider.rate)
        self.image_id_by_filter_cache = cachetools.TTLCache(
            maxsize=8192, ttl=(5 * 60))
        self.aws = boto3.Session(
            region_name=self.provider.region_name,
            profile_name=self.provider.profile_name)
        self.ec2_client = self.aws.client("ec2")
        self.s3 = self.aws.resource('s3')
        self.s3_client = self.aws.client('s3')
        self.aws_quotas = self.aws.client("service-quotas")

        workers = 10
        self.log.info("Create executor with max workers=%s", workers)
        self.api_executor = ThreadPoolExecutor(
            thread_name_prefix=f'aws-api-{provider_config.name}',
            max_workers=workers)

        # Use a lazy TTL cache for these.  This uses the TPE to
        # asynchronously update the cached values, meanwhile returning
        # the previous cached data if available.  This means every
        # call after the first one is instantaneous.
        self._listInstances = LazyExecutorTTLCache(
            CACHE_TTL, self.api_executor)(
                self._listInstances)
        self._listVolumes = LazyExecutorTTLCache(
            CACHE_TTL, self.api_executor)(
                self._listVolumes)
        self._listAmis = LazyExecutorTTLCache(
            CACHE_TTL, self.api_executor)(
                self._listAmis)
        self._listSnapshots = LazyExecutorTTLCache(
            CACHE_TTL, self.api_executor)(
                self._listSnapshots)
        self._listObjects = LazyExecutorTTLCache(
            CACHE_TTL, self.api_executor)(
                self._listObjects)
        self._listEC2Quotas = LazyExecutorTTLCache(
            SERVICE_QUOTA_CACHE_TTL, self.api_executor)(
                self._listEC2Quotas)
        self._listEBSQuotas = LazyExecutorTTLCache(
            SERVICE_QUOTA_CACHE_TTL, self.api_executor)(
                self._listEBSQuotas)

        # In listResources, we reconcile AMIs which appear to be
        # imports but have no nodepool tags, however it's possible
        # that these aren't nodepool images.  If we determine that's
        # the case, we'll add their ids here so we don't waste our
        # time on that again.
        self.not_our_images = set()
        self.not_our_snapshots = set()

    def stop(self):
        self.create_executor.shutdown()
        self.api_executor.shutdown()
        self._running = False

    def getCreateStateMachine(self, hostname, label, image_external_id,
                              metadata, request, az, log):
        return AwsCreateStateMachine(self, hostname, label, image_external_id,
                                     metadata, request, log)

    def getDeleteStateMachine(self, external_id, log):
        return AwsDeleteStateMachine(self, external_id, log)

    def listResources(self):
        self._tagSnapshots()
        self._tagAmis()
        for instance in self._listInstances():
            try:
                if instance['State']['Name'].lower() == "terminated":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(instance.get('Tags')),
                              AwsResource.TYPE_INSTANCE,
                              instance['InstanceId'])
        for volume in self._listVolumes():
            try:
                if volume['State'].lower() == "deleted":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(volume.get('Tags')),
                              AwsResource.TYPE_VOLUME, volume['VolumeId'])
        for ami in self._listAmis():
            try:
                if ami['State'].lower() == "deleted":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(ami.get('Tags')),
                              AwsResource.TYPE_AMI, ami['ImageId'])
        for snap in self._listSnapshots():
            try:
                if snap['State'].lower() == "deleted":
                    continue
            except botocore.exceptions.ClientError:
                continue
            yield AwsResource(tag_list_to_dict(snap.get('Tags')),
                              AwsResource.TYPE_SNAPSHOT, snap['SnapshotId'])
        if self.provider.object_storage:
            for obj in self._listObjects():
                with self.non_mutating_rate_limiter:
                    try:
                        tags = self.s3_client.get_object_tagging(
                            Bucket=obj.bucket_name, Key=obj.key)
                    except botocore.exceptions.ClientError:
                        continue
                yield AwsResource(tag_list_to_dict(tags['TagSet']),
                                  AwsResource.TYPE_OBJECT, obj.key)

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.id}")
        if resource.type == AwsResource.TYPE_INSTANCE:
            self._deleteInstance(resource.id, immediate=True)
        if resource.type == AwsResource.TYPE_VOLUME:
            self._deleteVolume(resource.id)
        if resource.type == AwsResource.TYPE_AMI:
            self._deleteAmi(resource.id)
        if resource.type == AwsResource.TYPE_SNAPSHOT:
            self._deleteSnapshot(resource.id)
        if resource.type == AwsResource.TYPE_OBJECT:
            self._deleteObject(resource.id)

    def listInstances(self):
        volumes = {}
        for volume in self._listVolumes():
            volumes[volume['VolumeId']] = volume
        for instance in self._listInstances():
            if instance['State']["Name"].lower() == "terminated":
                continue
            quota = self._getQuotaForInstanceType(
                instance['InstanceType'],
                SPOT if instance.get('InstanceLifecycle') == 'spot'
                else ON_DEMAND)
            for attachment in instance['BlockDeviceMappings']:
                volume_id = attachment['Ebs']['VolumeId']
                volume = volumes.get(volume_id)
                if volume is None:
                    self.log.warning(
                        "Volume %s of instance %s could not be found",
                        volume_id, instance['InstanceId'])
                    continue
                quota.add(self._getQuotaForVolume(volume))

            yield AwsInstance(self.provider, instance, quota)

    def getQuotaLimits(self):
        # Get the instance and volume types that this provider handles
        instance_types = {}
        volume_types = set()
        ec2_quotas = self._listEC2Quotas()
        ebs_quotas = self._listEBSQuotas()
        for pool in self.provider.pools.values():
            for label in pool.labels.values():
                if label.instance_type not in instance_types:
                    instance_types[label.instance_type] = set()
                instance_types[label.instance_type].add(
                    SPOT if label.use_spot else ON_DEMAND)
                if label.volume_type:
                    volume_types.add(label.volume_type)
        args = dict(default=math.inf)
        for instance_type in instance_types:
            for market_type_option in instance_types[instance_type]:
                code = self._getQuotaCodeForInstanceType(instance_type,
                                                         market_type_option)
                if code in args:
                    continue
                if not code:
                    self.log.warning(
                        "Unknown quota code for instance type: %s",
                        instance_type)
                    continue
                if code not in ec2_quotas:
                    self.log.warning(
                        "AWS quota code %s for instance type: %s not known",
                        code, instance_type)
                    continue
                args[code] = ec2_quotas[code]
        for volume_type in volume_types:
            vquota_codes = VOLUME_QUOTA_CODES.get(volume_type)
            if not vquota_codes:
                self.log.warning(
                    "Unknown quota code for volume type: %s",
                    volume_type)
                continue
            for resource, code in vquota_codes.items():
                if code in args:
                    continue
                if code not in ebs_quotas:
                    self.log.warning(
                        "AWS quota code %s for volume type: %s not known",
                        code, volume_type)
                    continue
                value = ebs_quotas[code]
                # Unit mismatch: storage limit is in TB, but usage
                # is in GB.  Translate the limit to GB.
                if resource == 'storage':
                    value *= 1000
                args[code] = value
        return QuotaInformation(**args)

    def getQuotaForLabel(self, label):
        quota = self._getQuotaForInstanceType(
            label.instance_type,
            SPOT if label.use_spot else ON_DEMAND)
        if label.volume_type:
            quota.add(self._getQuotaForVolumeType(
                label.volume_type,
                storage=label.volume_size,
                iops=label.iops))
        return quota

    def uploadImage(self, provider_image, image_name, filename,
                    image_format, metadata, md5, sha256):
        self.log.debug(f"Uploading image {image_name}")

        # Upload image to S3
        bucket_name = self.provider.object_storage['bucket-name']
        bucket = self.s3.Bucket(bucket_name)
        object_filename = f'{image_name}.{image_format}'
        extra_args = {'Tagging': urllib.parse.urlencode(metadata)}

        # There is no IMDS support option for the import_image call
        if (provider_image.import_method == 'image' and
            provider_image.imds_support == 'v2.0'):
            raise Exception("IMDSv2 requires 'snapshot' import method")

        with open(filename, "rb") as fobj:
            with self.rate_limiter:
                bucket.upload_fileobj(fobj, object_filename,
                                      ExtraArgs=extra_args)

        if provider_image.import_method == 'image':
            image_id = self._uploadImageImage(
                provider_image, image_name, filename,
                image_format, metadata, md5, sha256,
                bucket_name, object_filename)
        else:
            image_id = self._uploadImageSnapshot(
                provider_image, image_name, filename,
                image_format, metadata, md5, sha256,
                bucket_name, object_filename)
        return image_id

    def _uploadImageSnapshot(self, provider_image, image_name, filename,
                             image_format, metadata, md5, sha256,
                             bucket_name, object_filename):
        # Import snapshot
        self.log.debug(f"Importing {image_name} as snapshot")
        timeout = time.time()
        if self.provider.image_import_timeout:
            timeout += self.provider.image_import_timeout
        while True:
            try:
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
                    break
            except botocore.exceptions.ClientError as error:
                if (error.response['Error']['Code'] ==
                    'ResourceCountLimitExceeded'):
                    if time.time() < timeout:
                        self.log.warning("AWS error: '%s' will retry",
                                         str(error))
                        time.sleep(self.IMAGE_UPLOAD_SLEEP)
                        continue
                raise
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
                resp = self.ec2_client.describe_snapshots(
                    SnapshotIds=[task['SnapshotTaskDetail']['SnapshotId']])
                snap = resp['Snapshots'][0]
            with self.rate_limiter:
                self.ec2_client.create_tags(
                    Resources=[task['SnapshotTaskDetail']['SnapshotId']],
                    Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging snapshot:")

        volume_size = provider_image.volume_size or snap['VolumeSize']
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

            args = dict(
                Architecture=provider_image.architecture,
                BlockDeviceMappings=[bdm],
                RootDeviceName='/dev/sda1',
                VirtualizationType='hvm',
                EnaSupport=provider_image.ena_support,
                Name=image_name,
            )
            if provider_image.imds_support == 'v2.0':
                args['ImdsSupport'] = 'v2.0'
            register_response = self.ec2_client.register_image(**args)

        # Tag the AMI
        try:
            with self.rate_limiter:
                self.ec2_client.create_tags(
                    Resources=[register_response['ImageId']],
                    Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging AMI:")

        self.log.debug(f"Upload of {image_name} complete as "
                       f"{register_response['ImageId']}")
        return register_response['ImageId']

    def _uploadImageImage(self, provider_image, image_name, filename,
                          image_format, metadata, md5, sha256,
                          bucket_name, object_filename):
        # Import image as AMI
        self.log.debug(f"Importing {image_name} as AMI")
        timeout = time.time()
        if self.provider.image_import_timeout:
            timeout += self.provider.image_import_timeout
        while True:
            try:
                with self.rate_limiter:
                    import_image_task = self.ec2_client.import_image(
                        Architecture=provider_image.architecture,
                        DiskContainers=[{
                            'Format': image_format,
                            'UserBucket': {
                                'S3Bucket': bucket_name,
                                'S3Key': object_filename,
                            },
                        }],
                        TagSpecifications=[
                            {
                                'ResourceType': 'import-image-task',
                                'Tags': tag_dict_to_list(metadata),
                            },
                        ]
                    )
                    break
            except botocore.exceptions.ClientError as error:
                if (error.response['Error']['Code'] ==
                    'ResourceCountLimitExceeded'):
                    if time.time() < timeout:
                        self.log.warning("AWS error: '%s' will retry",
                                         str(error))
                        time.sleep(self.IMAGE_UPLOAD_SLEEP)
                        continue
                raise
        task_id = import_image_task['ImportTaskId']

        paginator = self.ec2_client.get_paginator(
            'describe_import_image_tasks')
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
            with self.rate_limiter:
                self.ec2_client.create_tags(
                    Resources=[task['ImageId']],
                    Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging AMI:")

        # Tag the snapshot
        try:
            with self.rate_limiter:
                self.ec2_client.create_tags(
                    Resources=[task['SnapshotDetails'][0]['SnapshotId']],
                    Tags=task['Tags'])
        except Exception:
            self.log.exception("Error tagging snapshot:")

        self.log.debug(f"Upload of {image_name} complete as {task['ImageId']}")
        # Last task returned from paginator above
        return task['ImageId']

    def deleteImage(self, external_id):
        snaps = set()
        self.log.debug(f"Deleting image {external_id}")
        for ami in self._listAmis():
            if ami['ImageId'] == external_id:
                for bdm in ami.get('BlockDeviceMappings', []):
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
        # and we copy the tags from the associated snapshot or image
        # import task.
        to_examine = []
        for ami in self._listAmis():
            if ami['ImageId'] in self.not_our_images:
                continue
            if ami.get('Tags'):
                continue

            # This has no tags, which means it's either not a nodepool
            # image, or it's a new one which doesn't have tags yet.
            if ami['Name'].startswith('import-ami-'):
                task = self._getImportImageTask(ami['Name'])
                if task:
                    # This was an import image (not snapshot) so let's
                    # try to find tags from the import task.
                    tags = tag_list_to_dict(task.get('Tags'))
                    if (tags.get('nodepool_provider_name') ==
                        self.provider.name):
                        # Copy over tags
                        self.log.debug(
                            "Copying tags from import task %s to AMI",
                            ami['Name'])
                        with self.rate_limiter:
                            self.ec2_client.create_tags(
                                Resources=[ami['ImageId']],
                                Tags=task['Tags'])
                        continue

            # This may have been a snapshot import; try to copy over
            # any tags from the snapshot import task, otherwise, mark
            # it as an image we can ignore in future runs.
            if len(ami.get('BlockDeviceMappings', [])) < 1:
                self.not_our_images.add(ami['ImageId'])
                continue
            bdm = ami['BlockDeviceMappings'][0]
            ebs = bdm.get('Ebs')
            if not ebs:
                self.not_our_images.add(ami['ImageId'])
                continue
            snapshot_id = ebs.get('SnapshotId')
            if not snapshot_id:
                self.not_our_images.add(ami['ImageId'])
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
                self.not_our_images.add(ami['ImageId'])
                continue
            metadata = tag_list_to_dict(tags)
            if (metadata.get('nodepool_provider_name') == self.provider.name):
                # Copy over tags
                self.log.debug(
                    "Copying tags from import task to image %s",
                    ami['ImageId'])
                with self.rate_limiter:
                    self.ec2_client.create_tags(
                        Resources=[ami['ImageId']],
                        Tags=task['Tags'])
            else:
                self.not_our_images.add(ami['ImageId'])

    def _tagSnapshots(self):
        # See comments for _tagAmis
        to_examine = []
        for snap in self._listSnapshots():
            if snap['SnapshotId'] in self.not_our_snapshots:
                continue
            try:
                if snap.get('Tags'):
                    continue
            except botocore.exceptions.ClientError:
                # We may have cached a snapshot that doesn't exist
                continue

            if 'import-ami' in snap.get('Description', ''):
                match = re.match(r'.*?(import-ami-\w*)',
                                 snap.get('Description', ''))
                task = None
                if match:
                    task_id = match.group(1)
                    task = self._getImportImageTask(task_id)
                if task:
                    # This was an import image (not snapshot) so let's
                    # try to find tags from the import task.
                    tags = tag_list_to_dict(task.get('Tags'))
                    if (tags.get('nodepool_provider_name') ==
                        self.provider.name):
                        # Copy over tags
                        self.log.debug(
                            f"Copying tags from import task {task_id}"
                            " to snapshot")
                        with self.rate_limiter:
                            self.ec2_client.create_tags(
                                Resources=[snap['SnapshotId']],
                                Tags=task['Tags'])
                        continue

            # This may have been a snapshot import; try to copy over
            # any tags from the snapshot import task.
            to_examine.append(snap)

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
            tags = task_map.get(snap['SnapshotId'])
            if not tags:
                self.not_our_snapshots.add(snap['SnapshotId'])
                continue
            metadata = tag_list_to_dict(tags)
            if (metadata.get('nodepool_provider_name') == self.provider.name):
                # Copy over tags
                self.log.debug(
                    "Copying tags from import task to snapshot %s",
                    snap['SnapshotId'])
                with self.rate_limiter:
                    self.ec2_client.create_tags(
                        Resources=[snap['SnapshotId']],
                        Tags=tags)
            else:
                self.not_our_snapshots.add(snap['SnapshotId'])

    def _getImportImageTask(self, task_id):
        paginator = self.ec2_client.get_paginator(
            'describe_import_image_tasks')
        with self.non_mutating_rate_limiter:
            try:
                for page in paginator.paginate(ImportTaskIds=[task_id]):
                    for task in page['ImportImageTasks']:
                        # Return the first and only task
                        return task
            except botocore.exceptions.ClientError as error:
                if (error.response['Error']['Code'] ==
                    'InvalidConversionTaskId.Malformed'):
                    # In practice, this can mean that the task no
                    # longer exists
                    pass
                else:
                    raise
        return None

    def _listImportSnapshotTasks(self):
        paginator = self.ec2_client.get_paginator(
            'describe_import_snapshot_tasks')
        with self.non_mutating_rate_limiter:
            for page in paginator.paginate():
                for task in page['ImportSnapshotTasks']:
                    yield task

    instance_key_re = re.compile(r'([a-z\-]+)\d.*')

    def _getQuotaCodeForInstanceType(self, instance_type, market_type_option):
        m = self.instance_key_re.match(instance_type)
        if m:
            key = m.group(1)
            return QUOTA_CODES.get(key)[market_type_option]

    def _getQuotaForInstanceType(self, instance_type, market_type_option):
        try:
            itype = self._getInstanceType(instance_type)
            cores = itype['InstanceTypes'][0]['VCpuInfo']['DefaultCores']
            vcpus = itype['InstanceTypes'][0]['VCpuInfo']['DefaultVCpus']
            ram = itype['InstanceTypes'][0]['MemoryInfo']['SizeInMiB']
            code = self._getQuotaCodeForInstanceType(instance_type,
                                                     market_type_option)
        except botocore.exceptions.ClientError as error:
            if error.response['Error']['Code'] == 'InvalidInstanceType':
                self.log.exception("Error querying instance type: %s",
                                   instance_type)
                # Re-raise as a configuration exception so that the
                # statemachine driver resets quota.
                raise exceptions.RuntimeConfigurationException(str(error))
            raise
        # We include cores to match the overall cores quota (which may
        # be set as a tenant resource limit), and include vCPUs for the
        # specific AWS quota code which in for a specific instance
        # type. With two threads per core, the vCPU number is
        # typically twice the number of cores. AWS service quotas are
        # implemented in terms of vCPUs.
        args = dict(cores=cores, ram=ram, instances=1)
        if code:
            args[code] = vcpus

        return QuotaInformation(**args)

    def _getQuotaForVolume(self, volume):
        volume_type = volume['VolumeType']
        vquota_codes = VOLUME_QUOTA_CODES.get(volume_type, {})
        args = {}
        if 'iops' in vquota_codes and volume.get('Iops'):
            args[vquota_codes['iops']] = volume['Iops']
        if 'storage' in vquota_codes and volume.get('Size'):
            args[vquota_codes['storage']] = volume['Size']
        return QuotaInformation(**args)

    def _getQuotaForVolumeType(self, volume_type, storage=None, iops=None):
        vquota_codes = VOLUME_QUOTA_CODES.get(volume_type, {})
        args = {}
        if 'iops' in vquota_codes and iops is not None:
            args[vquota_codes['iops']] = iops
        if 'storage' in vquota_codes and storage is not None:
            args[vquota_codes['storage']] = storage
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
            if instance['InstanceId'] == obj['InstanceId']:
                return instance
        return obj

    def _refreshDelete(self, obj):
        if obj is None:
            return obj

        for instance in self._listInstances():
            if instance['InstanceId'] == obj['InstanceId']:
                if instance['State']['Name'].lower() == "terminated":
                    return None
                return instance
        return None

    def _listServiceQuotas(self, service_code):
        with self.quota_service_rate_limiter(
                self.log.debug, f"Listed {service_code} quotas"):
            paginator = self.aws_quotas.get_paginator(
                'list_service_quotas')
            quotas = {}
            for page in paginator.paginate(ServiceCode=service_code):
                for quota in page['Quotas']:
                    quotas[quota['QuotaCode']] = quota['Value']
            return quotas

    def _listEC2Quotas(self):
        return self._listServiceQuotas('ec2')

    def _listEBSQuotas(self):
        return self._listServiceQuotas('ebs')

    def _listInstances(self):
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed instances"):
            paginator = self.ec2_client.get_paginator('describe_instances')
            instances = []
            for page in paginator.paginate():
                for res in page['Reservations']:
                    instances.extend(res['Instances'])
            return instances

    def _listVolumes(self):
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed volumes"):
            paginator = self.ec2_client.get_paginator('describe_volumes')
            volumes = []
            for page in paginator.paginate():
                volumes.extend(page['Volumes'])
            return volumes

    def _listAmis(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed images"):
            paginator = self.ec2_client.get_paginator('describe_images')
            images = []
            for page in paginator.paginate(Owners=['self']):
                images.extend(page['Images'])
            return images

    def _listSnapshots(self):
        # Note: this is overridden in tests due to the filter
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed snapshots"):
            paginator = self.ec2_client.get_paginator('describe_snapshots')
            snapshots = []
            for page in paginator.paginate(OwnerIds=['self']):
                snapshots.extend(page['Snapshots'])
            return snapshots

    def _listObjects(self):
        bucket_name = self.provider.object_storage.get('bucket-name')
        if not bucket_name:
            return []

        bucket = self.s3.Bucket(bucket_name)
        with self.non_mutating_rate_limiter(
                self.log.debug, "Listed S3 objects"):
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
            resp = self.ec2_client.describe_images(ImageIds=[image_id])
            return resp['Images'][0]

    def _submitCreateInstance(self, label, image_external_id,
                              tags, hostname, log):
        return self.create_executor.submit(
            self._createInstance,
            label, image_external_id,
            tags, hostname, log)

    def _completeCreateInstance(self, future):
        if not future.done():
            return None
        try:
            return future.result()
        except botocore.exceptions.ClientError as error:
            if error.response['Error']['Code'] == 'VolumeLimitExceeded':
                # Re-raise as a quota exception so that the
                # statemachine driver resets quota.
                raise exceptions.QuotaException(str(error))
            if (error.response['Error']['Code'] ==
                'InsufficientInstanceCapacity'):
                # Re-raise as CapacityException so it would have
                # "error.capacity" statsd_key, which can be handled
                # differently than "error.unknown"
                raise exceptions.CapacityException(str(error))
            raise

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
        if image.get('BlockDeviceMappings'):
            bdm = image['BlockDeviceMappings']
            mapping = copy.deepcopy(bdm[0])
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

        # enable EC2 Spot
        if label.use_spot:
            args['InstanceMarketOptions'] = {
                'MarketType': 'spot',
                'SpotOptions': {
                    'SpotInstanceType': 'one-time',
                    'InstanceInterruptionBehavior': 'terminate'
                }
            }

        if label.imdsv2 == 'required':
            args['MetadataOptions'] = {
                'HttpTokens': 'required',
                'HttpEndpoint': 'enabled',
            }
        elif label.imdsv2 == 'optional':
            args['MetadataOptions'] = {
                'HttpTokens': 'optional',
                'HttpEndpoint': 'enabled',
            }

        with self.rate_limiter(log.debug, "Created instance"):
            log.debug("Creating VM %s", hostname)
            resp = self.ec2_client.run_instances(**args)
            instances = resp['Instances']
            log.debug("Created VM %s as instance %s",
                      hostname, instances[0]['InstanceId'])
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
            if instance['InstanceId'] == external_id:
                break
        else:
            log.warning(f"Instance not found when deleting {external_id}")
            return None
        if immediate:
            with self.rate_limiter(log.debug, "Deleted instance"):
                log.debug(f"Deleting instance {external_id}")
                self.ec2_client.terminate_instances(
                    InstanceIds=[instance['InstanceId']])
        else:
            self.delete_queue.put((external_id, log))
        return instance

    def _deleteVolume(self, external_id):
        for volume in self._listVolumes():
            if volume['VolumeId'] == external_id:
                break
        else:
            self.log.warning(f"Volume not found when deleting {external_id}")
            return None
        with self.rate_limiter(self.log.debug, "Deleted volume"):
            self.log.debug(f"Deleting volume {external_id}")
            self.ec2_client.delete_volume(VolumeId=volume['VolumeId'])
        return volume

    def _deleteAmi(self, external_id):
        for ami in self._listAmis():
            if ami['ImageId'] == external_id:
                break
        else:
            self.log.warning(f"AMI not found when deleting {external_id}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting AMI {external_id}")
            self.ec2_client.deregister_image(ImageId=ami['ImageId'])
        return ami

    def _deleteSnapshot(self, external_id):
        for snap in self._listSnapshots():
            if snap['SnapshotId'] == external_id:
                break
        else:
            self.log.warning(f"Snapshot not found when deleting {external_id}")
            return None
        with self.rate_limiter:
            self.log.debug(f"Deleting Snapshot {external_id}")
            self.ec2_client.delete_snapshot(SnapshotId=snap['SnapshotId'])
        return snap

    def _deleteObject(self, external_id):
        bucket_name = self.provider.object_storage.get('bucket-name')
        with self.rate_limiter:
            self.log.debug(f"Deleting object {external_id}")
            self.s3.Object(bucket_name, external_id).delete()
