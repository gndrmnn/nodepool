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

import logging
import math

from nodepool.zk import zookeeper as zk

from nodepool.driver.openshift.handler import OpenshiftLauncher
from nodepool.driver.openshift.handler import OpenshiftNodeRequestHandler
from nodepool.driver.utils import QuotaInformation


class OpenshiftPodLauncher(OpenshiftLauncher):
    def _launchLabel(self):
        self.log.debug("Creating resource")
        pod_name = "%s-%s" % (self.label.name, self.node.id)
        project = self.handler.pool.name
        self.handler.manager.createPod(project, pod_name, self.label)
        self.node.external_id = "%s-%s" % (project, pod_name)
        self.node.interface_ip = pod_name
        self.zk.storeNode(self.node)

        pod_node_id = self.handler.manager.waitForPod(project, pod_name)

        self.node.state = zk.READY
        self.node.python_path = self.label.python_path
        self.node.shell_type = self.label.shell_type
        # NOTE: resource access token may be encrypted here
        k8s = self.handler.manager.k8s_client
        self.node.connection_port = {
            'pod': pod_name,
            'namespace': project,
            'host': k8s.api_client.configuration.host,
            'ca_crt': self.handler.manager.ca_crt,
            'skiptls': not k8s.api_client.configuration.verify_ssl,
            'token': self.handler.manager.token,
            'user': 'zuul-worker',
        }
        self.node.connection_type = "kubectl"
        pool = self.handler.provider.pools.get(self.node.pool)
        self.node.resources = self.handler.manager.quotaNeededByLabel(
            self.node.type[0], pool).get_resources()
        self.node.cloud = self.provider_config.context
        self.node.host_id = pod_node_id
        self.zk.storeNode(self.node)
        self.log.info("Pod %s is ready" % self.node.external_id)


class OpenshiftPodRequestHandler(OpenshiftNodeRequestHandler):
    log = logging.getLogger("nodepool.driver.openshiftpods."
                            "OpenshiftPodRequestHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)

    def hasProviderQuota(self, node_types):
        '''
        Checks if a provider has enough quota to handle a list of nodes.
        This does not take our currently existing nodes into account.

        :param node_types: list of node types to check
        :return: True if the node list fits into the provider, False otherwise
        '''
        needed_quota = QuotaInformation()

        for ntype in node_types:
            needed_quota.add(
                self.manager.quotaNeededByLabel(ntype, self.pool))

        if hasattr(self.pool, 'ignore_provider_quota'):
            if not self.pool.ignore_provider_quota:
                cloud_quota = self.manager.estimatedNodepoolQuota()
                cloud_quota.subtract(needed_quota)

                if not cloud_quota.non_negative():
                    return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(
            cores=getattr(self.provider, 'max_cores', None),
            instances=self.pool.max_pods,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
        pool_quota.subtract(needed_quota)
        self.log.info("Provider Quota: %s", pool_quota)
        return pool_quota.non_negative()

    def hasRemainingQuota(self, node_types):
        '''
        Checks if the predicted quota is enough for an additional node of type
        ntype.

        :param ntype: node type for the quota check
        :return: True if there is enough quota, False otherwise
        '''
        needed_quota = self.manager.quotaNeededByLabel(node_types, self.pool)

        # Calculate remaining quota which is calculated as:
        # quota = <total nodepool quota> - <used quota> - <quota for node>
        cloud_quota = self.manager.estimatedNodepoolQuota()
        cloud_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed())
        cloud_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining provider quota: %s", cloud_quota)

        if not cloud_quota.non_negative():
            return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(
            cores=getattr(self.provider, 'max_cores', None),
            instances=self.pool.max_pods,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
        pool_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed(self.pool))
        self.log.debug("Current pool quota: %s" % pool_quota)
        pool_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining pool quota: %s", pool_quota)

        return pool_quota.non_negative()

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = OpenshiftPodLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
