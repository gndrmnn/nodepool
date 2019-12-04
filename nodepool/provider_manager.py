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

import logging

from nodepool.driver import Drivers


def get_provider(provider):
    driver = Drivers.get(provider.driver.name)
    return driver.getProvider(provider)


class ProviderManager(object):
    '''
    A class to handle management of objects subclassed from the Provider class.

    This is a singleton class, implemented as a pattern suggested from:

        https://python-3-patterns-idioms-test.readthedocs.io/

    '''

	# Our singleton instance
	instance = None

	def __new__(cls):
	    if not ProviderManager.instance:
	        ProviderManager.instance = ProviderManager.__ProviderManager()
	    return ProviderManager.instance

	# Redirect attribute access to our private singleton object.
	def __getattr__(self, name):
		return getattr(self.instance, name)

	def __setattr__(self, name):
		return setattr(self.instance, name)

	# Private inner class acting as the singleton
	class __ProviderManager:
        log = logging.getLogger("nodepool.ProviderManager")

	    def __init__(self):
			self.managers = {}

		def addProvider(self, provider_config, zk_conn):
			'''
			Add a new Provider object to the manager and starts it.

			If the given provider is already being managed, it is simply
			replaced.

			:param dict provider_config: The provider configuration as read
			    from the nodepool configuration file.
			:param ZooKeeper zk_conn: A ZooKeeper connection object.
			'''
			name = provider_config.name
			self.log.debug("Creating new ProviderManager object for %s", name)
			self.managers[name] = get_provider(provider_config)
			self.managers[name].start(zk_conn)

		def getProvider(self, provider_name):
			'''
			Get the Provider object with the given provider name.

			:param str provider_name: The name of the provider as defined in
			    the nodepool configuration file.
			'''
		    return self.managers.get(provider_name)

		def stopProviders(self, name_list=None):
			'''
			Stop providers that we are currently managing.

			Stopping a provider effectively stops us from managing it since
			it will be removed from our manage list.

			:param list name_list: A list of provider names to stop. If not
			    supplied, all currently managed providers are stopped.
			'''
			if not name_list:
				name_list = self.managers.keys()

			for name in name_list:
				provider = self.getProvider(name)
				provider.stop()
				provider.join()
				del self.managers[name]

		def reconfigure(new_config, zk_conn, only_image_manager=False):
			'''
			Reconfigure the provider managers on any configuration changes.

			If a provider configuration changes, stop the current provider
			manager we have cached and replace it with a new one.

			:param Config new_config: The newly read configuration.
			:param ZooKeeper zk_conn: A ZooKeeper connection object.
			:param bool only_image_manager: If True, skip managers that do not
			    manage images. This is used by the builder process.
			'''
			stop_list = []
			restart_list = []

			for provider_cfg in new_config.providers.values():
				if only_image_manager and not provider_cfg.manage_images:
					continue

				# New provider
				if provider_cfg.name not in self.managers:
					self.addProvider(provider_cfg, zk_conn)
					continue

				# Existing provider, but different configuration.
				# Each Provider object has a copy of it's configuration in its
				# 'provider' attribute.
				provider = self.getProvider(provider_cfg.name)
				if provider_cfg != provider.provider:
				    restart_list.append(provider_cfg)

			# Stop managing any providers that have been totally removed.
			new_providers = [p.name for p in new_config.providers.values()]
			for name in self.managers.keys():
				if name not in new_providers:
					stop_list.append(name)

			# Stop and delete any providers that have been removed or changed.
			if stop_list or restart_list:
				restart_names = [p.name for p in restart_list]
				self.stopProviders(stop_list + restart_names)

			# Add back the providers that have changed.
			for config in restart_list:
				self.addProvider(config, zk_conn)
