# Copyright 2020 BMW Group
# Copyright 2022 Acme Gating, LLC
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
import sys
import json
import logging
import threading
import time
from collections import defaultdict

from kazoo.exceptions import NoNodeError
from kazoo.protocol.states import EventType

from nodepool.zk import ZooKeeperBase
from nodepool.model_api import MODEL_API


COMPONENTS_ROOT = "/nodepool/components"


class GlobalRegistry:
    def __init__(self):
        self.registry = None

    def create(self, zk_client):
        if not self.registry:
            self.registry = ComponentRegistry(zk_client)
        return self.registry

    def clearRegistry(self):
        self.registry = None

    @property
    def model_api(self):
        return self.registry.model_api


COMPONENT_REGISTRY = GlobalRegistry()


class BaseComponent(ZooKeeperBase):
    """
    Read/write component object.

    This object holds an offline cache of all the component's attributes. In
    case of an failed update to ZooKeeper the local cache will still hold the
    fresh values. Updating any attribute uploads all attributes to ZooKeeper.

    This enables this object to be used as local key/value store even if the
    ZooKeeper connection got lost.

    :arg client ZooKeeperClient: The ZooKeeperClient object to use, or
         None if this should be a read-only component.
    :arg hostname str: The component's hostname (multiple components with
         the same hostname may be registered; the registry will create unique
         nodes for each).
    """

    # Component states
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"

    log = logging.getLogger("nodepool.Component")
    kind = "base"

    def __init__(self, client, hostname, version=None):
        # Ensure that the content is available before setting any other
        # attribute, because our __setattr__ implementation is relying on it.
        self.__dict__["content"] = {
            "hostname": hostname,
            "state": self.STOPPED,
            "kind": self.kind,
            "version": version,
            "model_api": 0,
        }
        super().__init__(client)

        self.path = None
        self._zstat = None
        self.register_lock = threading.Lock()

    def __getattr__(self, name):
        try:
            return self.content[name]
        except KeyError:
            raise AttributeError

    def __setattr__(self, name, value):
        # If the specified attribute is not part of our content dictionary,
        # fall back to the default __settattr__ behaviour.
        if name not in self.content.keys():
            return super().__setattr__(name, value)

        if self.content[name] == value:
            return

        # Set the value in the local content dict
        self.content[name] = value

        with self.register_lock:
            if not self.path:
                self.log.error(
                    "Path is not set on this component, did you forget "
                    "to call register()?"
                )
                return

            # Update the ZooKeeper node
            content = json.dumps(self.content, sort_keys=True).encode("utf-8")
            try:
                zstat = self.kazoo_client.set(
                    self.path, content, version=self._zstat.version
                )
                self._zstat = zstat
            except NoNodeError:
                self.log.error("Could not update %s in ZooKeeper", self)

    def register(self, model_api=MODEL_API):
        self.content['model_api'] = model_api
        with self.register_lock:
            path = "/".join([COMPONENTS_ROOT, self.kind, self.hostname])
            self.log.info("Registering component in ZooKeeper %s", path)
            self.path, self._zstat = self.kazoo_client.create(
                path,
                json.dumps(self.content, sort_keys=True).encode("utf-8"),
                makepath=True,
                ephemeral=True,
                sequence=True,
                # Also return the zstat, which is necessary to successfully
                # update the component.
                include_data=True,
            )

        if not COMPONENT_REGISTRY.registry:
            return

        # Wait 5 seconds for the component to appear in our local
        # cache so that operations which rely on lists of available
        # labels, etc, behave more synchronously.
        for x in range(50):
            registered = set()
            for kind, components in COMPONENT_REGISTRY.registry.all():
                for component in components:
                    registered.add(component.path)
            if self.path in registered:
                return
            time.sleep(0.1)
        self.log.info("Did not see component registration for %s", path)

    def unregister(self):
        with self.register_lock:
            self.log.info("Unregistering component in ZooKeeper %s", self.path)
            self.client.on_connect_listeners.remove(self._onConnect)
            self.client.on_disconnect_listeners.remove(self._onDisconnect)
            self.client.on_reconnect_listeners.remove(self._onReconnect)
            self.kazoo_client.delete(self.path)
            # Break the object so we can't register again
            del self.register_lock

        # Wait 5 seconds for the component to appear in our local
        # cache so that operations which rely on lists of available
        # labels, etc, behave more synchronously.
        for x in range(50):
            registered = set()
            for kind, components in COMPONENT_REGISTRY.registry.all():
                for component in components:
                    registered.add(component.path)
            if self.path not in registered:
                return
            time.sleep(0.1)
        self.log.info("Did not see component unregistration for %s", self.path)

    def _onReconnect(self):
        self.register()

    def updateFromDict(self, data):
        self.content.update(data)

    @classmethod
    def fromDict(cls, client, hostname, data):
        component = cls(client, hostname)
        component.updateFromDict(data)
        return component

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.content}>"


class LauncherComponent(BaseComponent):
    kind = "launcher"


class BuilderComponent(BaseComponent):
    kind = "builder"


# Not a process, but rather a provider-pool within a launcher.
class PoolComponent(BaseComponent):
    kind = "pool"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial_state = {
            "id": None,
            "provider_name": None,
            "supported_labels": [],
            "priority": 100,
            "paused": False,
        }
        self.content.update(self.initial_state)


