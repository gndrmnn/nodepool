# Copyright (C) 2011-2014 OpenStack Foundation
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

import contextlib
import logging
import math
import os
import os.path
import socket
import threading
import time
import uuid

from kazoo import exceptions as kze

from nodepool import exceptions
from nodepool import provider_manager
from nodepool import stats
from nodepool import config as nodepool_config
from nodepool import zk
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.logconfig import get_annotated_logger
from nodepool.version import version_info as npd_version_info


MINS = 60
HOURS = 60 * MINS

# Interval between checking if new servers needed
WATERMARK_SLEEP = 10

# When to delete node request lock znodes
LOCK_CLEANUP = 8 * HOURS

# How long to wait between checks for ZooKeeper connectivity if it disappears.
SUSPEND_WAIT_TIME = 30


class PoolWorker(threading.Thread, stats.StatsReporter):
    '''
    Class that manages node requests for a single provider pool.

    The NodePool thread will instantiate a class of this type for each
    provider pool found in the nodepool configuration file. If the
    pool or provider to which this thread is assigned is removed from
    the configuration file, then that will be recognized and this
    thread will shut itself down.
    '''

    def __init__(self, nodepool, provider_name, pool_name):
        threading.Thread.__init__(
            self, name='PoolWorker.%s-%s' % (provider_name, pool_name)
        )
        self.log = logging.getLogger("nodepool.%s" % self.name)
        self.nodepool = nodepool
        self.provider_name = provider_name
        self.pool_name = pool_name
        self.running = False
        self.stop_event = threading.Event()
        self.paused_handler = None
        self.request_handlers = []
        self.watermark_sleep = nodepool.watermark_sleep
        self.zk = self.getZK()
        self.launcher_id = "%s-%s-%s" % (socket.getfqdn(),
                                         self.name,
                                         uuid.uuid4().hex)
        stats.StatsReporter.__init__(self)

    # ---------------------------------------------------------------
    # Private methods
    # ---------------------------------------------------------------

    def _assignHandlers(self, timeout=15):
        '''
        For each request we can grab, create a NodeRequestHandler for it.

        The NodeRequestHandler object will kick off any threads needed to
        satisfy the request, then return. We will need to periodically poll
        the handler for completion.

        If exceeds the timeout it stops further iteration and returns False
        in order to give us time to call _removeCompletedHandlers. Otherwise
        it returns True to signal that it is finished for now.
        '''
        start = time.monotonic()
        provider = self.getProviderConfig()
        if not provider:
            self.log.info("Missing config. Deleted provider?")
            return True

        if provider.max_concurrency == 0:
            return True

        # Get the launchers which are currently online.  This may
        # become out of date as the loop progresses, but it should be
        # good enough to determine whether we should process requests
        # which express a preference for a specific provider.
        launchers = self.zk.getRegisteredLaunchers()

        pm = self.getProviderManager()
        has_quota_support = isinstance(pm, QuotaSupport)
        if has_quota_support:
            # The label quota limits will be used for the whole loop since we
            # don't want to accept lower priority requests when a label becomes
            # available after we've already deferred higher priority requests.
            label_quota = pm.getLabelQuota()

        pool = self.getPoolConfig()
        pool_labels = set(pool.labels)

        def _sort_key(request):
            missing_labels = set(request.node_types) - pool_labels
            # Return a tuple with the number of labels that are *not* served by
            # this pool and the request priority. This will sort the requests
            # that we can serve (no missing labels) before the requests that we
            # need to decline (missing labels > 0).
            return len(missing_labels), request.priority

        requests = sorted(self.zk.nodeRequestIterator(), key=_sort_key)
        for req in requests:
            if not self.running:
                return True

            if self.paused_handler:
                self.log.debug("Handler is now paused")
                return True

            # Get active threads for all pools for this provider
            active_threads = sum([
                w.activeThreads() for
                w in self.nodepool.getPoolWorkers(self.provider_name)
            ])

            # Short-circuit for limited request handling
            if (provider.max_concurrency > 0 and
                    active_threads >= provider.max_concurrency):
                self.log.debug("Request handling limited: %s active threads ",
                               "with max concurrency of %s",
                               active_threads, provider.max_concurrency)
                return True

            req = self.zk.getNodeRequest(req.id)
            if not req:
                continue

            # Only interested in unhandled requests
            if req.state != zk.REQUESTED:
                continue

            # Skip it if we've already declined
            if self.launcher_id in req.declined_by:
                continue

            log = get_annotated_logger(self.log, event_id=req.event_id,
                                       node_request_id=req.id)
            # Skip this request if it is requesting another provider
            # which is online
            if req.provider and req.provider != self.provider_name:
                # The request is asking for a specific provider
                candidate_launchers = set(
                    x.id for x in launchers
                    if x.provider_name == req.provider
                    and x.supported_labels.issuperset(req.node_types))
                if candidate_launchers:
                    # There is a launcher online which can satisfy the request
                    if not candidate_launchers.issubset(set(req.declined_by)):
                        # It has not yet declined the request, so yield to it.
                        log.debug("Yielding request to provider %s %s",
                                  req.provider, candidate_launchers)
                        continue

            if has_quota_support and not all(label_quota.get(l, math.inf) > 0
                                             for l in req.node_types):
                # Defer the request as we can't provide the required labels at
                # the moment.
                log.debug("Deferring request because labels are unavailable")
                continue

            # check tenant quota if the request has a tenant associated
            # and there are resource limits configured for this tenant
            check_tenant_quota = req.tenant_name and req.tenant_name \
                in self.nodepool.config.tenant_resource_limits \
                and has_quota_support

            if check_tenant_quota and not self._hasTenantQuota(req, pm):
                # Defer request for it to be handled and fulfilled at a later
                # run.
                log.debug(
                    "Deferring request because it would exceed tenant quota")
                continue

            log.debug("Locking request")
            try:
                self.zk.lockNodeRequest(req, blocking=False)
            except exceptions.ZKLockException:
                log.debug("Request is locked by someone else")
                continue

            # Make sure the state didn't change on us after getting the lock
            if req.state != zk.REQUESTED:
                self.zk.unlockNodeRequest(req)
                log.debug("Request is in state %s", req.state)
                continue

            # Got a lock, so assign it
            log.info("Assigning node request %s" % req)

            if has_quota_support:
                # Adjust the label quota so we don't accept more requests
                # than we have labels available.
                for label in req.node_types:
                    with contextlib.suppress(KeyError):
                        label_quota[label] -= 1

            rh = pm.getRequestHandler(self, req)
            rh.run()
            if rh.paused:
                self.paused_handler = rh
            self.request_handlers.append(rh)

            # if we exceeded the timeout stop iterating here
            if time.monotonic() - start > timeout:
                return False
        return True

    def _removeCompletedHandlers(self):
        '''
        Poll handlers to see which have completed.
        '''
        active_handlers = []
        for r in self.request_handlers:
            log = get_annotated_logger(self.log, event_id=r.request.event_id,
                                       node_request_id=r.request.id)
            try:
                if not r.poll():
                    active_handlers.append(r)
                    if r.paused:
                        self.paused_handler = r
                else:
                    log.debug("Removing request handler")
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our NodeRequest
                # lock so it's no longer active
                log.error("Removing request handler (lost zookeeper session)")
                continue
            except Exception:
                # If we fail to poll a request handler log it but move on
                # and process the other handlers. We keep this handler around
                # and will try again later.
                log.exception("Error polling request handler")
                active_handlers.append(r)
        self.request_handlers = active_handlers
        active_reqs = [r.request.id for r in self.request_handlers]
        self.log.debug("Active requests: %s", active_reqs)

    def _hasTenantQuota(self, request, provider_manager):
        '''
        Checks if a tenant has enough quota to handle a list of nodes.
        This takes into account the all currently existing nodes as reported
        by zk.

        :param request: the node request in question
        :param provider_manager: the provider manager for the request
        :return: True if there is enough quota for the tenant, False otherwise
        '''
        log = get_annotated_logger(self.log, event_id=request.event_id,
                                   node_request_id=request.id)

        tenant_name = request.tenant_name
        needed_quota = QuotaInformation()

        pool = self.getPoolConfig()
        for ntype in request.node_types:
            # can not determine needed quota for ntype if label not in pool
            # therefore just skip them here to avoid errors in
            # 'quotaNeededByLabel'
            if ntype not in pool.labels:
                continue
            needed_quota.add(provider_manager.quotaNeededByLabel(ntype, pool))

        used_quota = self._getUsedQuotaForTenant(tenant_name)
        tenant_quota = QuotaInformation(
            default=math.inf,
            **self.nodepool.config.tenant_resource_limits[tenant_name])

        tenant_quota.subtract(used_quota)
        log.debug("Current tenant quota: %s", tenant_quota)
        tenant_quota.subtract(needed_quota)
        log.debug("Predicted remaining tenant quota: %s", tenant_quota)
        return tenant_quota.non_negative()

    def _getUsedQuotaForTenant(self, tenant_name):
        used_quota = QuotaInformation()
        for node in self.zk.nodeIterator(cached_ids=True):
            if not node.resources:
                continue
            if node.tenant_name == tenant_name:
                resources = QuotaInformation(**node.resources)
                used_quota.add(resources)
        return used_quota

    # ---------------------------------------------------------------
    # Public methods
    # ---------------------------------------------------------------

    def activeThreads(self):
        '''
        Return the number of alive threads in use by this provider.

        This is an approximate, top-end number for alive threads, since some
        threads obviously may have finished by the time we finish the
        calculation.
        '''
        total = 0
        for r in self.request_handlers:
            total += r.alive_thread_count
        return total

    def getZK(self):
        return self.nodepool.getZK()

    def getProviderConfig(self):
        return self.nodepool.config.providers.get(self.provider_name)

    def getPoolConfig(self):
        provider = self.getProviderConfig()
        if provider:
            return provider.pools[self.pool_name]
        else:
            return None

    def getProviderManager(self):
        return self.nodepool.getProviderManager(self.provider_name)

    def run(self):
        self.running = True

        while self.running:
            try:
                # Don't do work if we've lost communication with the ZK cluster
                did_suspend = False
                while self.zk and (self.zk.suspended or self.zk.lost):
                    did_suspend = True
                    self.log.info("ZooKeeper suspended. Waiting")
                    time.sleep(SUSPEND_WAIT_TIME)
                if did_suspend:
                    self.log.info("ZooKeeper available. Resuming")

                # Make sure we're always registered with ZK
                launcher = zk.Launcher()
                launcher.id = self.launcher_id
                for prov_cfg in self.nodepool.config.providers.values():
                    launcher.supported_labels.update(
                        prov_cfg.getSupportedLabels())
                launcher.provider_name = self.provider_name
                self.zk.registerLauncher(launcher)

                self.updateProviderLimits(
                    self.nodepool.config.providers.get(self.provider_name))
                self.updateTenantLimits(
                    self.nodepool.config.tenant_resource_limits)

                if not self.paused_handler:
                    while not self._assignHandlers():
                        # _assignHandlers can take quite some time on a busy
                        # system so sprinkle _removeCompletedHandlers in
                        # between such that we have a chance to fulfill
                        # requests that already have all nodes.
                        self._removeCompletedHandlers()
                else:
                    # If we are paused, one request handler could not
                    # satisfy its assigned request, so give it
                    # another shot. Unpause ourselves if it completed.
                    self.paused_handler.run()
                    if not self.paused_handler.paused:
                        self.paused_handler = None

                self._removeCompletedHandlers()
            except Exception:
                self.log.exception("Error in PoolWorker:")
            self.stop_event.wait(self.watermark_sleep)

        # Cleanup on exit
        if self.paused_handler:
            self.paused_handler.unlockNodeSet(clear_allocation=True)

    def stop(self):
        '''
        Shutdown the PoolWorker thread.

        Do not wait for the request handlers to finish. Any nodes
        that are in the process of launching will be cleaned up on a
        restart. They will be unlocked and BUILDING in ZooKeeper.
        '''
        self.log.info("%s received stop" % self.name)
        self.running = False
        self.zk.deregisterLauncher(self.launcher_id)
        self.stop_event.set()


