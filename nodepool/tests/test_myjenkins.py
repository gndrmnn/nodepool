# Copyright (C) 2014 Marc Abramowitz
# Copyright (C) 2015 Antoine "hashar" Musso
# Copyright (C) 2015 Wikimedia Foundation Inc.
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

import json

from mock import patch
import testtools

import nodepool.myjenkins

class TestMyJenkins(testtools.TestCase):

    # 'reconfig_node',

    api_url = 'http://example.com/'

    def assertStartsWith(self, expected, candidate, msg=''):
        if msg is '':
            msg = '%s does not starts with %s' % (candidate, expected)
        self.assertTrue(candidate.startswith(expected), msg)

    @patch('nodepool.myjenkins.Jenkins.jenkins_open')
    def test_jenkins_create_node_uses_post(self, jenkins_mock):
        jenkins_mock.side_effect = [
            False,  # node_exists()
            None,
            json.dumps({"offline": False}),  # get_node_info()
            True,   # node_exists()
        ]
        j = nodepool.myjenkins.Jenkins(self.api_url, 'user', 'pass')
        j.create_node('computer01')

        request = jenkins_mock.call_args_list[1][0][0]

        self.assertStartsWith(
            self.api_url + nodepool.myjenkins.CREATE_NODE % '',
            request.get_full_url())
        self.assertEqual('POST', request.get_method())

    @patch('nodepool.myjenkins.Jenkins.jenkins_open')
    def test_jenkins_disable_node_uses_post(self, jenkins_mock):
        jenkins_mock.side_effect = [
            json.dumps({"offline": False}),  # get_node_info()
            None,
        ]
        j = nodepool.myjenkins.Jenkins(self.api_url, 'user', 'pass')
        j.disable_node('computer01')

        request = jenkins_mock.call_args_list[1][0][0]

        self.assertStartsWith(
            self.api_url + nodepool.myjenkins.TOGGLE_OFFLINE % { 'name': 'computer01', 'msg': '' },
            request.get_full_url())
        self.assertEqual('POST', request.get_method())

    @patch('nodepool.myjenkins.Jenkins.jenkins_open')
    def test_jenkins_enable_node_uses_post(self, jenkins_mock):
        jenkins_mock.side_effect = [
            json.dumps({"offline": True}),  # get_node_info()
            None,
        ]
        j = nodepool.myjenkins.Jenkins(self.api_url, 'user', 'pass')
        j.enable_node('computer01')

        request = jenkins_mock.call_args_list[1][0][0]

        self.assertStartsWith(
            self.api_url + nodepool.myjenkins.TOGGLE_OFFLINE % { 'name': 'computer01', 'msg': '' },
            request.get_full_url())
        self.assertEqual('POST', request.get_method())

    @patch('nodepool.myjenkins.Jenkins.jenkins_open')
    def test_jenkins_reconfig_node_uses_post(self, jenkins_mock):
        jenkins_mock.side_effect = [
            None,
        ]
        j = nodepool.myjenkins.Jenkins(self.api_url, 'user', 'pass')
        j.reconfig_node('computer01', '')

        request = jenkins_mock.call_args_list[0][0][0]

        self.assertStartsWith(
            self.api_url + nodepool.myjenkins.CONFIG_NODE % { 'name': 'computer01' },
            request.get_full_url())
        self.assertEqual('POST', request.get_method())