class ComponentRegistry(ZooKeeperBase):
    """A component registry is organized like:

        /nodepool/components/{kind}/{sequence}

    Where kind corresponds to one of the classes in this module, and
    sequence is a ZK sequence node with a prefix of the hostname in
    order to produce a unique id for multiple identical components
    running on the same host.  An example path:

        /nodepool/components/scheduler/hostname0000000000

    Components are ephemeral nodes, and so if the actual service
    disconnects from ZK, the node will disappear.

    Component objects returned by this class are read-only; updating
    their attributes will not be reflected in ZooKeeper.
    """
    log = logging.getLogger("nodepool.ComponentRegistry")

    COMPONENT_CLASSES = {
        "pool": PoolComponent,
        "launcher": LauncherComponent,
        "builder": BuilderComponent,
    }

    def __init__(self, client):
        super().__init__(client)

        self.client = client
        self._component_tree = None
        # kind -> hostname -> component
        self._cached_components = defaultdict(dict)

        self.model_api = None
        # Have we initialized enough to trust the model_api
        self._init = False
        # If we are already connected when the class is instantiated, directly
        # call the onConnect callback.
        if self.client.connected:
            self._onConnect()

    def _getComponentRoot(self, kind):
        return '/'.join([COMPONENTS_ROOT, kind])

    def _getComponentPath(self, kind, hostname):
        return '/'.join([COMPONENTS_ROOT, kind, hostname])

    def _onConnect(self):
        for kind in self.COMPONENT_CLASSES.keys():
            root = self._getComponentRoot(kind)
            self.kazoo_client.ensure_path(root)
            self.kazoo_client.ChildrenWatch(
                root, self._makeComponentRootWatcher(kind))
        self._init = True
        self._updateMinimumModelApi()

    def _makeComponentRootWatcher(self, kind):
        def watch(children, event=None):
            return self._onComponentRootUpdate(kind, children, event)
        return watch

    def _onComponentRootUpdate(self, kind, children, event):
        for hostname in children:
            component = self._cached_components[kind].get(hostname)
            if not component:
                self.log.info("Noticed new %s component %s", kind, hostname)
                root = self._getComponentPath(kind, hostname)
                self.kazoo_client.DataWatch(
                    root, self._makeComponentWatcher(kind, hostname))

    def _makeComponentWatcher(self, kind, hostname):
        def watch(data, stat, event=None):
            return self._onComponentUpdate(kind, hostname, data, stat, event)
        return watch

    def _onComponentUpdate(self, kind, hostname, data, stat, event):
        if event:
            etype = event.type
        else:
            etype = None
        self.log.debug(
            "Registry %s got event %s for %s %s",
            self, etype, kind, hostname)
        if (etype in (None, EventType.CHANGED, EventType.CREATED) and
            data is not None):

            # Perform an in-place update of the cached component (if any)
            component = self._cached_components.get(kind, {}).get(hostname)
            d = json.loads(data.decode("utf-8"))

            self.log.info(
                "Component %s %s updated: %s",
                kind, hostname, d)

            if component:
                if (stat.version <= component._zstat.version):
                    # Don't update to older data
                    return
                component.updateFromDict(d)
                component._zstat = stat
            else:
                # Create a new component from the data structure
                # Get the correct kind of component
                # TODO (felix): KeyError on unknown component type?
                component_cls = self.COMPONENT_CLASSES[kind]
                # Pass in null ZK client to make a read-only component
                # instance.
                component = component_cls.fromDict(None, hostname, d)
                component.path = self._getComponentPath(kind, hostname)
                component._zstat = stat

            self._cached_components[kind][hostname] = component
            self._updateMinimumModelApi()
        elif (etype == EventType.DELETED or data is None):
            self.log.info(
                "Noticed %s component %s disappeared",
                kind, hostname)
            try:
                del self._cached_components[kind][hostname]
            except KeyError:
                # If it's already gone, don't care
                pass
            self._updateMinimumModelApi()
            # Return False to stop the datawatch
            return False

    def all(self, kind=None):
        """Returns a list of components.

        If kind is None, then a list of tuples is returned, with each tuple
        being (kind, [list of components]).

        :arg kind str: The type of component to look up in the registry, or
            None to return all kinds
        """

        if kind is None:
            return [(kind, list(components.values()))
                    for (kind, components) in self._cached_components.items()]

        # Filter the cached components for the given kind
        return self._cached_components.get(kind, {}).values()

    def getMinimumModelApi(self):
        """Get the minimum model API version of all currently connected
        components"""

        # Start with our own version in case we're the only component
        # and we haven't registered.
        version = MODEL_API
        for kind, components in self.all():
            for component in components:
                version = min(version, component.model_api)
        return version

    def _updateMinimumModelApi(self):
        if not self._init:
            return
        version = self.getMinimumModelApi()
        if version != self.model_api:
            self.log.info(f"System minimum data model version {version}; "
                          f"this component {MODEL_API}")
        if self.model_api is None:
            if version < MODEL_API:
                self.log.info("The data model version of this component is "
                              "newer than the rest of the system; this "
                              "component will operate in compatability mode "
                              "until the system is upgraded")
            elif version > MODEL_API:
                self.log.error("The data model version of this component is "
                               "older than the rest of the system; "
                               "exiting to prevent data corruption")
                sys.exit(1)
        else:
            if version > self.model_api:
                if version > MODEL_API:
                    self.log.info("The data model version of this component "
                                  "is older than other components in the "
                                  "system, so other components will operate "
                                  "in a compability mode; upgrade this "
                                  "component as soon as possible to complete "
                                  "the system upgrade")
                elif version == MODEL_API:
                    self.log.info("The rest of the system has been upgraded "
                                  "to the data model version of this "
                                  "component")
            elif version < self.model_api:
                self.log.error("A component with a data model version older "
                               "than the rest of the system has been started; "
                               "data corruption is very likely to occur.")
                # Should we exit here as well?
        self.model_api = version