class BaseCleanupWorker(threading.Thread):
    def __init__(self, nodepool, interval, name):
        threading.Thread.__init__(self, name=name)
        self._nodepool = nodepool
        self._interval = interval
        self._running = False
        self._stop_event = threading.Event()

    def run(self):
        self.log.info("Starting")
        self._running = True

        while self._running:
            # Don't do work if we've lost communication with the ZK cluster
            did_suspend = False
            zk_conn = self._nodepool.getZK()
            while zk_conn and (zk_conn.suspended or zk_conn.lost):
                did_suspend = True
                self.log.info("ZooKeeper suspended. Waiting")
                time.sleep(SUSPEND_WAIT_TIME)
            if did_suspend:
                self.log.info("ZooKeeper available. Resuming")

            self._run()
            self._stop_event.wait(self._interval)

        self.log.info("Stopped")

    def stop(self):
        self._running = False
        self._stop_event.set()
        self.join()


class CleanupWorker(BaseCleanupWorker):
    def __init__(self, nodepool, interval):
        super(CleanupWorker, self).__init__(
            nodepool, interval, name='CleanupWorker')
        self.log = logging.getLogger("nodepool.CleanupWorker")

        # List of callable tasks we want to perform during cleanup, and a
        # brief description of the task.
        self._tasks = [
            (self._cleanupNodeRequestLocks, 'node request lock cleanup'),
            (self._cleanupLeakedInstances, 'leaked instance cleanup'),
            (self._cleanupLostRequests, 'lost request cleanup'),
            (self._cleanupMaxReadyAge, 'max ready age cleanup'),
            (self._cleanupMaxHoldAge, 'max hold age cleanup'),
            (self._cleanupEmptyNodes, 'empty node cleanup'),
        ]

    def _resetLostRequest(self, zk_conn, req):
        '''
        Reset the request state and deallocate nodes.

        :param ZooKeeper zk_conn: A ZooKeeper connection object.
        :param NodeRequest req: The lost NodeRequest object.
        '''
        # Double check the state after the lock
        req = zk_conn.getNodeRequest(req.id)
        if req.state != zk.PENDING:
            return

        log = get_annotated_logger(self.log, event_id=req.event_id,
                                   node_request_id=req.id)
        for node in zk_conn.nodeIterator():
            if node.allocated_to == req.id:
                try:
                    zk_conn.lockNode(node)
                except exceptions.ZKLockException:
                    log.warning(
                        "Unable to grab lock to deallocate node %s from "
                        "request", node.id)
                    return

                # If the node is in state init then the launcher that worked
                # on the lost request has been interrupted between creating
                # the znode and locking/setting to building. In this case the
                # znode is leaked and we should delete the node instead of
                # just deallocating it.
                if node.state == zk.INIT:
                    node.state = zk.DELETING

                node.allocated_to = None
                try:
                    zk_conn.storeNode(node)
                    log.debug("Deallocated node %s for lost request", node.id)
                except Exception:
                    log.exception(
                        "Unable to deallocate node %s from request:", node.id)

                zk_conn.unlockNode(node)

        req.state = zk.REQUESTED
        req.nodes = []
        zk_conn.storeNodeRequest(req)
        log.info("Reset lost request")

    def _cleanupLostRequests(self):
        '''
        Look for lost requests and reset them.

        A lost request is a node request that was left in the PENDING state
        when nodepool exited. We need to look for these (they'll be unlocked)
        and disassociate any nodes we've allocated to the request and reset
        the request state to REQUESTED so it will be processed again.
        '''
        zk_conn = self._nodepool.getZK()
        for req in zk_conn.nodeRequestIterator():
            log = get_annotated_logger(self.log, event_id=req.event_id,
                                       node_request_id=req.id)
            if req.state == zk.PENDING:
                try:
                    zk_conn.lockNodeRequest(req, blocking=False)
                except exceptions.ZKLockException:
                    continue

                try:
                    self._resetLostRequest(zk_conn, req)
                except Exception:
                    log.exception("Error resetting lost request:")

                zk_conn.unlockNodeRequest(req)

    def _cleanupNodeRequestLocks(self):
        '''
        Remove request locks where the request no longer exists.

        Because the node request locks are not direct children of the request
        znode, we need to remove the locks separately after the request has
        been processed. Only remove them after LOCK_CLEANUP seconds have
        passed. This helps reduce chances of the scenario where a request could
        go away _while_ a lock is currently held for processing and the cleanup
        thread attempts to delete it. The delay should reduce the chance that
        we delete a currently held lock.
        '''
        zk = self._nodepool.getZK()
        requests = zk.getNodeRequests()
        now = time.time()
        for lock_stat in zk.nodeRequestLockStatsIterator():
            if lock_stat.lock_id in requests:
                continue
            if (now - lock_stat.stat.mtime / 1000) > LOCK_CLEANUP:
                zk.deleteNodeRequestLock(lock_stat.lock_id)

    def _cleanupLeakedInstances(self):
        '''
        Allow each provider manager a chance to cleanup resources.
        '''
        for provider in self._nodepool.config.providers.values():
            manager = self._nodepool.getProviderManager(provider.name)
            if manager:
                try:
                    manager.cleanupLeakedResources()
                except Exception:
                    self.log.exception(
                        "Failure during resource cleanup for provider %s",
                        provider.name)

    def _cleanupMaxReadyAge(self):
        '''
        Delete any server past their max-ready-age.

        Remove any servers which are longer than max-ready-age in ready state.
        '''

        # first get all labels with max_ready_age > 0
        label_names = []
        for label_name in self._nodepool.config.labels:
            if self._nodepool.config.labels[label_name].max_ready_age > 0:
                label_names.append(label_name)

        zk_conn = self._nodepool.getZK()
        ready_nodes = zk_conn.getReadyNodesOfTypes(label_names)

        for label_name in ready_nodes:
            # get label from node
            label = self._nodepool.config.labels[label_name]

            for node in ready_nodes[label_name]:

                # Can't do anything if we aren't configured for this provider.
                if node.provider not in self._nodepool.config.providers:
                    continue

                # check state time against now
                now = int(time.time())
                if (now - node.state_time) < label.max_ready_age:
                    continue

                try:
                    zk_conn.lockNode(node, blocking=False)
                except exceptions.ZKLockException:
                    continue

                # Double check the state now that we have a lock since it
                # may have changed on us.
                if node.state != zk.READY:
                    zk_conn.unlockNode(node)
                    continue

                self.log.debug("Node %s exceeds max ready age: %s >= %s",
                               node.id, now - node.state_time,
                               label.max_ready_age)

                try:
                    node.state = zk.DELETING
                    zk_conn.storeNode(node)
                except Exception:
                    self.log.exception(
                        "Failure marking aged node %s for delete:", node.id)
                finally:
                    zk_conn.unlockNode(node)

    def _cleanupMaxHoldAge(self):
        '''
        Delete any held server past their max-hold-age.

        Remove any servers which are longer than max-hold-age in hold state.
        '''
        self.log.debug('Cleaning up held nodes...')

        zk_conn = self._nodepool.getZK()
        held_nodes = [n for n in zk_conn.nodeIterator() if n.state == zk.HOLD]
        for node in held_nodes:
            # Can't do anything if we aren't configured for this provider.
            if node.provider not in self._nodepool.config.providers:
                continue

            if node.hold_expiration is not None and node.hold_expiration > 0:
                expiration = node.hold_expiration
            else:
                expiration = math.inf
            max_uptime = min(expiration, self._nodepool.config.max_hold_age)
            if math.isinf(max_uptime):
                continue

            # check state time against now
            now = int(time.time())
            if (now - node.state_time) < max_uptime:
                continue

            try:
                zk_conn.lockNode(node, blocking=False)
            except exceptions.ZKLockException:
                continue

            # Double check the state now that we have a lock since it
            # may have changed on us.
            if node.state != zk.HOLD:
                zk_conn.unlockNode(node)
                continue

            self.log.debug("Node %s exceeds max hold age (%s): %s >= %s",
                           node.id,
                           ("manual setting"
                            and node.hold_expiration == max_uptime
                            or "configuration setting"),
                           now - node.state_time,
                           max_uptime)

            try:
                node.state = zk.DELETING
                zk_conn.storeNode(node)
            except Exception:
                self.log.exception(
                    "Failure marking aged node %s for delete:", node.id)
            finally:
                zk_conn.unlockNode(node)

    def _cleanupEmptyNodes(self):
        '''
        Remove any Node znodes that may be totally empty.
        '''
        self.log.debug('Cleaning up empty nodes...')
        zk_conn = self._nodepool.getZK()

        # We cannot use nodeIterator() here since that does not yield us
        # empty nodes.
        for node_id in zk_conn.getNodes():
            node = zk_conn.getNode(node_id)
            if node is None:
                self.log.debug("Removing empty node %s", node_id)
                zk_conn.deleteRawNode(node_id)

    def _run(self):
        '''
        Catch exceptions individually so that other cleanup routines may
        have a chance.
        '''
        for task, description in self._tasks:
            try:
                task()
            except Exception:
                self.log.exception(
                    "Exception in %s (%s)", self.name, description)


