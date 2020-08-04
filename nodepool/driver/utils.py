# Copyright (C) 2018 Red Hat
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

import abc
import copy
import logging
import math
import threading
import time

from kazoo import exceptions as kze

from nodepool import exceptions
from nodepool import stats
from nodepool import zk
from nodepool.logconfig import get_annotated_logger


MAX_QUOTA_AGE = 5 * 60  # How long to keep the quota information cached


class NodeLauncher(threading.Thread,
                   stats.StatsReporter,
                   metaclass=abc.ABCMeta):
    '''
    Class to launch a single node within a thread and record stats.

    At this time, the implementing class must manage this thread.
    '''

    def __init__(self, handler, node, provider_config):
        '''
        :param NodeRequestHandler handler: The handler object.
        :param Node node: A Node object describing the node to launch.
        :param ProviderConfig provider_config: A ProviderConfig object
            describing the provider launching this node.
        '''
        threading.Thread.__init__(self, name="NodeLauncher-%s" % node.id)
        stats.StatsReporter.__init__(self)
        logger = logging.getLogger("nodepool.NodeLauncher")
        request = handler.request
        self.log = get_annotated_logger(logger,
                                        event_id=request.event_id,
                                        node_request_id=request.id,
                                        node_id=node.id)
        self.handler = handler
        self.zk = handler.zk
        self.node = node
        self.provider_config = provider_config

    @abc.abstractmethod
    def launch(self):
        pass

    def run(self):
        start_time = time.monotonic()
        statsd_key = 'ready'

        try:
            self.launch()
        except kze.SessionExpiredError:
            # Our node lock is gone, leaving the node state as BUILDING.
            # This will get cleaned up in ZooKeeper automatically, but we
            # must still set our cached node state to FAILED for the
            # NodeLaunchManager's poll() method.
            self.log.error(
                "Lost ZooKeeper session trying to launch for node %s",
                self.node.id)
            self.node.state = zk.FAILED
            statsd_key = 'error.zksession'
        except exceptions.QuotaException:
            # We encountered a quota error when trying to launch a
            # node. In this case we need to abort the launch. The upper
            # layers will take care of this and reschedule a new node once
            # the quota is ok again.
            self.log.info("Aborting node %s due to quota failure" %
                          self.node.id)
            self.node.state = zk.ABORTED
            self.zk.storeNode(self.node)
            statsd_key = 'error.quota'
        except Exception as e:
            self.log.exception(
                "Launch failed for node %s:", self.node.hostname)
            self.node.state = zk.FAILED
            self.zk.storeNode(self.node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

        try:
            dt = int((time.monotonic() - start_time) * 1000)
            self.recordLaunchStats(statsd_key, dt)
        except Exception:
            self.log.exception("Exception while reporting stats:")


class QuotaInformation:

    def __init__(self, cores=None, instances=None, ram=None, default=0):
        '''
        Initializes the quota information with some values. None values will
        be initialized with default which will be typically 0 or math.inf
        indicating an infinite limit.

        :param cores:
        :param instances:
        :param ram:
        :param default:
        '''
        self.quota = {
            'compute': {
                'cores': self._get_default(cores, default),
                'instances': self._get_default(instances, default),
                'ram': self._get_default(ram, default),
            }
        }

    @staticmethod
    def construct_from_flavor(flavor):
        return QuotaInformation(instances=1,
                                cores=flavor.vcpus,
                                ram=flavor.ram)

    @staticmethod
    def construct_from_limits(limits):
        def bound_value(value):
            if value == -1:
                return math.inf
            return value

        return QuotaInformation(
            instances=bound_value(limits.max_total_instances),
            cores=bound_value(limits.max_total_cores),
            ram=bound_value(limits.max_total_ram_size))

    def _get_default(self, value, default):
        return value if value is not None else default

    def _add_subtract(self, other, add=True):
        for category in self.quota.keys():
            for resource in self.quota[category].keys():
                second_value = other.quota.get(category, {}).get(resource, 0)
                if add:
                    self.quota[category][resource] += second_value
                else:
                    self.quota[category][resource] -= second_value

    def subtract(self, other):
        self._add_subtract(other, add=False)

    def add(self, other):
        self._add_subtract(other, True)

    def non_negative(self):
        for key_i, category in self.quota.items():
            for resource, value in category.items():
                if value < 0:
                    return False
        return True

    def __str__(self):
        return str(self.quota)


class QuotaSupport:
    """A mix-in class for providers to supply quota support methods"""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._current_nodepool_quota = None

    @abc.abstractmethod
    def quotaNeededByLabel(self, label, pool):
        """Return quota information about a label

        :param str label: The label name
        :param ProviderPool pool: A ProviderPool config object with the label

        :return: QuotaInformation about the label
        """
        pass

    @abc.abstractmethod
    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        pass

    @abc.abstractmethod
    def getProviderLimits(self):
        '''
        Get the resource limits from the provider.

        :return: QuotaInformation about the label
        '''
        pass

    def invalidateQuotaCache(self):
        self._current_nodepool_quota['timestamp'] = 0

    def estimatedNodepoolQuota(self):
        '''
        Determine how much quota is available for nodepool managed resources.
        This needs to take into account the quota of the tenant, resources
        used outside of nodepool and the currently used resources by nodepool,
        max settings in nodepool config. This is cached for MAX_QUOTA_AGE
        seconds.

        :return: Total amount of resources available which is currently
                 available to nodepool including currently existing nodes.
        '''

        if self._current_nodepool_quota:
            now = time.time()
            if now < self._current_nodepool_quota['timestamp'] + MAX_QUOTA_AGE:
                return copy.deepcopy(self._current_nodepool_quota['quota'])

        # This is initialized with the full tenant quota and later becomes
        # the quota available for nodepool.
        nodepool_quota = self.getProviderLimits()

        self.log.debug("Provider quota for %s: %s",
                       self.provider.name, nodepool_quota)

        # Subtract the unmanaged quota usage from nodepool_max
        # to get the quota available for us.
        nodepool_quota.subtract(self.unmanagedQuotaUsed())

        self._current_nodepool_quota = {
            'quota': nodepool_quota,
            'timestamp': time.time()
        }

        self.log.debug("Available quota for %s: %s",
                       self.provider.name, nodepool_quota)

        return copy.deepcopy(nodepool_quota)

    def estimatedNodepoolQuotaUsed(self, pool=None):
        '''
        Sums up the quota used (or planned) currently by nodepool. If pool is
        given it is filtered by the pool.

        :param pool: If given, filtered by the pool.
        :return: Calculated quota in use by nodepool
        '''
        used_quota = QuotaInformation()

        for node in self._zk.nodeIterator():
            if node.provider == self.provider.name:
                try:
                    if pool and not node.pool == pool.name:
                        continue
                    provider_pool = self.provider.pools.get(node.pool)
                    if not provider_pool:
                        self.log.warning(
                            "Cannot find provider pool for node %s" % node)
                        # This node is in a funny state we log it for debugging
                        # but move on and don't account it as we can't properly
                        # calculate its cost without pool info.
                        continue
                    if node.type[0] not in provider_pool.labels:
                        self.log.warning("Node type is not in provider pool "
                                         "for node %s" % node)
                        # This node is also in a funny state; the config
                        # may have changed under it.  It should settle out
                        # eventually when it's deleted.
                        continue
                    node_resources = self.quotaNeededByLabel(
                        node.type[0], provider_pool)
                    used_quota.add(node_resources)
                except Exception:
                    self.log.exception("Couldn't consider invalid node %s "
                                       "for quota:" % node)
        return used_quota
