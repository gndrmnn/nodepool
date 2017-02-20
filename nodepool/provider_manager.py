#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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

import json
import logging
import paramiko
from contextlib import contextmanager

import exceptions
import fakeprovider
from nodeutils import iterate_timeout
from task_manager import TaskManager, ManagerStoppedException


class NotFound(Exception):
    pass


def get_provider_manager(provider, use_taskmanager):
    if (provider.cloud_config.get_auth_args().get('auth_url') == 'fake'):
        return FakeProviderManager(provider, use_taskmanager)
    else:
        return ProviderManager(provider, use_taskmanager)


class ProviderManager(object):
    log = logging.getLogger("nodepool.ProviderManager")

    @staticmethod
    def reconfigure(old_config, new_config, use_taskmanager=True):
        stop_managers = []
        for p in new_config.providers.values():
            oldmanager = None
            if old_config:
                oldmanager = old_config.provider_managers.get(p.name)
            if oldmanager and p != oldmanager.provider:
                stop_managers.append(oldmanager)
                oldmanager = None
            if oldmanager:
                new_config.provider_managers[p.name] = oldmanager
            else:
                ProviderManager.log.debug("Creating new ProviderManager object"
                                          " for %s" % p.name)
                new_config.provider_managers[p.name] = \
                    get_provider_manager(p, use_taskmanager)
                new_config.provider_managers[p.name].start()

        for stop_manager in stop_managers:
            stop_manager.stop()

    @staticmethod
    def stopProviders(config):
        for m in config.provider_managers.values():
            m.stop()
            m.join()

    def __init__(self, provider, use_taskmanager):
        self.provider = provider

    def start(self):
        self.resetClient()

    def stop(self):
        pass

    def join(self):
        pass

    def _getClient(self):
        # TODO(mordred) create zk client
        return None

    def resetClient(self):
        self._client = self._getClient()

    def createServer(self, name, min_ram, image_id=None, image_name=None,
                     az=None, key_name=None, name_filter=None,
                     config_drive=None, nodepool_node_id=None,
                     nodepool_image_name=None,
                     nodepool_snapshot_image_id=None):
        # TODO(mordred): Replace this with zookeeper request
        pass

    def waitForServer(self, server, timeout=3600):
        # TODO(mordred): Replace with zookeeper request
        pass

    def waitForServerDeletion(self, server_id, timeout=600):
        # TODO(mordred): Replace with zookeeper request
        pass

    def listServers(self):
        # Return a [] to short circuit nodepool's cleanup leaked servers logic
        return []

    def cleanupServer(self, server_id):
        # TODO(mordred): Replace with zookeeper request
        pass

    def cleanupLeakedFloaters(self):
        # Do nothing - nodepool v3 will handle this as needed
        pass


class FakeProviderManager(ProviderManager):
    def __init__(self, provider, use_taskmanager):
        self.__client = fakeprovider.FakeOpenStackCloud()
        super(FakeProviderManager, self).__init__(provider, use_taskmanager)

    def _getClient(self):
        return self.__client
