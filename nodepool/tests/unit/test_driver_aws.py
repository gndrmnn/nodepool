# Copyright (C) 2018 Red Hat
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
import base64

import fixtures
import logging
import urllib.parse

import boto3
import botocore.exceptions
from moto import mock_ec2, mock_s3
import testtools

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool import zk
from nodepool.nodeutils import iterate_timeout
import nodepool.driver.statemachine
from nodepool.driver.statemachine import StateMachineProvider
from nodepool.driver.aws.adapter import AwsInstance, AwsAdapter

from nodepool.tests.unit.fake_aws import FakeAws


def fake_nodescan(*args, **kw):
    return ['ssh-rsa FAKEKEY']


class Dummy:
    pass


class TestDriverAws(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAws")
    mock_ec2 = mock_ec2()
    mock_s3 = mock_s3()

    def setUp(self):
        super().setUp()

        StateMachineProvider.MINIMUM_SLEEP = 0.1
        StateMachineProvider.MAXIMUM_SLEEP = 1
        AwsAdapter.IMAGE_UPLOAD_SLEEP = 1
        aws_id = 'AK000000000000000000'
        aws_key = '0123456789abcdef0123456789abcdef0123456789abcdef'
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_ACCESS_KEY_ID', aws_id))
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_SECRET_ACCESS_KEY', aws_key))

        self.fake_aws = FakeAws()
        self.mock_ec2.start()
        self.mock_s3.start()

        self.ec2 = boto3.resource('ec2', region_name='us-west-2')
        self.ec2_client = boto3.client('ec2', region_name='us-west-2')
        self.s3 = boto3.resource('s3', region_name='us-west-2')
        self.s3_client = boto3.client('s3', region_name='us-west-2')
        self.s3.create_bucket(
            Bucket='nodepool',
            CreateBucketConfiguration={'LocationConstraint': 'us-west-2'})

        # A list of args to create instance for validation
        self.create_instance_calls = []

        # TEST-NET-3
        ipv6 = False
        if ipv6:
            # This is currently unused, but if moto gains IPv6 support
            # on instance creation, this may be useful.
            self.vpc = self.ec2_client.create_vpc(
                CidrBlock='203.0.113.0/24',
                AmazonProvidedIpv6CidrBlock=True)
            ipv6_cidr = self.vpc['Vpc'][
                'Ipv6CidrBlockAssociationSet'][0]['Ipv6CidrBlock']
            ipv6_cidr = ipv6_cidr.split('/')[0] + '/64'
            self.subnet = self.ec2_client.create_subnet(
                CidrBlock='203.0.113.128/25',
                Ipv6CidrBlock=ipv6_cidr,
                VpcId=self.vpc['Vpc']['VpcId'])
            self.subnet_id = self.subnet['Subnet']['SubnetId']
        else:
            self.vpc = self.ec2_client.create_vpc(CidrBlock='203.0.113.0/24')
            self.subnet = self.ec2_client.create_subnet(
                CidrBlock='203.0.113.128/25', VpcId=self.vpc['Vpc']['VpcId'])
            self.subnet_id = self.subnet['Subnet']['SubnetId']

        self.security_group = self.ec2_client.create_security_group(
            GroupName='zuul-nodes', VpcId=self.vpc['Vpc']['VpcId'],
            Description='Zuul Nodes')
        self.security_group_id = self.security_group['GroupId']
        self.patch(nodepool.driver.statemachine, 'nodescan', fake_nodescan)

    def tearDown(self):
        self.mock_ec2.stop()
        self.mock_s3.stop()
        super().tearDown()

    def setup_config(self, *args, **kw):
        kw['subnet_id'] = self.subnet_id
        kw['security_group_id'] = self.security_group_id
        return super().setup_config(*args, **kw)

    def patchProvider(self, nodepool, provider_name='ec2-us-west-2'):
        for _ in iterate_timeout(
                30, Exception, 'wait for provider'):
            try:
                provider_manager = nodepool.getProviderManager(provider_name)
                if provider_manager.adapter.ec2 is not None:
                    break
            except Exception:
                pass

        # Note: boto3 doesn't handle ipv6 addresses correctly
        # when in fake mode so we need to intercept the
        # create_instances call and validate the args we supply.
        def _fake_create_instances(*args, **kwargs):
            self.create_instance_calls.append(kwargs)
            return provider_manager.adapter.ec2.create_instances_orig(
                *args, **kwargs)

        provider_manager.adapter.ec2.create_instances_orig =\
            provider_manager.adapter.ec2.create_instances
        provider_manager.adapter.ec2.create_instances =\
            _fake_create_instances

        # moto does not mock service-quotas, so we do it ourselves:
        def _fake_get_service_quota(*args, **kwargs):
            # This is a simple fake that only returns the number
            # of cores.
            return {'Quota': {'Value': 100}}
        provider_manager.adapter.aws_quotas.get_service_quota =\
            _fake_get_service_quota

    def requestNode(self, config_path, label):
        # A helper method to perform a single node request
        configfile = self.setup_config(config_path)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.patchProvider(pool)

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.tenant_name = 'tenant-1'
        req.node_types.append(label)

        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        return self.waitForNodeRequest(req)

    def assertSuccess(self, req):
        # Assert values common to most requests
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])

        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})
        return node

    def test_aws_node(self):
        req = self.requestNode('aws/aws.yaml', 'ubuntu1404')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        self.assertIsNotNone(node.public_ipv4)
        self.assertIsNotNone(node.private_ipv4)
        self.assertIsNone(node.public_ipv6)
        self.assertIsNotNone(node.interface_ip)
        self.assertEqual(node.public_ipv4, node.interface_ip)
        self.assertTrue(node.private_ipv4.startswith('203.0.113.'))
        self.assertFalse(node.public_ipv4.startswith('203.0.113.'))
        self.assertEqual(node.python_path, 'auto')

        instance = self.ec2.Instance(node.external_id)
        response = instance.describe_attribute(Attribute='ebsOptimized')
        self.assertFalse(response['EbsOptimized']['Value'])

        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

    def test_aws_by_filters(self):
        req = self.requestNode('aws/aws.yaml', 'ubuntu1404-by-filters')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404-by-filters')

    def test_aws_by_capitalized_filters(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-by-capitalized-filters')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404-by-capitalized-filters')

    def test_aws_bad_ami_name(self):
        req = self.requestNode('aws/aws.yaml', 'ubuntu1404-bad-ami-name')
        self.assertEqual(req.state, zk.FAILED)
        self.assertEqual(req.nodes, [])

    def test_aws_bad_config(self):
        # This fails config schema validation
        with testtools.ExpectedException(ValueError,
                                         ".*?could not be validated.*?"):
            self.setup_config('aws/bad-config-images.yaml')

    def test_aws_non_host_key_checking(self):
        req = self.requestNode('aws/non-host-key-checking.yaml',
                               'ubuntu1404-non-host-key-checking')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, [])

    def test_aws_userdata(self):
        req = self.requestNode('aws/aws.yaml', 'ubuntu1404-userdata')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        instance = self.ec2.Instance(node.external_id)
        response = instance.describe_attribute(
            Attribute='userData')
        self.assertIn('UserData', response)
        userdata = base64.b64decode(
            response['UserData']['Value']).decode()
        self.assertEqual('fake-user-data', userdata)

    # Note(avass): moto does not yet support attaching an instance profile
    # but these two at least tests to make sure that the instances 'starts'
    def test_aws_iam_instance_profile_name(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-iam-instance-profile-name')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

    def test_aws_iam_instance_profile_arn(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-iam-instance-profile-arn')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

    def test_aws_private_ip(self):
        req = self.requestNode('aws/private-ip.yaml', 'ubuntu1404-private-ip')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        self.assertIsNone(node.public_ipv4)
        self.assertIsNotNone(node.private_ipv4)
        self.assertIsNone(node.public_ipv6)
        self.assertIsNotNone(node.interface_ip)
        self.assertEqual(node.private_ipv4, node.interface_ip)
        self.assertTrue(node.private_ipv4.startswith('203.0.113.'))

    def test_aws_ipv6(self):
        req = self.requestNode('aws/ipv6.yaml', 'ubuntu1404-ipv6')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        self.assertIsNotNone(node.public_ipv4)
        self.assertIsNotNone(node.private_ipv4)
        # Not supported by moto
        # self.assertIsNotNone(node.public_ipv6)
        self.assertIsNotNone(node.interface_ip)
        self.assertEqual(node.public_ipv4, node.interface_ip)
        self.assertTrue(node.private_ipv4.startswith('203.0.113.'))

        # Moto doesn't support ipv6 assignment on creation, so we can
        # only unit test the parts.

        # Make sure we make the call to AWS as expected
        self.assertEqual(
            self.create_instance_calls[0]['NetworkInterfaces']
            [0]['Ipv6AddressCount'], 1)

        # This is like what we should get back from AWS, verify the
        # statemachine instance object has the parameters set
        # correctly.
        instance = Dummy()
        instance.id = 'test'
        instance.tags = []
        instance.private_ip_address = '10.0.0.1'
        instance.public_ip_address = '1.2.3.4'
        iface = Dummy()
        iface.ipv6_addresses = [{'Ipv6Address': 'fe80::dead:beef'}]
        instance.network_interfaces = [iface]
        awsi = AwsInstance(instance, None)
        self.assertEqual(awsi.public_ipv4, '1.2.3.4')
        self.assertEqual(awsi.private_ipv4, '10.0.0.1')
        self.assertEqual(awsi.public_ipv6, 'fe80::dead:beef')
        self.assertIsNone(awsi.private_ipv6)
        self.assertEqual(awsi.public_ipv4, awsi.interface_ip)

    def test_aws_tags(self):
        req = self.requestNode('aws/aws.yaml', 'ubuntu1404-with-tags')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        instance = self.ec2.Instance(node.external_id)
        tag_list = instance.tags
        self.assertIn({"Key": "has-tags", "Value": "true"}, tag_list)
        self.assertIn({"Key": "Name", "Value": "np0000000000"}, tag_list)
        self.assertNotIn({"Key": "Name", "Value": "ignored-name"}, tag_list)

    def test_aws_shell_type(self):
        req = self.requestNode('aws/shell-type.yaml',
                               'ubuntu1404-with-shell-type')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404-with-shell-type')
        self.assertEqual(node.shell_type, 'csh')

    def test_aws_config(self):
        configfile = self.setup_config('aws/config.yaml')
        config = nodepool_config.loadConfig(configfile)
        self.assertIn('ec2-us-west-2', config.providers)
        config2 = nodepool_config.loadConfig(configfile)
        self.assertEqual(config, config2)

    def test_aws_ebs_optimized(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-ebs-optimized')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        instance = self.ec2.Instance(node.external_id)
        response = instance.describe_attribute(Attribute='ebsOptimized')
        self.assertTrue(response['EbsOptimized']['Value'])

    def test_aws_diskimage(self):
        self.patch(AwsAdapter, '_import_image', self.fake_aws.import_image)
        self.patch(AwsAdapter, '_get_paginator', self.fake_aws.get_paginator)
        configfile = self.setup_config('aws/diskimage.yaml')

        self.useBuilder(configfile)

        image = self.waitForImage('ec2-us-west-2', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.patchProvider(pool)

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('diskimage')

        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)

        self.assertEqual(req.state, zk.FULFILLED)
        self.assertNotEqual(req.nodes, [])
        node = self.zk.getNode(req.nodes[0])
        self.assertEqual(node.allocated_to, req.id)
        self.assertEqual(node.state, zk.READY)
        self.assertIsNotNone(node.launcher)
        self.assertEqual(node.connection_type, 'ssh')
        self.assertEqual(node.shell_type, None)
        self.assertEqual(node.attributes,
                         {'key1': 'value1', 'key2': 'value2'})

    def test_aws_diskimage_removal(self):
        self.patch(AwsAdapter, '_import_image', self.fake_aws.import_image)
        self.patch(AwsAdapter, '_get_paginator', self.fake_aws.get_paginator)
        configfile = self.setup_config('aws/diskimage.yaml')
        self.useBuilder(configfile)
        self.waitForImage('ec2-us-west-2', 'fake-image')
        self.replace_config(configfile, 'aws/config.yaml')
        self.waitForImageDeletion('ec2-us-west-2', 'fake-image')
        self.waitForBuildDeletion('fake-image', '0000000001')

    def test_aws_resource_cleanup(self):
        self.patch(AwsAdapter, '_get_paginator', self.fake_aws.get_paginator)

        # Start by setting up leaked resources
        instance_tags = [
            {'Key': 'nodepool_node_id', 'Value': '0000000042'},
            {'Key': 'nodepool_pool_name', 'Value': 'main'},
            {'Key': 'nodepool_provider_name', 'Value': 'ec2-us-west-2'}
        ]
        image_tags = [
            {'Key': 'nodepool_build_id', 'Value': '0000000042'},
            {'Key': 'nodepool_upload_id', 'Value': '0000000042'},
            {'Key': 'nodepool_provider_name', 'Value': 'ec2-us-west-2'}
        ]

        reservation = self.ec2_client.run_instances(
            ImageId="ami-12c6146b", MinCount=1, MaxCount=1,
            BlockDeviceMappings=[{
                'DeviceName': '/dev/sda1',
                'Ebs': {
                    'VolumeSize': 80,
                    'DeleteOnTermination': False
                }
            }],
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': instance_tags
            }, {
                'ResourceType': 'volume',
                'Tags': instance_tags
            }]
        )
        instance_id = reservation['Instances'][0]['InstanceId']

        task = self.fake_aws.import_image(
            DiskContainers=[{
                'Format': 'ova',
                'UserBucket': {
                    'S3Bucket': 'nodepool',
                    'S3Key': 'testfile',
                }
            }],
            TagSpecifications=[{
                'ResourceType': 'import-image-task',
                'Tags': image_tags,
            }])
        image_id, snapshot_id = self.fake_aws.finish_import_image(task)

        # Note that the resulting image and snapshot do not have tags
        # applied, so we test the automatic retagging methods in the
        # adapter.

        s3_tags = {
            'nodepool_build_id': '0000000042',
            'nodepool_upload_id': '0000000042',
            'nodepool_provider_name': 'ec2-us-west-2',
        }

        bucket = self.s3.Bucket('nodepool')
        bucket.put_object(Body=b'hi',
                          Key='testimage',
                          Tagging=urllib.parse.urlencode(s3_tags))
        obj = self.s3.Object('nodepool', 'testimage')
        # This effectively asserts the object exists
        self.s3_client.get_object_tagging(
            Bucket=obj.bucket_name, Key=obj.key)

        instance = self.ec2.Instance(instance_id)
        self.assertEqual(instance.state['Name'], 'running')

        volume_id = list(instance.volumes.all())[0].id
        volume = self.ec2.Volume(volume_id)
        self.assertEqual(volume.state, 'in-use')

        image = self.ec2.Image(image_id)
        self.assertEqual(image.state, 'available')

        snap = self.ec2.Snapshot(snapshot_id)
        self.assertEqual(snap.state, 'completed')

        # Now that the leaked resources exist, start the provider and
        # wait for it to clean them.

        configfile = self.setup_config('aws/diskimage.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.patchProvider(pool)

        for _ in iterate_timeout(30, Exception, 'instance deletion'):
            instance = self.ec2.Instance(instance_id)
            if instance.state['Name'] == 'terminated':
                break

        for _ in iterate_timeout(30, Exception, 'volume deletion'):
            volume = self.ec2.Volume(volume_id)
            try:
                if volume.state == 'deleted':
                    break
            except botocore.exceptions.ClientError:
                # Probably not found
                break

        for _ in iterate_timeout(30, Exception, 'ami deletion'):
            image = self.ec2.Image(image_id)
            try:
                if image.state == 'deleted':
                    break
            except botocore.exceptions.ClientError:
                # Probably not found
                break
            except AttributeError:
                # NOTE(ianw) 2022-04-27 For reasons that are unclear
                # but have been filed as
                #  https://github.com/spulec/moto/issues/5067
                # accessing the image.state property of a deleted image
                # with moto 1.3.6  will no longer result in the ClientError
                # above, but boto will get blank response and try to
                # dereference a None value.  Thus catching this is a
                # work-around until we have a better solution.
                break

        for _ in iterate_timeout(30, Exception, 'snapshot deletion'):
            snap = self.ec2.Snapshot(snapshot_id)
            try:
                if snap.state == 'deleted':
                    break
            except botocore.exceptions.ClientError:
                # Probably not found
                break

        for _ in iterate_timeout(30, Exception, 'object deletion'):
            obj = self.s3.Object('nodepool', 'testimage')
            try:
                self.s3_client.get_object_tagging(
                    Bucket=obj.bucket_name, Key=obj.key)
            except self.s3_client.exceptions.NoSuchKey:
                break
