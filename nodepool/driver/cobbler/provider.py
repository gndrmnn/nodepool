# Copyright 2020 AMD
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
import random
import threading
import time
import xmlrpc.client

from collections import defaultdict

from nodepool.driver import Provider
from nodepool.driver.cobbler import handler
from nodepool.driver.utils import QuotaInformation, QuotaSupport
from nodepool.driver.utils import NodeDeleter


class CobblerProvider(Provider, QuotaSupport):
    log = logging.getLogger("nodepool.driver.cobbler.CobblerProvider")
    DEFAULT_NAME = "zuul-nodepool-default"

    def __init__(self, config, *args):
        super().__init__()
        self.provider = config
        self._zk = None
        self.ready = False
        # protect access to self.registered
        self._provider_lock = threading.Lock()

        # first level is machine name then details and external
        self.registered = defaultdict(dict)
        # first level is label and then machine name
        self.registered_by_labels = defaultdict(dict)

        self.cobbler_url = None

    def _get_cobbler(self, api_server_url):
        server = xmlrpc.client.ServerProxy(api_server_url)
        return server

    def _unassign_machine(self, external_id):
        assert self._provider_lock.locked()

        cobbler = self._get_cobbler(self.cobbler_url)

        machine = self.registered[external_id]

        # TODO catch xml rpc failures
        if machine is not None:
            # use a new token in case the user token has expired
            token = cobbler.login(self.provider.api_server_username,
                                  self.provider.api_server_password)

            sid = cobbler.get_system_handle(external_id, token)

            cobbler.power_system(sid, "off", token)

            cobbler.modify_system(sid, "profile",
                                  self.DEFAULT_NAME, token)
            cobbler.save_system(sid, token)

            if machine['profile'] is not None and \
                    machine['profile'] != self.DEFAULT_NAME:
                cobbler.remove_profile(machine['profile'], token)
            machine['profile'] = None

            if machine['distro'] is not None and \
                    machine['distro'] != self.DEFAULT_NAME:
                cobbler.remove_distro(machine['distro'], token)
            machine['distro'] = None

            cobbler.logout(token)
            cobbler.logout(machine['token'])
            machine['token'] = None
            machine['node_id'] = None

        self.log.debug("Unassigned machine %s", external_id)

    def _make_cblr_distro_profile(self, cobbler, dp_name, token):
        distro = cobbler.new_distro(token)
        cobbler.modify_distro(distro, 'name', dp_name, token)
        # using /bin/cat as a placeholder
        cobbler.modify_distro(distro, 'kernel', '/bin/cat', token)
        cobbler.modify_distro(distro, 'initrd', '/bin/cat', token)
        cobbler.save_distro(distro, token)

        profile = cobbler.new_profile(token)
        cobbler.modify_profile(profile, 'name', dp_name, token)
        cobbler.modify_profile(profile, 'distro', dp_name, token)
        cobbler.save_profile(profile, token)

    def assign_machine(self, label, pool, node_id):
        name = None
        machine = None

        cobbler = self._get_cobbler(self.cobbler_url)
        with self._provider_lock:
            unused_machines = {k: v for k, v
                               in self.registered_by_labels[label].items()
                               if v['token'] is None and v['node_id'] is None}

            name, machine = random.choice(list(unused_machines.items()))

            if name is not None and machine is not None:
                token = cobbler.login(self.provider.api_server_username,
                                      self.provider.api_server_password)
                machine['node_id'] = node_id
                machine['token'] = token
                machine['token_last_used'] = time.time()

                # Create cobbler distro and profile for this assignment
                # TODO catch xml rpc failures
                dp_name = "znp-%s-%s-%s" % (pool, name, node_id)
                self._make_cblr_distro_profile(cobbler, dp_name, token)

                # associate the profile with the assigned system
                sid = cobbler.get_system_handle(name, token)
                cobbler.modify_system(sid, "profile", dp_name, token)
                cobbler.save_system(sid, token)

                machine['distro'] = dp_name
                machine['profile'] = dp_name

        self.log.debug("Assigning machine %s for label %s, node %s",
                       name, label, node_id)

        return name, machine

    def has_unassigned_machine(self, label):
        with self._provider_lock:
            for k, v in self.registered_by_labels[label].items():
                if v['token'] is None and v['node_id'] is None:
                    return True

        return False

    def _update_available_machines(self):
        self.log.debug("_update_available_machines")
        cobbler = self._get_cobbler(self.cobbler_url)

        # Find machines no longer available from Cobbler
        machines = set()
        for pool in self.provider.pools.values():
            # executor-zone is used by zuul
            attrs = {k: pool.node_attributes[k] for k in
                     pool.node_attributes if k != 'executor-zone'}
            machines = machines | set(cobbler.find_system(attrs))

        with self._provider_lock:
            existing = {k for k in self.registered}
            removed = existing - set(machines)
            for machine in removed:
                m = self.registered[machine]

                if m['token'] is not None or m['node_id'] is not None:
                    self._unassign_machine(machine)

                del self.registered[machine]
                for label in m['labels']:
                    del self.registered_by_labels[label][machine]

            for pool in self.provider.pools.values():
                # executor-zone is used by zuul
                attrs = {k: pool.node_attributes[k] for k in
                         pool.node_attributes if k != 'executor-zone'}
                machines = cobbler.find_system(attrs)

                for machine in machines:
                    if machine not in self.registered:
                        details = cobbler.get_system(machine)

                        if not isinstance(details, dict):
                            continue

                        self.registered[machine]['details'] = details
                        self.registered[machine]['node_id'] = None
                        self.registered[machine]['token'] = None
                        self.registered[machine]['distro'] = None
                        self.registered[machine]['profile'] = None
                        self.registered[machine]['pool_name'] = pool.name
                        self.registered[machine]['labels'] = set()

                        for label in pool.labels:
                            self.registered_by_labels[label][machine] = \
                                self.registered[machine]
                            self.registered[machine]['labels'].add(label)

    def _start(self, zk_conn):
        self._zk = zk_conn

        self.cobbler_url = "http://" + self.provider.name + "/cobbler_api"
        self.log.info("Trying to connect to cobbler: %s", self.cobbler_url)
        cobbler = self._get_cobbler(self.cobbler_url)
        version = cobbler.version()
        self.log.info("Connected to Cobbler version: %s", str(version))

        default_profile = cobbler.find_profile({'name': self.DEFAULT_NAME})
        if len(default_profile) < 1:
            self.log.info("Create default Cobbler profile: %s",
                          self.DEFAULT_NAME)

            # create default profile
            token = cobbler.login(self.provider.api_server_username,
                                  self.provider.api_server_password)
            self._make_cblr_distro_profile(cobbler, self.DEFAULT_NAME, token)
            cobbler.logout(token)

        # self.log.info("pools %s",
        #               pprint.pformat(
        #                   self.provider.pools['main'].node_attributes))

        self._update_available_machines()

    def start(self, zk_conn):
        self.log.debug("Starting")

        self._start(zk_conn)

        self.ready = True

    def stop(self):
        self.log.debug("Stopping")
        self.ready = False

    def listNodes(self):
        servers = []

        class ServerMachine:
            def __init__(self, name, node_id, pool_name, provider):
                self.id = name
                self.name = name
                self.metadata = {'nodepool_node_id': node_id,
                                 'nodepool_pool_name': pool_name,
                                 'nodepool_provider_name': provider.name}

            def get(self, name, default=None):
                return getattr(self, name, default)

        with self._provider_lock:
            for k, v in self.registered.items():
                # skip unassigned machine
                if v['node_id'] is None:
                    continue

                servers.append(ServerMachine(k,
                                             v['node_id'],
                                             v['pool_name'],
                                             self.provider))

        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        pass

    def _refresh_token(self, token, machine):
        self.log.debug("Refreshing token for %s", machine)

        cobbler = self._get_cobbler(self.cobbler_url)

        # use token to prevent it from expiring
        cobbler.check_access(token, machine)

    def cleanupLeakedResources(self):
        self.log.debug("cleanupLeakedResources")

        self._update_available_machines()

        with self._provider_lock:
            for k, v in self.registered.items():
                if v['token'] is None or \
                        time.time() - v['token_last_used'] < \
                        self.provider.token_keepalive:
                    continue

                # cobbler login token expires after 60 minutes
                # refresh every 20 minutes
                v['token_last_used'] = time.time()
                t = threading.Thread(target=self._refresh_token,
                                     args=(v['token'], k))
                t.start()

    def startNodeCleanup(self, node):
        t = NodeDeleter(self._zk, self, node)
        t.start()
        return t

    def cleanupNode(self, server_id):
        self.log.debug("cleanupNodes")
        if not self.ready:
            return

        with self._provider_lock:
            self._unassign_machine(server_id)

        self.log.debug("%s: cleaned up cobbler node" % server_id)

    def waitForNodeCleanup(self, server_id):
        self.log.debug("%s: wait for clean up cobbler node" % server_id)
        pass

    def getRequestHandler(self, poolworker, request):
        return handler.CobblerNodeRequestHandler(poolworker, request)

    def getProviderLimits(self):
        # TODO: query the api to get real limits
        return QuotaInformation(
            cores=math.inf,
            instances=math.inf,
            ram=math.inf,
            default=math.inf)

    def quotaNeededByLabel(self, ntype, pool):
        # TODO: return real quota information about a label
        return QuotaInformation(cores=1, instances=1, ram=1, default=1)

    def unmanagedQuotaUsed(self):
        # TODO: return real quota information about quota
        return QuotaInformation()
