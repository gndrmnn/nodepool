# Copyright 2021 BMW Group
# Copyright 2024 Acme Gating, LLC
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

from kazoo.exceptions import NoNodeError
from kazoo.protocol.states import KazooState
from kazoo.recipe.lock import Lock


class SessionAwareMixin:
    def __init__(self, client, path, identifier=None, extra_lock_patterns=()):
        self._zuul_ephemeral = None
        self._zuul_session_expired = False
        self._zuul_watching_session = False
        self._zuul_seen_contenders = set()
        self._zuul_seen_contender_names = set()
        self._zuul_contender_watch = None
        super().__init__(client, path, identifier, extra_lock_patterns)

    def acquire(self, blocking=True, timeout=None, ephemeral=True):
        ret = super().acquire(blocking, timeout, ephemeral)
        self._zuul_session_expired = False
        if ret and ephemeral:
            self._zuul_ephemeral = ephemeral
            self.client.add_listener(self._zuul_session_watcher)
            self._zuul_watching_session = True
        return ret

    def release(self):
        if self._zuul_watching_session:
            self.client.remove_listener(self._zuul_session_watcher)
            self._zuul_watching_session = False
        return super().release()

    def _zuul_session_watcher(self, state):
        if state == KazooState.LOST:
            self._zuul_session_expired = True

            # Return true to de-register
            return True

    def is_still_valid(self):
        if not self._zuul_ephemeral:
            return True
        return not self._zuul_session_expired

    def watch_for_contenders(self):
        if not self.is_acquired:
            raise Exception("Unable to set contender watch without lock")
        self._zuul_contender_watch = self.client.ChildrenWatch(
            self.path,
            self._zuul_event_watch, send_event=True)

    def _zuul_event_watch(self, children, event=None):
        if not self.is_acquired:
            # Stop watching
            return False
        if children:
            for child in children:
                if child in self._zuul_seen_contenders:
                    continue
                self._zuul_seen_contenders.add(child)
                try:
                    data, stat = self.client.get(self.path + "/" + child)
                    if data is not None:
                        data = data.decode("utf-8")
                        self._zuul_seen_contender_names.add(data)
                except NoNodeError:
                    pass
        return True

    def contender_present(self, name):
        if self._zuul_contender_watch is None:
            raise Exception("Watch not started")
        return name in self._zuul_seen_contender_names


class SessionAwareLock(SessionAwareMixin, Lock):
    pass
