# Copyright (C) 2018 Red Hat
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
import os
import tempfile
import time
from unittest.mock import patch

import boto3
from moto import mock_ec2, mock_s3
import yaml

from nodepool import tests
from nodepool import zk
from nodepool.nodeutils import iterate_timeout
from nodepool.zk import ImageUpload, ImageBuild


class TestDriverAws(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverAws")

    def _wait_for_provider(self, nodepool, provider):
        for _ in iterate_timeout(
                30, Exception, 'wait for provider'):
            try:
                provider_manager = nodepool.getProviderManager(provider)
                if provider_manager.ec2 is not None:
                    break
            except Exception:
                pass


    def _get_aws_config(self, ec2, vpc):
        subnet = ec2.create_subnet(
            CidrBlock='203.0.113.128/25', VpcId=vpc['Vpc']['VpcId'])
        subnet_id = subnet['Subnet']['SubnetId']
        sg = ec2.create_security_group(
            GroupName='zuul-nodes', VpcId=vpc['Vpc']['VpcId'],
            Description='Zuul Nodes')
        sg_id = sg['GroupId']

        ec2_template = os.path.join(
            os.path.dirname(__file__), '..', 'fixtures', 'aws.yaml')
        with open(ec2_template) as f:
            raw_config = yaml.safe_load(f)
        raw_config['zookeeper-servers'][0] = {
            'host': self.zookeeper_host,
            'port': self.zookeeper_port,
            'chroot': self.zookeeper_chroot,
        }
        raw_config['zookeeper-tls'] = {
            'ca': self.zookeeper_ca,
            'cert': self.zookeeper_cert,
            'key': self.zookeeper_key,
        }
        raw_config['providers'][0]['pools'][0]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][0]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][1]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][1]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][2]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][2]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][3]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][3]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][4]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][4]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][5]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][5]['security-group-id'] = sg_id
        raw_config['providers'][0]['pools'][6]['subnet-id'] = subnet_id
        raw_config['providers'][0]['pools'][6]['security-group-id'] = sg_id

        return raw_config

    def _setup_aws_env_variables(self):
        aws_id = 'AK000000000000000000'
        aws_key = '0123456789abcdef0123456789abcdef0123456789abcdef'
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_ACCESS_KEY_ID', aws_id))
        self.useFixture(
            fixtures.EnvironmentVariable('AWS_SECRET_ACCESS_KEY', aws_key))

    def _get_provider_with_config(self, temp_file, raw_config):
        temp_file.write(yaml.safe_dump(
            raw_config, default_flow_style=False).encode('utf-8'))
        temp_file.flush()
        configfile = self.setup_config(temp_file.name)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self._wait_for_provider(pool, 'ec2-us-west-2')
        return pool.getProviderManager('ec2-us-west-2')

    @mock_ec2
    def _test_ec2_machine(self, label,
                          diskimage_label=False,
                          is_valid_config=True,
                          host_key_checking=True,
                          userdata=None,
                          public_ip=True,
                          tags=[],
                          shell_type=None):
        self._setup_aws_env_variables()

        ec2_resource = boto3.resource('ec2', region_name='us-west-2')
        ec2 = boto3.client('ec2', region_name='us-west-2')

        # TEST-NET-3
        vpc = ec2.create_vpc(CidrBlock='203.0.113.0/24')

        raw_config = self._get_aws_config(ec2, vpc)

        with tempfile.NamedTemporaryFile() as tf:
            provider_manager = self._get_provider_with_config(tf, raw_config)

            if diskimage_label:
                self._prepare_diskimage_label(provider_manager)

            # Note: boto3 doesn't handle private ip addresses correctly
            # when in fake mode so we need to intercept the
            # create_instances call and validate the args we supply.
            def _fake_create_instances(*args, **kwargs):
                self.assertIsNotNone(kwargs.get('NetworkInterfaces'))
                interfaces = kwargs.get('NetworkInterfaces')
                self.assertEqual(1, len(interfaces))
                if not public_ip:
                    self.assertEqual(
                        False,
                        interfaces[0].get('AssociatePublicIpAddress'))
                return provider_manager.ec2.create_instances_orig(
                    *args, **kwargs)

            provider_manager.ec2.create_instances_orig = \
                provider_manager.ec2.create_instances
            provider_manager.ec2.create_instances = _fake_create_instances

            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.node_types.append(label)
            with patch('nodepool.driver.aws.handler.nodescan') as nodescan:
                nodescan.return_value = 'MOCK KEY'
                self.zk.storeNodeRequest(req)

                self.log.debug("Waiting for request %s", req.id)
                req = self.waitForNodeRequest(req)

                if is_valid_config is False:
                    self.assertEqual(req.state, zk.FAILED)
                    self.assertEqual(req.nodes, [])
                    return

                self.assertEqual(req.state, zk.FULFILLED)
                self.assertNotEqual(req.nodes, [])

                node = self.zk.getNode(req.nodes[0])
                self.assertEqual(node.allocated_to, req.id)
                self.assertEqual(node.state, zk.READY)
                self.assertIsNotNone(node.launcher)
                self.assertEqual(node.connection_type, 'ssh')
                self.assertEqual(node.attributes,
                                 {'key1': 'value1', 'key2': 'value2'})
                self.assertEqual(node.shell_type, shell_type)
                if host_key_checking:
                    nodescan.assert_called_with(
                        node.interface_ip,
                        port=22,
                        timeout=180,
                        gather_hostkeys=True)
                if userdata:
                    instance = ec2_resource.Instance(node.external_id)
                    response = instance.describe_attribute(
                        Attribute='userData')
                    self.assertIn('UserData', response)
                    userdata = base64.b64decode(
                        response['UserData']['Value']).decode()
                    self.assertEqual('fake-user-data', userdata)
                if tags:
                    instance = ec2_resource.Instance(node.external_id)
                    tag_list = instance.tags
                    for tag in tags:
                        self.assertIn(tag, tag_list)

                # A new request will be paused and for lack of quota
                # until this one is deleted
                req2 = zk.NodeRequest()
                req2.state = zk.REQUESTED
                req2.node_types.append(label)
                self.zk.storeNodeRequest(req2)
                req2 = self.waitForNodeRequest(
                    req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.PENDING)
                # It could flip from PENDING to one of the others,
                # so sleep a bit and be sure
                time.sleep(1)
                req2 = self.waitForNodeRequest(
                    req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.PENDING)

                node.state = zk.DELETING
                self.zk.storeNode(node)

                self.waitForNodeDeletion(node)

                req2 = self.waitForNodeRequest(req2,
                                               (zk.FAILED, zk.FULFILLED))
                self.assertEqual(req2.state, zk.FULFILLED)
                node = self.zk.getNode(req2.nodes[0])
                node.state = zk.DELETING
                self.zk.storeNode(node)
                self.waitForNodeDeletion(node)

    def _prepare_diskimage_label(self, provider_manager):
        self.zk.storeBuild('dib-ubuntu1804', ImageBuild(build_id='12345'))
        image_upload_dict = {"external_id": "ami-1e749f67", "state": "ready"}
        image_upload = ImageUpload.fromDict(image_upload_dict,
                                            build_id='12345',
                                            provider_name=provider_manager.
                                            provider.name,
                                            image_name='dib-ubuntu1804',
                                            upload_id='12345')
        self.zk.storeImageUpload('dib-ubuntu1804', 1,
                                 provider_manager.provider.name, image_upload)

    def test_ec2_machine(self):
        self._test_ec2_machine('ubuntu1404')

    def test_ec2_machine_by_filters(self):
        self._test_ec2_machine('ubuntu1404-by-filters')

    def test_ec2_machine_by_filters_capitalized(self):
        self._test_ec2_machine('ubuntu1404-by-capitalized-filters')

    def test_ec2_machine_bad_ami_name(self):
        self._test_ec2_machine('ubuntu1404-bad-ami-name',
                               is_valid_config=False)

    def test_ec2_machine_bad_config(self):
        self._test_ec2_machine('ubuntu1404-bad-config',
                               is_valid_config=False)

    def test_ec2_machine_non_host_key_checking(self):
        self._test_ec2_machine('ubuntu1404-non-host-key-checking',
                               host_key_checking=False)

    def test_ec2_machine_userdata(self):
        self._test_ec2_machine('ubuntu1404-userdata',
                               userdata=True)

    # Note(avass): moto does not yet support attaching an instance profile
    # but these two at least tests to make sure that the instances 'starts'
    def test_ec2_machine_iam_instance_profile_name(self):
        self._test_ec2_machine('ubuntu1404-iam-instance-profile-name')

    def test_ec2_machine_iam_instance_profile_arn(self):
        self._test_ec2_machine('ubuntu1404-iam-instance-profile-arn')

    def test_ec2_machine_private_ip(self):
        self._test_ec2_machine('ubuntu1404-private-ip',
                               public_ip=False)

    def test_ec2_machine_tags(self):
        self._test_ec2_machine('ubuntu1404-with-tags',
                               tags=[
                                   {"Key": "has-tags", "Value": "true"},
                                   {"Key": "Name",
                                    "Value": "ubuntu1404-with-tags"}
                               ])

    def test_ec2_machine_name_tag(self):
        self._test_ec2_machine('ubuntu1404-with-name-tag',
                               tags=[
                                   {"Key": "Name", "Value": "different-name"}
                               ])

    def test_ec2_machine_shell_type(self):
        self._test_ec2_machine('ubuntu1404-with-shell-type',
                               shell_type="csh")

    def test_ec2_with_diskimage(self):
        self._test_ec2_machine('dib-ubuntu1804', diskimage_label=True)

    @mock_s3
    @mock_ec2
    def test_image_upload(self):
        self._setup_aws_env_variables()
        aws_region = 'us-west-2'
        ec2 = boto3.client('ec2', region_name=aws_region)
        vpc = ec2.create_vpc(CidrBlock='203.0.113.0/24')
        raw_config = self._get_aws_config(ec2, vpc)
        with tempfile.NamedTemporaryFile() as tf:
            provider_manager = self._get_provider_with_config(tf, raw_config)
            s3 = boto3.resource('s3')
            bucket_name = provider_manager.provider.s3_image_bucket
            s3.create_bucket(Bucket=bucket_name,
                             CreateBucketConfiguration={
                                 'LocationConstraint': aws_region})
            s3_bucket = s3.Bucket(bucket_name)

            ami_import_task_id = 'ami-import-id-123'
            ami_id = "ami-1234"

            def _import_image_mock(**kwargs):
                return {'ImportTaskId': ami_import_task_id}

            def _describe_ami_import_mock(**kwargs):
                assert kwargs['ImportTaskIds'][0] == ami_import_task_id
                return {'ImportImageTasks': [{
                    'Status': 'completed',
                    'ImageId': ami_id}]}

            def _ec2_create_tags_mock(Resources, Tags):
                assert Resources[0] == ami_id

            provider_manager.ec2_client.import_image = _import_image_mock
            provider_manager.ec2_client.describe_import_image_tasks =\
                _describe_ami_import_mock
            provider_manager.ec2_client.create_tags = _ec2_create_tags_mock
            image_name = 'dib-ubuntu1804'
            with tempfile.NamedTemporaryFile() as image_tf:
                meta = {'nodepool_build_id': 'aws-test-123'}
                uploaded_image_id = provider_manager.uploadImage(
                    image_name, image_tf.name, meta=meta)
                for content in s3_bucket.objects.all():
                    assert False
                assert uploaded_image_id == ami_id
