# Copyright 2017 IBM Corp
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

import mock
from six.moves import configparser

from nodepool import mqtt
from nodepool import tests


class TestMQTT(tests.BaseTestCase):


    def test_get_client_without_mqtt_section(self):
        config = configparser.RawConfigParser()
        self.assertIsNone(mqtt.get_client(config))

    def test_get_client_without_mqtt_hostname_option(self):
        config = configparser.RawConfigParser()
        config.add_section('mqtt')
        config.set('mqtt', 'qos', 1)
        self.assertIsNone(mqtt.get_client(config))

    @mock.patch('nodepool.mqtt.PushMQTT')
    def test_get_client_no_auth_no_tls_with_defaults(self, PushMQTTMock):
        config = configparser.RawConfigParser()
        config.add_section('mqtt')
        config.set('mqtt', 'hostname', 'fakehost')
        mqtt.get_client(config)
        PushMQTTMock.assert_called_once_with('fakehost', 'nodepool', auth=None,
                                             keepalive=60, port=1883, qos=0,
                                             tls=None)

    @mock.patch('nodepool.mqtt.PushMQTT')
    def test_get_client_with_auth_and_defaults_no_tls(self, PushMQTTMock):
        config = configparser.RawConfigParser()
        config.add_section('mqtt')
        config.set('mqtt', 'hostname', 'fakehost')
        config.set('mqtt', 'username', 'fakeuser')
        config.set('mqtt', 'password', 'pass')
        mqtt.get_client(config)
        auth_dict = {'username': 'fakeuser', 'password': 'pass'}
        PushMQTTMock.assert_called_once_with('fakehost', 'nodepool',
                                             auth=auth_dict, keepalive=60,
                                             port=1883, qos=0, tls=None)

    @mock.patch('nodepool.mqtt.PushMQTT')
    def test_get_client_with_auth_tls_and_defaults(self, PushMQTTMock):
        config = configparser.RawConfigParser()
        config.add_section('mqtt')
        config.set('mqtt', 'hostname', 'fakehost')
        config.set('mqtt', 'username', 'fakeuser')
        config.set('mqtt', 'password', 'pass')
        config.set('mqtt', 'ca_certs', '/path/to/certs')
        mqtt.get_client(config)
        auth_dict = {'username': 'fakeuser', 'password': 'pass'}
        tls_dict = {'ca_certs': '/path/to/certs', 'certfile': None,
                    'keyfile': None}
        PushMQTTMock.assert_called_once_with('fakehost', 'nodepool',
                                             auth=auth_dict, keepalive=60,
                                             port=1883, qos=0, tls=tls_dict)
