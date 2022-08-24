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

import math
import logging

from kazoo import exceptions as kze

from nodepool.zk import zookeeper as zk
from nodepool.driver import NodeRequestHandler
from nodepool.driver.utils import NodeLauncher, QuotaInformation


class K8SLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler, node, provider_config)
        self.label = provider_label
        self._retries = provider_config.launch_retries

    def _launchLabel(self):
        self.log.debug("Creating resource")
        if self.label.type == "namespace":
            resource = self.handler.manager.createNamespace(
                self.node, self.handler.pool.name)
        else:
            resource = self.handler.manager.createPod(
                self.node, self.handler.pool.name, self.label)

        self.node.state = zk.READY
        self.node.python_path = self.label.python_path
        self.node.shell_type = self.label.shell_type
        # NOTE: resource access token may be encrypted here
        self.node.connection_port = resource
        if self.label.type == "namespace":
            self.node.connection_type = "namespace"
        else:
            self.node.connection_type = "kubectl"
            self.node.interface_ip = resource['pod']
        pool = self.handler.provider.pools.get(self.node.pool)
        self.node.resources = self.handler.manager.quotaNeededByLabel(
            self.node.type[0], pool).get_resources()
        self.node.cloud = self.provider_config.context
        self.zk.storeNode(self.node)
        self.log.info("Resource %s is ready" % resource['name'])

    def launch(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchLabel()
                break
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our node lock
                # so there's no need to continue.
                raise
            except Exception:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self.node.id)
                # If we created an instance, delete it.
                if self.node.external_id:
                    self.handler.manager.cleanupNode(self.node.external_id)
                    self.handler.manager.waitForNodeCleanup(
                        self.node.external_id)
                    self.node.external_id = None
                    self.node.interface_ip = None
                    self.zk.storeNode(self.node)
                if attempts == self._retries:
                    raise
                attempts += 1


class KubernetesNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.kubernetes."
                            "KubernetesNodeRequestHandler")
    launcher = K8SLauncher

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.is_alive():
                count += 1
        return count

    def imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        :returns: True if it is available, False otherwise.
        '''
        return True

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
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
        pool_quota.subtract(needed_quota)
        return pool_quota.non_negative()

    def hasRemainingQuota(self, ntype):
        '''
        Checks if the predicted quota is enough for an additional node of type
        ntype.

        :param ntype: node type for the quota check
        :return: True if there is enough quota, False otherwise
        '''
        needed_quota = self.manager.quotaNeededByLabel(ntype, self.pool)

        # Calculate remaining quota which is calculated as:
        # quota = <total nodepool quota> - <used quota> - <quota for node>
        cloud_quota = self.manager.estimatedNodepoolQuota()
        cloud_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed())
        cloud_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining provider quota: %s",
                       cloud_quota)

        if not cloud_quota.non_negative():
            return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(
            cores=getattr(self.pool, 'max_cores', None),
            instances=self.pool.max_servers,
            ram=getattr(self.pool, 'max_ram', None),
            default=math.inf)
        pool_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed(self.pool))
        self.log.debug("Current pool quota: %s" % pool_quota)
        pool_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining pool quota: %s", pool_quota)

        return pool_quota.non_negative()

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY, FAILED
        or ABORTED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It is very important that NodeLauncher always sets one
        # of these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED, zk.ABORTED)
                   for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = self.launcher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
