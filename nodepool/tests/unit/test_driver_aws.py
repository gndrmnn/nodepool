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
from moto import mock_ec2, mock_s3, mock_iam
import testtools

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool.zk import zookeeper as zk
from nodepool.nodeutils import iterate_timeout
import nodepool.driver.statemachine
from nodepool.driver.statemachine import StateMachineProvider
import nodepool.driver.aws.adapter
from nodepool.driver.aws.adapter import AwsInstance, AwsAdapter

from nodepool.tests.unit.fake_aws import FakeAws


def fake_nodescan(*args, **kw):
    return ['ssh-rsa FAKEKEY']


class Dummy:
    pass


class FakeAwsAdapter(AwsAdapter):
    # Patch/override adapter methods to aid unit tests
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)

        # Note: boto3 doesn't handle ipv6 addresses correctly
        # when in fake mode so we need to intercept the
        # create_instances call and validate the args we supply.
        def _fake_create_instances(*args, **kwargs):
            self.__testcase.create_instance_calls.append(kwargs)
            return self.ec2.create_instances_orig(*args, **kwargs)

        self.ec2.create_instances_orig = self.ec2.create_instances
        self.ec2.create_instances = _fake_create_instances
        self.ec2_client.import_snapshot = \
            self.__testcase.fake_aws.import_snapshot
        self.ec2_client.get_paginator = \
            self.__testcase.fake_aws.get_paginator

        # moto does not mock service-quotas, so we do it ourselves:
        def _fake_get_service_quota(ServiceCode, QuotaCode, *args, **kwargs):
            # This is a simple fake that only returns the number
            # of cores.
            if self.__quotas is None:
                return {'Quota': {'Value': 100}}
            else:
                return {'Quota': {'Value': self.__quotas.get(QuotaCode)}}
        self.aws_quotas.get_service_quota = _fake_get_service_quota


def aws_quotas(quotas):
    """Specify a set of AWS quota values for use by a test method.

    :arg dict quotas: The quota dictionary.
    """

    def decorator(test):
        test.__aws_quotas__ = quotas
        return test
    return decorator


