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
import time
import re

from google.auth.exceptions import DefaultCredentialsError
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

from nodepool import stats


REST_PATH_RE = re.compile(
    r"^/(?P<service_type>api[s]?(/[^/]+)*/v[0-9]+[a-z0-9]*)/(?P<ops>.+)"
)

KUBERNETES_STATSD_PREFIX = 'nodepool.kubernetes'


def createApiClientClassWithStatsD(klass):

    class newClass(klass):
        def __init__(self, provider_name, log, configuration=None, header_name=None,
                    header_value=None, cookie=None, pool_threads=1):
            super(ApiClientWithStatsD, self).__init__(
                configuration, header_name, header_value,
                cookie, pool_threads)
            self._statsd = stats.get_client()
            self.provider_name = provider_name
            self.log = log

        def resourcePathToMetricName(self, resource_path, method):
            key = '%s.%s' % (KUBERNETES_STATSD_PREFIX, self.provider_name)
            matches = REST_PATH_RE.match(resource_path)
            if not matches:
                self.log.error(
                    "Could not map statsd metric name to API path: %s"
                    % resource_path)
                return key + '.' + resource_path.replace('/', '_') + '.' + method
            mdict = matches.groupdict()
            if mdict['service_type'] == 'api/v1':
                key += '.coreV1'
            elif mdict['service_type'] == ('/apis/rbac.authorization.k8s.io'
                                                '/v1'):
                key += '.rbacAuthorizationV1'
            else:
                # TODO cover more known APIs
                key += '.' + mdict['service_type'].split('/')[1].replace('.', '_')
            key += '.%s' % method
            ops = mdict['ops'].split('/')
            namespaced = len(ops) > 2 and ops[0] == 'namespaces'
            operation = ''
            for op in ops:
                if namespaced and op == 'namespaces':
                    operation += 'namespaced_'
                elif not op.startswith("{"):
                    operation += op + '_'
            if ops[-1][1:-1] == "path":
                operation += 'with_path'
            else:
                operation = operation[:-1]
            key += '.%s' % operation
            return key

        def emitStatsDMetrics(self, resource_path, method,
                            status=None, duration=None, exc=None):
            if self._statsd:
                with self._statsd.pipeline() as pipeline:
                    metric_name = self.resourcePathToMetricName(
                        resource_path, method)
                    if exc:
                        pipeline.incr(metric_name + '.failed')
                    if status:
                        key = (metric_name + '.%i' % status)
                        pipeline.timing(key, duration)
                        pipeline.incr(key)
                    pipeline.incr(metric_name + '.attempted')

        def __call_api(self, resource_path, method, path_params=None,
                    query_params=None, header_params=None, body=None,
                    post_params=None, files=None, response_type=None,
                    auth_settings=None, _return_http_data_only=None,
                    collection_formats=None, _preload_content=True,
                    _request_timeout=None, _host=None):
            try:
                call_start = time.time()
                return_data, status, headers = super().__call_api(
                    resource_path, method, path_params, query_params,
                    header_params, body, post_params, files, response_type,
                    auth_settings, False, collection_formats, _preload_content,
                    _request_timeout, _host)
                call_end = time.time()
                duration = int((call_end - call_start) * 1000)
                self.emitStatsDMetrics(resource_path, method, status, duration)
            except Exception as exc:
                self.emitStatsDMetrics(resource_path, exc=exc)
                raise
            if _return_http_data_only:
                return (return_data)
            else:
                return (return_data, status, headers)

    return newClass


ApiClientWithStatsD = createApiClientClassWithStatsD(k8s_client.ApiClient)

class CoreV1ApiWithStatsD(k8s_client.CoreV1Api):
    def __init__(self, provider_name, log, api_client=None):
        _api_client = ApiClientWithStatsD(provider_name, log)
        if api_client is not None:
            header_name = None
            header_value = None
            if api_client.default_headers:
                header_name = api_client.default_headers.keys()[0]
                header_value = api_client.default_headers[header_name]
            _api_client = ApiClientWithStatsD(
                provider_name, log,
                configuration=api_client.configuration,
                header_name=header_name, header_value=header_value,
                cookie=api_client.cookie,
                pool_threads=api_client.pool_threads,
            )
        super(CoreV1ApiWithStatsD, self).__init__(_api_client)


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


def get_client(log, provider_name, context, extra_client_constructor=None):
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
        client = CoreV1ApiWithStatsD(provider_name, log, conf)
        if extra_client_constructor:
            extra_client = extra_client_constructor(conf)
    except DefaultCredentialsError as e:
        log.error("Invalid kubernetes configuration: %s", e)
    except k8s_config.config_exception.ConfigException:
        log.exception(
            "Couldn't load context %s from config", context)
    return (token, ca, client, extra_client)
