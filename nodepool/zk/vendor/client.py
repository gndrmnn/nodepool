# This file is derived from the Kazoo project
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

from collections import defaultdict

from kazoo.client import (
    _prefix_root,
    KazooClient,
)

from nodepool.zk.vendor.serialization import AddWatch


class ZuulKazooClient(KazooClient):
    def __init__(self, *args, **kw):
        self._persistent_watchers = defaultdict(set)
        super().__init__(*args, **kw)

    def add_watch(self, path, watch, mode):
        """Add a watch.

        This method adds persistent watches.  Unlike the data and
        child watches which may be set by calls to
        :meth:`KazooClient.exists`, :meth:`KazooClient.get`, and
        :meth:`KazooClient.get_children`, persistent watches are not
        removed after being triggered.

        To remove a persistent watch, use
        :meth:`KazooClient.remove_all_watches` with an argument of
        :attr:`~kazoo.states.WatcherType.ANY`.

        The `mode` argument determines whether or not the watch is
        recursive.  To set a persistent watch, use
        :class:`~kazoo.states.AddWatchMode.PERSISTENT`.  To set a
        persistent recursive watch, use
        :class:`~kazoo.states.AddWatchMode.PERSISTENT_RECURSIVE`.

        :param path: Path of node to watch.
        :param watch: Watch callback to set for future changes
                      to this path.
        :param mode: The mode to use.
        :type mode: int

        :raises:
            :exc:`~kazoo.exceptions.MarshallingError` if mode is
            unknown.

            :exc:`~kazoo.exceptions.ZookeeperError` if the server
            returns a non-zero error code.
        """
        return self.add_watch_async(path, watch, mode).get()

    def add_watch_async(self, path, watch, mode):
        """Asynchronously add a watch. Takes the same arguments as
        :meth:`add_watch`.
        """
        if not isinstance(path, str):
            raise TypeError("Invalid type for 'path' (string expected)")
        if not callable(watch):
            raise TypeError("Invalid type for 'watch' (must be a callable)")
        if not isinstance(mode, int):
            raise TypeError("Invalid type for 'mode' (int expected)")

        async_result = self.handler.async_result()
        self._call(
            AddWatch(_prefix_root(self.chroot, path), watch, mode),
            async_result,
        )
        return async_result
