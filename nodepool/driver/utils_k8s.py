# Copyright (C) 2021 Red Hat
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import base64

from google.auth.exceptions import DefaultCredentialsError
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config


def _get_conf(log, context):
    try:
        return k8s_config.new_client_from_config(context=context)
    except FileNotFoundError:
        log.debug("Kubernetes config file not found, attempting "
                  "to load in-cluster configs")
        return k8s_config.load_incluster_config()
    except k8s_config.config_exception.ConfigException as e:
        if 'Invalid kube-config file. No configuration found.' in str(e):
            log.debug("Kubernetes config file not found, attempting "
                      "to load in-cluster configs")
            return k8s_config.load_incluster_config()
        else:
            raise


def get_client(log, context, extra_client_constructor=None):
    token, ca, client, extra_client = None, None, None, None
    try:
        conf = _get_conf(log, context)
        if conf:
            auth = conf.configuration.api_key.get('authorization')
            if auth:
                token = auth.split()[-1]
        if conf and conf.configuration.ssl_ca_cert:
            with open(conf.configuration.ssl_ca_cert) as ca_file:
                ca = ca_file.read()
                ca = base64.b64encode(ca.encode('utf-8')).decode('utf-8')
        client = k8s_client.CoreV1Api(conf)
        if extra_client_constructor:
            extra_client = extra_client_constructor(conf)
    except DefaultCredentialsError as e:
        log.error("Invalid kubernetes configuration: %s", e)
    except k8s_config.config_exception.ConfigException:
        log.exception(
            "Couldn't load context %s from config", context)
    return (token, ca, client, extra_client)