class TestDriverAws(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAws")
    mock_ec2 = mock_ec2()
    mock_s3 = mock_s3()
    mock_iam = mock_iam()

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
        self.mock_iam.start()

        self.ec2 = boto3.resource('ec2', region_name='us-west-2')
        self.ec2_client = boto3.client('ec2', region_name='us-west-2')
        self.s3 = boto3.resource('s3', region_name='us-west-2')
        self.s3_client = boto3.client('s3', region_name='us-west-2')
        self.iam = boto3.resource('iam', region_name='us-west-2')
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

        profile = self.iam.create_instance_profile(
            InstanceProfileName='not-a-real-profile')
        self.instance_profile_name = profile.name
        self.instance_profile_arn = profile.arn

        self.security_group = self.ec2_client.create_security_group(
            GroupName='zuul-nodes', VpcId=self.vpc['Vpc']['VpcId'],
            Description='Zuul Nodes')
        self.security_group_id = self.security_group['GroupId']
        self.patch(nodepool.driver.statemachine, 'nodescan', fake_nodescan)
        test_name = self.id().split('.')[-1]
        test = getattr(self, test_name)
        if hasattr(test, '__aws_quotas__'):
            quotas = getattr(test, '__aws_quotas__')
        else:
            quotas = None
        self.patchAdapter(quotas=quotas)

    def tearDown(self):
        self.mock_ec2.stop()
        self.mock_s3.stop()
        super().tearDown()

    def setup_config(self, *args, **kw):
        kw['subnet_id'] = self.subnet_id
        kw['security_group_id'] = self.security_group_id
        kw['instance_profile_name'] = self.instance_profile_name
        kw['instance_profile_arn'] = self.instance_profile_arn
        return super().setup_config(*args, **kw)

    def patchAdapter(self, quotas=None):
        self.patch(nodepool.driver.aws.adapter, 'AwsAdapter', FakeAwsAdapter)
        self.patch(nodepool.driver.aws.adapter.AwsAdapter,
                   '_FakeAwsAdapter__testcase', self)
        self.patch(nodepool.driver.aws.adapter.AwsAdapter,
                   '_FakeAwsAdapter__quotas', quotas)

    def requestNode(self, config_path, label):
        # A helper method to perform a single node request
        configfile = self.setup_config(config_path)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

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

    def test_aws_multiple(self):
        # Test creating multiple instances at once.  This is most
        # useful to run manually during development to observe
        # behavior.
        configfile = self.setup_config('aws/aws-multiple.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        reqs = []
        for x in range(4):
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.node_types.append('ubuntu1404')
            self.zk.storeNodeRequest(req)
            reqs.append(req)

        nodes = []
        for req in reqs:
            self.log.debug("Waiting for request %s", req.id)
            req = self.waitForNodeRequest(req)
            nodes.append(self.assertSuccess(req))
        for node in nodes:
            node.state = zk.USED
            self.zk.storeNode(node)
        for node in nodes:
            self.waitForNodeDeletion(node)

    @aws_quotas({
        'L-1216C47A': 1,
        'L-43DA4232': 224,
    })
    def test_aws_multi_quota(self):
        # Test multiple instance type quotas (standard and high-mem)
        configfile = self.setup_config('aws/aws-quota.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Create a high-memory node request.
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('high')
        self.zk.storeNodeRequest(req1)
        self.log.debug("Waiting for request %s", req1.id)
        req1 = self.waitForNodeRequest(req1)
        node1 = self.assertSuccess(req1)

        # Create a second high-memory node request; this should be
        # over quota so it won't be fulfilled.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('high')
        self.zk.storeNodeRequest(req2)
        self.log.debug("Waiting for request %s", req2.id)
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # Make sure we're paused while we attempt to fulfill the
        # second request.
        pool_worker = pool.getPoolWorkers('ec2-us-west-2')
        for _ in iterate_timeout(30, Exception, 'paused handler'):
            if pool_worker[0].paused_handlers:
                break

        # Release the first node so that the second can be fulfilled.
        node1.state = zk.USED
        self.zk.storeNode(node1)
        self.waitForNodeDeletion(node1)

        # Make sure the second high node exists now.
        req2 = self.waitForNodeRequest(req2)
        self.assertSuccess(req2)

        # Create a standard node request which should succeed even
        # though we're at quota for high-mem (but not standard).
        req3 = zk.NodeRequest()
        req3.state = zk.REQUESTED
        req3.node_types.append('standard')
        self.zk.storeNodeRequest(req3)
        self.log.debug("Waiting for request %s", req3.id)
        req3 = self.waitForNodeRequest(req3)
        self.assertSuccess(req3)

    @aws_quotas({
        'L-1216C47A': 1000,
        'L-43DA4232': 1000,
    })
    def test_aws_multi_pool_limits(self):
        # Test multiple instance type quotas (standard and high-mem)
        # with pool resource limits
        configfile = self.setup_config('aws/aws-limits.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Create a standard node request.
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('standard')
        self.zk.storeNodeRequest(req1)
        self.log.debug("Waiting for request %s", req1.id)
        req1 = self.waitForNodeRequest(req1)
        node1 = self.assertSuccess(req1)

        # Create a second standard node request; this should be
        # over max-cores so it won't be fulfilled.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('standard')
        self.zk.storeNodeRequest(req2)
        self.log.debug("Waiting for request %s", req2.id)
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # Make sure we're paused while we attempt to fulfill the
        # second request.
        pool_worker = pool.getPoolWorkers('ec2-us-west-2')
        for _ in iterate_timeout(30, Exception, 'paused handler'):
            if pool_worker[0].paused_handlers:
                break

        # Release the first node so that the second can be fulfilled.
        node1.state = zk.USED
        self.zk.storeNode(node1)
        self.waitForNodeDeletion(node1)

        # Make sure the second standard node exists now.
        req2 = self.waitForNodeRequest(req2)
        self.assertSuccess(req2)

    @aws_quotas({
        'L-1216C47A': 1000,
        'L-43DA4232': 1000,
    })
    def test_aws_multi_tenant_limits(self):
        # Test multiple instance type quotas (standard and high-mem)
        # with tenant resource limits
        configfile = self.setup_config('aws/aws-limits.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Create a high node request.
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.tenant_name = 'tenant-1'
        req1.node_types.append('high')
        self.zk.storeNodeRequest(req1)
        self.log.debug("Waiting for request %s", req1.id)
        req1 = self.waitForNodeRequest(req1)
        self.assertSuccess(req1)

        # Create a second high node request; this should be
        # over quota so it won't be fulfilled.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.tenant_name = 'tenant-1'
        req2.node_types.append('high')
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2, (zk.REQUESTED,))

        # Create a standard node request which should succeed even
        # though we're at quota for high-mem (but not standard).
        req3 = zk.NodeRequest()
        req3.state = zk.REQUESTED
        req3.tenant_name = 'tenant-1'
        req3.node_types.append('standard')
        self.zk.storeNodeRequest(req3)
        self.log.debug("Waiting for request %s", req3.id)
        req3 = self.waitForNodeRequest(req3)
        self.assertSuccess(req3)

        # Assert that the second request is still being deferred
        req2 = self.waitForNodeRequest(req2, (zk.REQUESTED,))

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
        self.assertEqual(node.cloud, 'AWS')
        self.assertEqual(node.region, 'us-west-2')
        # Like us-west-2x where x is random
        self.assertTrue(len(node.az) == len('us-west-2x'))

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

    def test_aws_iam_instance_profile_name(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-iam-instance-profile-name')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')
        associations = self.ec2_client.\
            describe_iam_instance_profile_associations()[
                "IamInstanceProfileAssociations"]
        self.assertEqual(node.external_id, associations[0]['InstanceId'])
        self.assertEqual(self.instance_profile_arn,
                         associations[0]['IamInstanceProfile']['Arn'])

    def test_aws_iam_instance_profile_arn(self):
        req = self.requestNode('aws/aws.yaml',
                               'ubuntu1404-iam-instance-profile-arn')
        node = self.assertSuccess(req)
        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')
        associations = self.ec2_client.\
            describe_iam_instance_profile_associations()[
                "IamInstanceProfileAssociations"]
        self.assertEqual(node.external_id, associations[0]['InstanceId'])
        self.assertEqual(self.instance_profile_arn,
                         associations[0]['IamInstanceProfile']['Arn'])

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
        instance.subnet = Dummy()
        instance.subnet.availability_zone = 'us-west-2b'
        iface = Dummy()
        iface.ipv6_addresses = [{'Ipv6Address': 'fe80::dead:beef'}]
        instance.network_interfaces = [iface]
        provider = Dummy()
        provider.region_name = 'us-west-2'
        awsi = AwsInstance(provider, instance, None)
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
        self.assertIn(
            {"Key": "dynamic-tenant", "Value": "Tenant is tenant-1"}, tag_list)

    def test_aws_min_ready(self):
        # Test dynamic tag formatting without a real node request
        configfile = self.setup_config('aws/aws-min-ready.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        node = self.waitForNodes('ubuntu1404-with-tags')[0]

        self.assertEqual(node.host_keys, ['ssh-rsa FAKEKEY'])
        self.assertEqual(node.image_id, 'ubuntu1404')

        instance = self.ec2.Instance(node.external_id)
        tag_list = instance.tags
        self.assertIn({"Key": "has-tags", "Value": "true"}, tag_list)
        self.assertIn({"Key": "Name", "Value": "np0000000000"}, tag_list)
        self.assertNotIn({"Key": "Name", "Value": "ignored-name"}, tag_list)
        self.assertIn(
            {"Key": "dynamic-tenant", "Value": "Tenant is None"}, tag_list)

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
        configfile = self.setup_config('aws/diskimage.yaml')

        self.useBuilder(configfile)

        image = self.waitForImage('ec2-us-west-2', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        ec2_image = self.ec2.Image(image.external_id)
        self.assertEqual(ec2_image.state, 'available')
        self.assertTrue({'Key': 'diskimage_metadata', 'Value': 'diskimage'}
                        in ec2_image.tags)
        self.assertTrue({'Key': 'provider_metadata', 'Value': 'provider'}
                        in ec2_image.tags)

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

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
        self.assertEqual(
            self.create_instance_calls[0]['BlockDeviceMappings'][0]['Ebs']
            ['Iops'], 2000)
        self.assertEqual(
            self.create_instance_calls[0]['BlockDeviceMappings'][0]['Ebs']
            ['Throughput'], 200)

    def test_aws_diskimage_removal(self):
        configfile = self.setup_config('aws/diskimage.yaml')
        self.useBuilder(configfile)
        self.waitForImage('ec2-us-west-2', 'fake-image')
        self.replace_config(configfile, 'aws/config.yaml')
        self.waitForImageDeletion('ec2-us-west-2', 'fake-image')
        self.waitForBuildDeletion('fake-image', '0000000001')

    def test_aws_resource_cleanup(self):
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

        task = self.fake_aws.import_snapshot(
            DiskContainer={
                'Format': 'ova',
                'UserBucket': {
                    'S3Bucket': 'nodepool',
                    'S3Key': 'testfile',
                }
            },
            TagSpecifications=[{
                'ResourceType': 'import-snapshot-task',
                'Tags': image_tags,
            }])
        snapshot_id = self.fake_aws.finish_import_snapshot(task)

        register_response = self.ec2_client.register_image(
            Architecture='amd64',
            BlockDeviceMappings=[
                {
                    'DeviceName': '/dev/sda1',
                    'Ebs': {
                        'DeleteOnTermination': True,
                        'SnapshotId': snapshot_id,
                        'VolumeSize': 20,
                        'VolumeType': 'gp2',
                    },
                },
            ],
            RootDeviceName='/dev/sda1',
            VirtualizationType='hvm',
            Name='testimage',
        )
        image_id = register_response['ImageId']

        ami = self.ec2.Image(image_id)
        new_snapshot_id = ami.block_device_mappings[0]['Ebs']['SnapshotId']
        self.fake_aws.change_snapshot_id(task, new_snapshot_id)

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
                # If this has a value the image was not deleted
                if image.state == 'available':
                    # Definitely not deleted yet
                    continue
            except AttributeError:
                # Per AWS API, a recently deleted image is empty and
                # looking at the state raises an AttributeFailure; see
                # https://github.com/boto/boto3/issues/2531.  The image
                # was deleted, so we continue on here
                break

        for _ in iterate_timeout(30, Exception, 'snapshot deletion'):
            snap = self.ec2.Snapshot(new_snapshot_id)
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
