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

from openshift import client, config

from nodepool.driver import Provider


class OpenshiftProvider(Provider):
    log = logging.getLogger("nodepool.driver.openshift.OpenshiftProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        self.projects = {}
        config.load_kube_config(config_file=provider.config_file)
        self.client = client.OapiApi()

    # Nodepool provider interface
    def start(self):
        self.log.debug("Starting")
        if self.ready:
            return
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []

        class FakeServer:
            def __init__(self, project, provider):
                self.id = project.metadata.name
                self.name = project.metadata.name
                try:
                    self.metadata = {
                        'nodepool_provider_name': provider.name,
                        'nodepool_node_id': project.metadata.nodepool_node_id,
                    }
                except Exception:
                    pass

            def get(self, name, default=None):
                return getattr(self, name, default)

        for project in self.client.list_project().items:
            servers.append(FakeServer(project, self.provider))
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
        self.log.info("Removing project %s" % server_id)
        try:
            self.client.delete_project(server_id)
        except Exception:
            # TODO: implement better exception handling
            pass

    def waitForNodeCleanup(self, server_id):
        pass

    def createProject(self, name, pool):
        namespace = "zuul-ci-%s" % name
        body = {
            'kind': 'ProjectRequest',
            'apiVersion': 'v1',
            'metadata': {
                'name': namespace,
                'nodepool_node_id': name
            }
        }
        proj = self.client.create_project_request(body)

        # TODO: replace this method with proper python sdk api call
        def cliApi(cmd):
            import subprocess
            ocProc = subprocess.Popen(
                ["oc", "--namespace", namespace] + cmd,
                stdout=subprocess.PIPE)
            ocProc.wait()
            ret = ocProc.stdout.read().decode('utf-8').replace("'", "")
            return ret

        cliApi(["create", "serviceaccount", "zuul-worker"])
        cliApi(["policy", "add-role-to-user", "admin",
                "system:serviceaccount:zuul-ci-%s:zuul-worker" % name])
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
        except TypeError:
            # When token is JWT, encode in base64
            token_data = base64.b64encode(token_data)

        proj.resource = {
            'namespace': namespace,
            'host': self.client.api_client.configuration.host,
            'skiptls': not self.client.api_client.configuration.verify_ssl,
            'token': token_data,
        }
        return proj
