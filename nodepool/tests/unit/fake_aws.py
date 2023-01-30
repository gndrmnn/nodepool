# Copyright 2022 Acme Gating, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import uuid

import boto3


def make_import_snapshot_stage_1(task_id, user_bucket, tags):
    return {
        'Architecture': 'x86_64',
        'ImportTaskId': f'import-snap-{task_id}',
        'Progress': '19',
        'SnapshotTaskDetail': {'DiskImageSize': 355024384.0,
                               'Format': 'VMDK',
                               'Status': 'active',
                               'UserBucket': user_bucket},
        'Status': 'active',
        'StatusMessage': 'converting',
        'Tags': tags,
    }


def make_import_snapshot_stage_2(task_id, snap_id, task):
    # Make a unique snapshot id that's different than the task id.
    return {
        'ImportTaskId': f'import-snap-{task_id}',
        'SnapshotTaskDetail': {'DiskImageSize': 355024384.0,
                               'Format': 'VMDK',
                               'SnapshotId': snap_id,
                               'Status': 'completed',
                               'UserBucket':
                               task['SnapshotTaskDetail']['UserBucket']},
        'Status': 'completed',
        'Tags': task['Tags'],
    }


def make_import_image_stage_1(task_id, user_bucket, tags):
    return {
        'Architecture': 'x86_64',
        'ImportTaskId': f'import-ami-{task_id}',
        'Progress': '19',
        'SnapshotDetails': [{'DiskImageSize': 355024384.0,
                             'Format': 'VMDK',
                             'Status': 'active',
                             'UserBucket': user_bucket}],
        'Status': 'active',
        'StatusMessage': 'converting',
        'Tags': tags,
    }


def make_import_image_stage_2(task_id, image_id, snap_id, task):
    # Make a unique snapshot id that's different than the task id.
    return {
        'Architecture': 'x86_64',
        'BootMode': 'legacy_bios',
        'ImageId': image_id,
        'ImportTaskId': f'import-ami-{task_id}',
        'LicenseType': 'BYOL',
        'Platform': 'Linux',
        'SnapshotDetails': [{'DeviceName': '/dev/sda1',
                             'DiskImageSize': 355024384.0,
                             'Format': 'VMDK',
                             'SnapshotId': snap_id,
                             'Status': 'completed',
                             'UserBucket':
                             task['SnapshotDetails'][0]['UserBucket']}],
        'Status': 'completed',
        'Tags': task['Tags'],
    }


class ImportSnapshotTaskPaginator:
    log = logging.getLogger("nodepool.FakeAws")

    def __init__(self, fake):
        self.fake = fake

    def paginate(self, **kw):
        tasks = list(self.fake.tasks.values())
        tasks = [t for t in tasks if 'import-snap' in t['ImportTaskId']]
        if 'ImportTaskIds' in kw:
            tasks = [t for t in tasks
                     if t['ImportTaskId'] in kw['ImportTaskIds']]
        # A page of tasks
        ret = [{'ImportSnapshotTasks': tasks}]

        # Move the task along
        for task in tasks:
            if task['Status'] != 'completed':
                self.fake.finish_import_snapshot(task)
        return ret


class ImportImageTaskPaginator:
    log = logging.getLogger("nodepool.FakeAws")

    def __init__(self, fake):
        self.fake = fake

    def paginate(self, **kw):
        tasks = list(self.fake.tasks.values())
        tasks = [t for t in tasks if 'import-ami' in t['ImportTaskId']]
        if 'ImportTaskIds' in kw:
            tasks = [t for t in tasks
                     if t['ImportTaskId'] in kw['ImportTaskIds']]
        # A page of tasks
        ret = [{'ImportImageTasks': tasks}]

        # Move the task along
        for task in tasks:
            if task['Status'] != 'completed':
                self.fake.finish_import_image(task)
        return ret


class FakeAws:
    log = logging.getLogger("nodepool.FakeAws")

    def __init__(self):
        self.tasks = {}
        self.ec2 = boto3.resource('ec2', region_name='us-west-2')
        self.ec2_client = boto3.client('ec2', region_name='us-west-2')

    def import_snapshot(self, *args, **kw):
        task_id = uuid.uuid4().hex
        task = make_import_snapshot_stage_1(
            task_id,
            kw['DiskContainer']['UserBucket'],
            kw['TagSpecifications'][0]['Tags'])
        self.tasks[task_id] = task
        return task

    def finish_import_snapshot(self, task):
        task_id = task['ImportTaskId'].split('-')[-1]

        # Make a Volume to simulate the import finishing
        volume = self.ec2_client.create_volume(
            Size=80,
            AvailabilityZone='us-west-2')
        snap_id = self.ec2_client.create_snapshot(
            VolumeId=volume['VolumeId'],
        )["SnapshotId"]

        t2 = make_import_snapshot_stage_2(task_id, snap_id, task)
        self.tasks[task_id] = t2
        return snap_id

    def import_image(self, *args, **kw):
        task_id = uuid.uuid4().hex
        task = make_import_image_stage_1(
            task_id,
            kw['DiskContainers'][0]['UserBucket'],
            kw['TagSpecifications'][0]['Tags'])
        self.tasks[task_id] = task
        return task

    def finish_import_image(self, task):
        task_id = task['ImportTaskId'].split('-')[-1]

        # Make an AMI to simulate the import finishing
        reservation = self.ec2_client.run_instances(
            ImageId="ami-12c6146b", MinCount=1, MaxCount=1)
        instance = reservation["Instances"][0]
        instance_id = instance["InstanceId"]

        response = self.ec2_client.create_image(
            InstanceId=instance_id,
            Name=f'import-ami-{task_id}',
        )

        image_id = response["ImageId"]
        self.ec2_client.describe_images(ImageIds=[image_id])["Images"][0]

        volume = self.ec2_client.create_volume(
            Size=80,
            AvailabilityZone='us-west-2')
        snap_id = self.ec2_client.create_snapshot(
            VolumeId=volume['VolumeId'],
            Description=f'imported volume import-ami-{task_id}',
        )["SnapshotId"]

        t2 = make_import_image_stage_2(task_id, image_id, snap_id, task)
        self.tasks[task_id] = t2
        return (image_id, snap_id)

    def change_snapshot_id(self, task, snapshot_id):
        # Given a task, update its snapshot id; the moto
        # register_image mock doesn't honor the snapshot_id we pass
        # in.
        task_id = task['ImportTaskId'].split('-')[-1]
        self.tasks[task_id]['SnapshotTaskDetail']['SnapshotId'] = snapshot_id

    def get_paginator(self, name):
        if name == 'describe_import_image_tasks':
            return ImportImageTaskPaginator(self)
        if name == 'describe_import_snapshot_tasks':
            return ImportSnapshotTaskPaginator(self)
        raise NotImplementedError()

    def _listAmis(self):
        return list(self.ec2.images.filter())

    def _listSnapshots(self):
        return list(self.ec2.snapshots.filter())
