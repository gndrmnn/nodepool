# Copyright 2017 IBM Corp.
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

"""
Helper to create a mqtt client
"""

import paho.mqtt.publish as publish


class PushMQTT(object):
    def __init__(self, hostname, base_topic, port=1883, client_id=None,
                 keepalive=60, will=None, auth=None, tls=None, qos=0):
        self.hostname = hostname
        self.base_topic = base_topic
        self.port = port
        self.client_id = client_id
        self.keepalive = 60
        self.will = will
        self.auth = auth
        self.tls = tls
        self.qos = qos

    def _generate_topic(self, topic):
        return '/'.join([self.base_topic, topic])

    def publish_single(self, topic, msg):
        full_topic = self._generate_topic(topic)
        publish.single(full_topic, msg, hostname=self.hostname,
                       port=self.port, client_id=self.client_id,
                       keepalive=self.keepalive, will=self.will,
                       auth=self.auth, tls=self.tls, qos=self.qos)

    def publish_multiple(self, topic, msg):
        full_topic = self._generate_topic(topic)
        publish.multiple(full_topic, msg, hostname=self.hostname,
                         port=self.port, client_id=self.client_id,
                         keepalive=self.keepalive, will=self.will,
                         auth=self.auth, tls=self.tls, qos=self.qos)


def get_client(config):
    """Return a PushMQTT object setup from the ConfigParser Namespace object
    passed in. None is returned if there is no MQTT config or an incomplete
    config.
    """
    if not config.has_section('mqtt'):
        return None

    if not config.has_option('mqtt', 'hostname'):
        return None

    mqtt_hostname = config.get('mqtt', 'hostname')
    # Basic settings
    base_topic = 'nodepool'
    if config.has_option('mqtt', 'base_topic'):
        base_topic = config.get('mqtt', 'base_topic')
    mqtt_port = 1883
    if config.has_option('mqtt', 'port'):
        mqtt_port = config.get('mqtt', 'port')
    keepalive = 60
    if config.has_option('mqtt', 'keepalive'):
        keepalive = config.get('mqtt', 'keepalive')
    client_id = None
    if config.has_option('mqtt', 'client_id'):
        client_id = config.get('mqtt', 'client_id')

    # Configure auth
    auth = None
    mqtt_username = None
    if config.has_option('mqtt', 'username'):
        mqtt_username = config.get('mqtt', 'username')
    mqtt_password = None
    if config.has_option('mqtt', 'password'):
        mqtt_password = config.get('mqtt', 'password')
    if mqtt_username:
        auth = {'username': mqtt_username}
        if mqtt_password:
            auth['password'] = mqtt_password

    # TLS settings
    ca_certs = None
    if config.has_option('mqtt', 'ca_certs'):
        ca_certs = config.get('mqtt', 'ca_certs')
    certfile = None
    if config.has_option('mqtt', 'certfile'):
        ca_certs = config.get('mqtt', 'certfile')
    keyfile = None
    if config.has_option('mqtt', 'keyfile'):
        ca_certs = config.get('mqtt', 'keyfile')
    tls = None
    if ca_certs is not None:
        tls = {'ca_certs': ca_certs, 'certfile': certfile,
               'keyfile': keyfile}


    # QOS settings
    if config.has_option('mqtt', 'qos'):
        mqtt_qos = config.getint('mqtt', 'qos')
    else:
        mqtt_qos = 0

    return PushMQTT(mqtt_hostname, base_topic, port=mqtt_port,
                    client_id=client_id, keepalive=keepalive, auth=auth,
                    tls=tls, qos=mqtt_qos)
