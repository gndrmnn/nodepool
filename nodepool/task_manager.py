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

import sys
import threading
from six.moves import queue as Queue
import logging
import time


class ManagerStoppedException(Exception):
    pass


class Task(object):
    def __init__(self, **kw):
        self._wait_event = threading.Event()
        self.args = kw

    def enable(self):
        self._wait_event.set()

    def wait(self):
        self._wait_event.wait()

    def run(self, client):
        return self.main(client)


class TaskManager(threading.Thread):
    log = logging.getLogger("nodepool.TaskManager")

    def __init__(self, client, name, rate):
        super(TaskManager, self).__init__(name=name)
        self.daemon = True
        self.queue = Queue.Queue()
        self._running = True
        self.name = name
        self.rate = float(rate)
        self._client = None

    def stop(self):
        self._running = False
        self.queue.put(None)

    def run(self):
        last_ts = 0
        while True:
            task = self.queue.get()
            if not task:
                if not self._running:
                    break
                continue
            while True:
                delta = time.time() - last_ts
                if delta >= self.rate:
                    break
                time.sleep(self.rate - delta)
            task.enable()
            last_ts = time.time()
            self.queue.task_done()

    def submitTask(self, task):
        if not self._running:
            raise ManagerStoppedException(
                "Manager %s is no longer running" % self.name)
        self.queue.put(task)
        task.wait()
        self.log.debug("Manager %s running task %s (queue: %s)" %
                       (self.name, task, self.queue.qsize()))
        start = time.time()
        ret = task.run(self._client)
        end = time.time()
        self.log.debug("Manager %s ran task %s in %ss" %
                       (self.name, task, (end - start)))
        return ret
