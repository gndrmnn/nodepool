# Copyright 2018 Red Hat
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

import base64
import logging

from kubernetes.config import config_exception as kce
from openshift import config
from openshift import client

from nodepool.driver import Provider
from nodepool.driver.openshift import handler


class OpenshiftProvider(Provider):
    log = logging.getLogger("nodepool.driver.openshift.OpenshiftProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        try:
            self.client = client.OapiApi(
                config.new_client_from_config(context=provider.context))
        except kce.ConfigException:
            self.log.exception("Couldn't load client from config")
            self.log.info("Get context list using this command: "
                          "python3 -c \"from openshift import config; "
                          "print('\\n'.join([i['name'] for i in "
                          "config.list_kube_config_contexts()[0]]))\"")
            self.client = None
        self.project_names = set()
        for pool in provider.pools.values():
            self.project_names.add(pool.name)

    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.ready or not self.client:
            return
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, project, provider, valid_names):
                self.id = project.metadata.name
                self.name = project.metadata.name
                self.metadata = {}

                if [True for valid_name in valid_names
                    if project.metadata.name.startswith("%s-" % valid_name)]:
                    node_id = project.metadata.name.split('-')[-1]
                    try:
                        # Make sure last component of name is an id
                        int(node_id)
                        self.metadata['nodepool_provider_name'] = provider
                        self.metadata['nodepool_node_id'] = node_id
                    except Exception:
                        # Probably not a managed project, let's skip metadata
                        pass

            def get(self, name, default=None):
                return getattr(self, name, default)

        for project in self.client.list_project().items:
            servers.append(FakeServer(
                project, self.provider.name, self.project_names))
        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        pass

    def cleanupLeakedResources(self):
        pass

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        self.log.debug("%s: removing project" % server_id)
        try:
            self.client.delete_project(server_id)
            self.log.info("%s: project removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove project %s" % server_id)

    def waitForNodeCleanup(self, server_id):
        pass

    def createProject(self, name, pool):
        namespace = "%s-%s" % (pool, name)
        self.log.debug("%s: creating project" % namespace)
        body = {
            'kind': 'ProjectRequest',
            'apiVersion': 'v1',
            'metadata': {
                'name': namespace,
                'nodepool_node_id': name
            }
        }
        proj = self.client.create_project_request(body)
        self.log.debug("%s: namespace created" % namespace)

        # TODO: replace this method with proper python sdk api call
        def cliApi(cmd):
            import subprocess
            ocProc = subprocess.Popen(
                ["oc", "--context", self.provider.context,
                 "--namespace", namespace] + cmd,
                stdout=subprocess.PIPE)
            ocProc.wait()
            ret = ocProc.stdout.read().decode('utf-8').replace("'", "")
            return ret

        cliApi(["create", "serviceaccount", "zuul-worker"])
        cliApi(["policy", "add-role-to-user", "admin",
                "system:serviceaccount:%s:zuul-worker" % namespace])
        token_name = cliApi(["get", "serviceaccount", "zuul-worker",
                             "-o", "jsonpath='{.secrets[0].name}'"])
        token_data = cliApi(["get", "secret", token_name,
                             "-o", "jsonpath='{.data.token}'"])
        if not token_data:
            # try another jsonpath
            token_data = cliApi(
                ["get", "secret", token_name, "-o",
                 "jsonpath='{.metadata.annotations.openshift\.io/"
                 "token-secret\.value}'"])

        try:
            base64.urlsafe_b64decode(token_data)
        except Exception:
            # When token is JWT, encode in base64
            token_data = base64.b64encode(
                token_data.encode('utf-8')).decode('utf-8')

        proj.resource = {
            'namespace': namespace,
            'host': self.client.api_client.configuration.host,
            'skiptls': not self.client.api_client.configuration.verify_ssl,
            'token': token_data,
        }
        self.log.info("%s: project created" % namespace)
        return proj

    def getRequestHandler(self, poolworker, request):
        return handler.OpenshiftNodeRequestHandler(poolworker, request)
