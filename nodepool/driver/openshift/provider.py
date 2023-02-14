# Copyright 2018 Red Hat
# Copyright 2023 Acme Gating, LLC
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
import math
import urllib3
import time

from openshift.dynamic import DynamicClient

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.utils import NodeDeleter
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.driver.openshift import handler
from nodepool.driver.utils_k8s import get_client

urllib3.disable_warnings()


class OpenshiftProvider(Provider, QuotaSupport):
    log = logging.getLogger("nodepool.driver.openshift.OpenshiftProvider")

    def __init__(self, provider, *args, _skip_init=False):
        super().__init__()
        self.provider = provider
        self.ready = False
        if not _skip_init:
            # The OpenshiftPods driver subclasses this but doesn't
            # want this initialization.  TODO: unify the two.
            _, _, self.k8s_client, self.os_client = get_client(
                self.log, provider.context, DynamicClient)
            self.project_names = set()
            for pool in provider.pools.values():
                self.project_names.add(pool.name)

    def start(self, zk_conn):
        self.log.debug("Starting")
        self._zk = zk_conn
        if self.ready or not self.os_client or not self.k8s_client:
            return
        self.ready = True

    def stop(self):
        self.log.debug("Stopping")

    def idle(self):
        pass

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

        if self.ready:
            projects = self.os_client.resources.get(api_version='v1',
                                                    kind='Project')
            for project in projects.get().items:
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

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        self.log.debug("%s: removing project" % server_id)
        try:
            project = self.os_client.resources.get(api_version='v1',
                                                   kind='Project')
            project.delete(name=server_id)
            self.log.info("%s: project removed" % server_id)
        except Exception:
            # TODO: implement better exception handling
            self.log.exception("Couldn't remove project %s" % server_id)

    def waitForNodeCleanup(self, server_id):
        project = self.os_client.resources.get(api_version='v1',
                                               kind='Project')
        for retry in range(300):
            try:
                project.get(name=server_id)
            except Exception:
                break
            time.sleep(1)

    def createProject(self, node, pool, project, label):
        self.log.debug("%s: creating project" % project)
        # Create the project

        k8s_labels = {}
        if label.labels:
            k8s_labels.update(label.labels)
        k8s_labels.update({
            'nodepool_node_id': node.id,
            'nodepool_provider_name': self.provider.name,
            'nodepool_pool_name': pool,
            'nodepool_node_label': label.name,
        })

        proj_body = {
            'apiVersion': 'project.openshift.io/v1',
            'kind': 'ProjectRequest',
            'metadata': {
                'name': project,
                'labels': k8s_labels,
            }
        }
        projects = self.os_client.resources.get(
            api_version='project.openshift.io/v1', kind='ProjectRequest')
        projects.create(body=proj_body)
        return project

    def prepareProject(self, project):
        user = "zuul-worker"

        # Create the service account
        sa_body = {
            'apiVersion': 'v1',
            'kind': 'ServiceAccount',
            'metadata': {'name': user}
        }
        self.k8s_client.create_namespaced_service_account(project, sa_body)

        # Wait for the token to be created
        for retry in range(30):
            sa = self.k8s_client.read_namespaced_service_account(
                user, project)
            ca_crt = None
            token = None
            if sa.secrets:
                for secret_obj in sa.secrets:
                    secret = self.k8s_client.read_namespaced_secret(
                        secret_obj.name, project)
                    token = secret.data.get('token')
                    ca_crt = secret.data.get('ca.crt')
                    if token and ca_crt:
                        token = base64.b64decode(
                            token.encode('utf-8')).decode('utf-8')
                        break
            if token:
                break
            time.sleep(1)
        if not token or not ca_crt:
            raise exceptions.LaunchNodepoolException(
                "%s: couldn't find token for service account %s" %
                (project, sa))

        # Give service account admin access
        role_binding_body = {
            'apiVersion': 'authorization.openshift.io/v1',
            'kind': 'RoleBinding',
            'metadata': {'name': 'admin-0'},
            'roleRef': {'name': 'admin'},
            'subjects': [{
                'kind': 'ServiceAccount',
                'name': user,
                'namespace': project,
            }],
            'userNames': ['system:serviceaccount:%s:%s' % (project, user)]
        }
        try:
            role_bindings = self.os_client.resources.get(
                api_version='authorization.openshift.io/v1',
                kind='RoleBinding')
            role_bindings.create(body=role_binding_body, namespace=project)
        except ValueError:
            # https://github.com/ansible/ansible/issues/36939
            pass

        resource = {
            'namespace': project,
            'host': self.os_client.configuration.host,
            'skiptls': not self.os_client.configuration.verify_ssl,
            'token': token,
            'user': user,
        }

        if not resource['skiptls']:
            resource['ca_crt'] = ca_crt

        self.log.info("%s: project created" % project)
        return resource

    def createPod(self, node, pool, project, pod_name, label):
        self.log.debug("%s: creating pod in project %s" % (pod_name, project))
        container_body = {
            'name': label.name,
            'image': label.image,
            'imagePullPolicy': label.image_pull,
            'command': ["/bin/sh", "-c"],
            'args': ["while true; do sleep 30; done;"],
            'env': label.env,
        }

        requests = {}
        limits = {}
        if label.cpu:
            requests['cpu'] = int(label.cpu)
        if label.memory:
            requests['memory'] = '%dMi' % int(label.memory)
        if label.storage:
            requests['ephemeral-storage'] = '%dM' % int(label.storage)
        if label.cpu_limit:
            limits['cpu'] = int(label.cpu_limit)
        if label.memory_limit:
            limits['memory'] = '%dMi' % int(label.memory_limit)
        if label.storage_limit:
            limits['ephemeral-storage'] = '%dM' % int(label.storage_limit)
        if label.gpu_resource and label.gpu:
            requests[label.gpu_resource] = '%.2f' % label.gpu
            limits[label.gpu_resource] = '%.2f' % label.gpu
        resources = {}
        if requests:
            resources['requests'] = requests
        if limits:
            resources['limits'] = limits
        if resources:
            container_body['resources'] = resources

        spec_body = {
            'containers': [container_body],
            'imagePullSecrets': label.image_pull_secrets,
        }

        if label.node_selector:
            spec_body['nodeSelector'] = label.node_selector

        if label.scheduler_name:
            spec_body['schedulerName'] = label.scheduler_name

        if label.volumes:
            spec_body['volumes'] = label.volumes

        if label.volume_mounts:
            container_body['volumeMounts'] = label.volume_mounts

        if label.privileged is not None:
            container_body['securityContext'] = {
                'privileged': label.privileged,
            }

        k8s_labels = {}
        if label.labels:
            k8s_labels.update(label.labels)
        k8s_labels.update({
            'nodepool_node_id': node.id,
            'nodepool_provider_name': self.provider.name,
            'nodepool_pool_name': pool,
            'nodepool_node_label': label.name,
        })

        k8s_annotations = {}
        if label.annotations:
            k8s_annotations.update(label.annotations)

        pod_body = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                'name': pod_name,
                'labels': k8s_labels,
                'annotations': k8s_annotations,
            },
            'spec': spec_body,
            'restartPolicy': 'Never',
        }

        self.k8s_client.create_namespaced_pod(project, pod_body)

    def waitForPod(self, project, pod_name):
        for retry in range(300):
            pod = self.k8s_client.read_namespaced_pod(pod_name, project)
            if pod.status.phase == "Running":
                break
            self.log.debug("%s: pod status is %s", project, pod.status.phase)
            time.sleep(1)
        if retry == 299:
            raise exceptions.LaunchNodepoolException(
                "%s: pod failed to initialize (%s)" % (
                    project, pod.status.phase))
        return pod.spec.node_name

    def getRequestHandler(self, poolworker, request):
        return handler.OpenshiftNodeRequestHandler(poolworker, request)

    def getProviderLimits(self):
        # TODO: query the api to get real limits
        return QuotaInformation(
            cores=math.inf,
            instances=math.inf,
            ram=math.inf,
            default=math.inf)

    def quotaNeededByLabel(self, ntype, pool):
        provider_label = pool.labels[ntype]
        resources = {}
        if provider_label.cpu:
            resources["cores"] = provider_label.cpu
        if provider_label.memory:
            resources["ram"] = provider_label.memory
        if provider_label.storage:
            resources["ephemeral-storage"] = provider_label.storage
        if provider_label.gpu and provider_label.gpu_resource:
            resources[provider_label.gpu_resource] = provider_label.gpu
        resources.update(provider_label.extra_resources)
        return QuotaInformation(instances=1, **resources)

    def unmanagedQuotaUsed(self):
        # TODO: return real quota information about quota
        return QuotaInformation()
