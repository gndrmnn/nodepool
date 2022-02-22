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

import boto3
from moto import mock_ec2
import testtools

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool import zk
from nodepool.nodeutils import iterate_timeout
import nodepool.driver.statemachine
from nodepool.driver.statemachine import StateMachineProvider


def fake_nodescan(*args, **kw):
    return ['ssh-rsa FAKEKEY']


class TestDriverAws(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAws")
    mock_ec2 = mock_ec2()

    def setUp(self):
        super().setUp()

        self.mock_ec2.start()
        StateMachineProvider.MINIMUM_SLEEP = 0.1
        StateMachineProvider.MAXIMUM_SLEEP = 1
        aws_id = 'AK000000000000000000'
        aws_key = '0123456789abcdef0123456789abcdef0123456789abcdef'
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_ACCESS_KEY_ID', aws_id))
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_SECRET_ACCESS_KEY', aws_key))
        self.ec2 = boto3.resource('ec2', region_name='us-west-2')
        self.ec2_client = boto3.client('ec2', region_name='us-west-2')

        # TEST-NET-3
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

        instance = self.ec2.Instance(node.external_id)
        response = instance.describe_attribute(Attribute='ebsOptimized')
        self.assertFalse(response['EbsOptimized']['Value'])

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