class DeletedNodeWorker(BaseCleanupWorker):
    '''
    Class for deleting all nodes with state of DELETING.
    '''

    def __init__(self, nodepool, interval):
        super(DeletedNodeWorker, self).__init__(
            nodepool, interval, name='DeletedNodeWorker')
        self.log = logging.getLogger("nodepool.DeletedNodeWorker")

    def _deleteInstance(self, node):
        '''
        Delete an instance from a provider.

        A thread will be spawned to delete the actual instance from the
        provider.

        :param Node node: A Node object representing the instance to delete.
        '''
        self.log.info("Deleting %s instance %s from %s",
                      node.state, node.external_id, node.provider)
        try:
            pm = self._nodepool.getProviderManager(node.provider)
            pm.startNodeCleanup(node)
        except Exception:
            self.log.exception("Could not delete instance %s on provider %s",
                               node.external_id, node.provider)

    def _cleanupNodes(self):
        '''
        Delete instances from providers and nodes entries from ZooKeeper.
        '''
        cleanup_states = (zk.USED, zk.IN_USE, zk.BUILDING, zk.FAILED,
                          zk.DELETING, zk.DELETED, zk.ABORTED)

        zk_conn = self._nodepool.getZK()
        for node in zk_conn.nodeIterator():
            # If a ready node has been allocated to a request, but that
            # request is now missing, deallocate it.
            if (node.state == zk.READY and node.allocated_to
                    and not zk_conn.getNodeRequest(node.allocated_to)):
                try:
                    zk_conn.lockNode(node, blocking=False)
                except exceptions.ZKLockException:
                    pass
                else:
                    # Double check node conditions after lock
                    if node.state == zk.READY and node.allocated_to:
                        node.allocated_to = None
                        try:
                            zk_conn.storeNode(node)
                            self.log.debug(
                                "Deallocated node %s with missing request %s",
                                node.id, node.allocated_to)
                        except Exception:
                            self.log.exception(
                                "Failed to deallocate node %s for missing "
                                "request %s:", node.id, node.allocated_to)

                    zk_conn.unlockNode(node)

            # Can't do anything if we aren't configured for this provider.
            if node.provider not in self._nodepool.config.providers:
                continue

            # Any nodes in these states that are unlocked can be deleted.
            if node.state in cleanup_states:
                try:
                    zk_conn.lockNode(node, blocking=False)
                except exceptions.ZKLockException:
                    continue

                if (node.state == zk.DELETED or
                    node.provider is None):
                    # The node has been deleted out from under us --
                    # we only obtained the lock because in the
                    # recursive delete, the lock is deleted first and
                    # we locked the node between the time of the lock
                    # delete and the node delete.  We need to clean up
                    # the mess.
                    try:
                        # This should delete the lock as well
                        zk_conn.deleteNode(node)
                    except Exception:
                        self.log.exception(
                            "Error deleting already deleted znode:")
                        try:
                            zk_conn.unlockNode(node)
                        except Exception:
                            self.log.exception(
                                "Error unlocking already deleted znode:")
                    continue

                # Double check the state now that we have a lock since it
                # may have changed on us.
                if node.state not in cleanup_states:
                    zk_conn.unlockNode(node)
                    continue

                self.log.debug(
                    "Marking for deletion unlocked node %s "
                    "(state: %s, allocated_to: %s)",
                    node.id, node.state, node.allocated_to)

                # The NodeDeleter thread will unlock and remove the
                # node from ZooKeeper if it succeeds.
                try:
                    self._deleteInstance(node)
                except Exception:
                    self.log.exception(
                        "Failure deleting node %s in cleanup state %s:",
                        node.id, node.state)
                    zk_conn.unlockNode(node)

    def _run(self):
        try:
            self._cleanupNodes()
        except Exception:
            self.log.exception("Exception in DeletedNodeWorker:")


class StatsWorker(BaseCleanupWorker, stats.StatsReporter):

    def __init__(self, nodepool, interval):
        super().__init__(nodepool, interval, name='StatsWorker')
        self.log = logging.getLogger('nodepool.StatsWorker')
        self.stats_event = threading.Event()
        self.election = None

    def stop(self):
        self._running = False
        if self.election is not None:
            self.log.debug('Cancel leader election')
            self.election.cancel()
        self.stats_event.set()
        super().stop()

    def _run(self):
        try:
            stats.StatsReporter.__init__(self)

            if not self._statsd:
                return

            if self.election is None:
                zk = self._nodepool.getZK()
                identifier = "%s-%s" % (socket.gethostname(), os.getpid())
                self.election = zk.getStatsElection(identifier)

            if not self._running:
                return

            self.election.run(self._run_stats)

        except Exception:
            self.log.exception('Exception in StatsWorker:')

    def _run_stats(self):
        self.log.info('Won stats reporter election')

        # enable us getting events
        zk = self._nodepool.getZK()
        zk.setNodeStatsEvent(self.stats_event)

        while self._running:
            signaled = self.stats_event.wait()

            if not self._running:
                break

            if not signaled:
                continue

            self.stats_event.clear()
            try:
                self.updateNodeStats(zk)
            except Exception:
                self.log.exception("Exception while reporting stats:")
            time.sleep(1)

        # Unregister from node stats events
        zk.setNodeStatsEvent(None)


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, securefile, configfile,
                 watermark_sleep=WATERMARK_SLEEP):
        threading.Thread.__init__(self, name='NodePool')
        self.securefile = securefile
        self.configfile = configfile
        self.watermark_sleep = watermark_sleep
        self.cleanup_interval = 60
        self.delete_interval = 5
        self.stats_interval = 5
        self._stopped = False
        self._stop_event = threading.Event()
        self.config = None
        self.zk = None
        self.statsd = stats.get_client()
        self._pool_threads = {}
        self._cleanup_thread = None
        self._delete_thread = None
        self._stats_thread = None
        self._submittedRequests = {}
        self.ready = False

    def stop(self):
        self._stopped = True
        self._stop_event.set()
        # Our run method can start new threads, so make sure it has
        # completed before we continue the shutdown.
        if self.is_alive():
            self.join()

        if self._cleanup_thread:
            self._cleanup_thread.stop()
            self._cleanup_thread.join()

        if self._delete_thread:
            self._delete_thread.stop()
            self._delete_thread.join()

        if self._stats_thread:
            self._stats_thread.stop()
            self._stats_thread.join()

        # Don't let stop() return until all pool threads have been
        # terminated.
        self.log.debug("Stopping pool threads")
        for thd in self._pool_threads.values():
            if thd.is_alive():
                thd.stop()
            self.log.debug("Waiting for %s" % thd.name)
            thd.join()

        # Stop providers after all the cleanup threads have stopped.
        if self.config:
            provider_manager.ProviderManager.stopProviders(self.config)

        if self.zk:
            self.zk.disconnect()
        self.log.debug("Finished stopping")

    def loadConfig(self):
        config = nodepool_config.loadConfig(self.configfile)
        if self.securefile:
            nodepool_config.loadSecureConfig(config, self.securefile)
        return config

    def reconfigureZooKeeper(self, config):
        if self.config:
            running = list(self.config.zookeeper_servers.values())
        else:
            running = None

        configured = list(config.zookeeper_servers.values())
        if running == configured:
            return

        if not self.zk and configured:
            self.log.debug("Connecting to ZooKeeper servers")
            self.zk = zk.ZooKeeper()
            self.zk.connect(configured,
                            tls_cert=config.zookeeper_tls_cert,
                            tls_key=config.zookeeper_tls_key,
                            tls_ca=config.zookeeper_tls_ca,
                            timeout=config.zookeeper_timeout,
                            )
        else:
            self.log.debug("Detected ZooKeeper server changes")
            self.zk.resetHosts(configured)

    def setConfig(self, config):
        self.config = config

    def getZK(self):
        return self.zk

    def getProviderManager(self, provider_name):
        return self.config.provider_managers.get(provider_name)

    def getPoolWorkers(self, provider_name):
        return [t for t in self._pool_threads.values() if
                t.provider_name == provider_name]

    def updateConfig(self):
        config = self.loadConfig()
        self.reconfigureZooKeeper(config)
        provider_manager.ProviderManager.reconfigure(self.config, config,
                                                     self.getZK())
        for provider_name in list(config.providers.keys()):
            if provider_name not in config.provider_managers:
                del config.providers[provider_name]
        self.setConfig(config)

    def removeCompletedRequests(self):
        '''
        Remove (locally and in ZK) fulfilled node requests.

        We also must reset the allocated_to attribute for each Node assigned
        to our request, since we are deleting the request.
        '''

        # Use a copy of the labels because we modify _submittedRequests
        # within the loop below. Note that keys() returns an iterator in
        # py3, so we need to explicitly make a new list.
        requested_labels = list(self._submittedRequests.keys())

        for label in requested_labels:
            label_requests = self._submittedRequests[label]
            active_requests = []

            for req in label_requests:
                req = self.zk.getNodeRequest(req.id)

                if not req:
                    continue

                log = get_annotated_logger(self.log, event_id=req.event_id,
                                           node_request_id=req.id)
                if req.state == zk.FULFILLED:
                    # Reset node allocated_to
                    for node_id in req.nodes:
                        node = self.zk.getNode(node_id)
                        node.allocated_to = None
                        # NOTE: locking shouldn't be necessary since a node
                        # with allocated_to set should not be locked except
                        # by the creator of the request (us).
                        self.zk.storeNode(node)
                    self.zk.deleteNodeRequest(req)
                elif req.state == zk.FAILED:
                    log.debug("min-ready node request failed: %s", req)
                    self.zk.deleteNodeRequest(req)
                else:
                    active_requests.append(req)

            if active_requests:
                self._submittedRequests[label] = active_requests
            else:
                self.log.debug(
                    "No more active min-ready requests for label %s", label)
                del self._submittedRequests[label]

    def labelImageIsAvailable(self, label):
        '''
        Check if the image associated with a label is ready in any provider.

        :param Label label: The label config object.

        :returns: True if image associated with the label is uploaded and
            ready in at least one provider. False otherwise.
        '''
        for pool in label.pools:
            if not pool.provider.manage_images:
                # Provider doesn't manage images, assuming label is ready
                return True
            for pool_label in pool.labels.values():
                if pool_label.diskimage:
                    if self.zk.getMostRecentImageUpload(
                            pool_label.diskimage.name, pool.provider.name):
                        return True
                else:
                    manager = self.getProviderManager(pool.provider.name)
                    if manager.labelReady(pool_label):
                        return True
        return False

    def createMinReady(self):
        '''
        Create node requests to make the minimum amount of ready nodes.

        Since this method will be called repeatedly, we need to take care to
        note when we have already submitted node requests to satisfy min-ready.
        Requests we've already submitted are stored in the _submittedRequests
        dict, keyed by label.
        '''
        def createRequest(label_name):
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.requestor = "NodePool:min-ready"
            req.node_types.append(label_name)
            req.reuse = False    # force new node launches
            self.zk.storeNodeRequest(req, priority="900")
            if label_name not in self._submittedRequests:
                self._submittedRequests[label_name] = []
            self._submittedRequests[label_name].append(req)

        # Since we could have already submitted node requests, do not
        # resubmit a request for a type if a request for that type is
        # still in progress.
        self.removeCompletedRequests()
        label_names = list(self.config.labels.keys())
        requested_labels = list(self._submittedRequests.keys())
        needed_labels = list(set(label_names) - set(requested_labels))

        # Note we explicitly don't use the cache here because otherwise we can
        # end up creating more min-ready nodes than we want.
        ready_nodes = self.zk.getReadyNodesOfTypes(needed_labels, cached=False)

        for label in self.config.labels.values():
            if label.name not in needed_labels:
                continue
            min_ready = label.min_ready
            if min_ready <= 0:
                continue   # disabled

            # Calculate how many nodes of this type we need created
            need = 0
            if label.name not in ready_nodes:
                need = label.min_ready
            elif len(ready_nodes[label.name]) < min_ready:
                need = min_ready - len(ready_nodes[label.name])

            if need and self.labelImageIsAvailable(label):
                # Create requests for 1 node at a time. This helps to split
                # up requests across providers, and avoids scenario where a
                # single provider might fail the entire request because of
                # quota (e.g., min-ready=2, but max-servers=1).
                self.log.info("Creating requests for %d %s nodes",
                              need, label.name)
                for i in range(0, need):
                    createRequest(label.name)

    def run(self):
        '''
        Start point for the NodePool thread.
        '''
        self.log.info("Nodepool launcher %s starting",
                      npd_version_info.release_string())
        while not self._stopped:
            try:
                self.updateConfig()

                # Don't do work if we've lost communication with the ZK cluster
                did_suspend = False
                while self.zk and (self.zk.suspended or self.zk.lost):
                    did_suspend = True
                    self.log.info("ZooKeeper suspended. Waiting")
                    time.sleep(SUSPEND_WAIT_TIME)
                if did_suspend:
                    self.log.info("ZooKeeper available. Resuming")

                self.createMinReady()

                if not self._cleanup_thread:
                    self._cleanup_thread = CleanupWorker(
                        self, self.cleanup_interval)
                    self._cleanup_thread.start()

                if not self._delete_thread:
                    self._delete_thread = DeletedNodeWorker(
                        self, self.delete_interval)
                    self._delete_thread.start()

                if not self._stats_thread:
                    self._stats_thread = StatsWorker(self, self.stats_interval)
                    self._stats_thread.start()

                # Stop any PoolWorker threads if the pool was removed
                # from the config.
                pool_keys = set()
                for provider in self.config.providers.values():
                    for pool in provider.pools.values():
                        pool_keys.add(provider.name + '-' + pool.name)

                new_pool_threads = {}
                for key in self._pool_threads.keys():
                    if key not in pool_keys:
                        self._pool_threads[key].stop()
                    else:
                        new_pool_threads[key] = self._pool_threads[key]
                self._pool_threads = new_pool_threads

                # Start (or restart) provider threads for each provider in
                # the config. Removing a provider from the config and then
                # adding it back would cause a restart.
                for provider in self.config.providers.values():
                    for pool in provider.pools.values():
                        key = provider.name + '-' + pool.name
                        if key not in self._pool_threads:
                            t = PoolWorker(self, provider.name, pool.name)
                            self.log.info("Starting %s" % t.name)
                            t.start()
                            self._pool_threads[key] = t
                        elif not self._pool_threads[key].is_alive():
                            self._pool_threads[key].stop()
                            self._pool_threads[key].join()
                            t = PoolWorker(self, provider.name, pool.name)
                            self.log.info("Restarting %s" % t.name)
                            t.start()
                            self._pool_threads[key] = t
            except Exception:
                self.log.exception("Exception in main loop:")

            # At this point all providers are registered and fully functional
            # so we can mark nodepool as ready.
            self.ready = True

            self._stop_event.wait(self.watermark_sleep)
