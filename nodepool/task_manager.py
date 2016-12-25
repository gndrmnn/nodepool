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
import requests.exceptions

from shade import task_manager
import stats

class ManagerStoppedException(Exception):
    pass


class Task(task_manager.Task):
    pass


class TaskManager(task_manager.TaskManager, threading.Thread):
    log = logging.getLogger("nodepool.TaskManager")

    def __init__(self, client, name, rate, workers=5):
        super(TaskManager, self).__init__(
            name=name, client=client, workers=workers,
            statsd_prefix='nodepool.task')
        self.daemon = True
        self.queue = Queue.Queue()
        self._running = True
        self.rate = float(rate)
        self.statsd = stats.get_client()

    def stop(self):
        self._running = False
        self.queue.put(None)
        super(TaskManager, self).stop()

    def run(self):
        last_ts = 0
        try:
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
                self.log.debug("Manager %s queue size: %s)" %
                               (self.name, self.queue.qsize()))
                self.run_task(task)
                self.queue.task_done()
        except Exception:
            self.log.exception("Task manager died.")
            raise

    def submitTask(self, task):
        if not self._running:
            raise ManagerStoppedException(
                "Manager %s is no longer running" % self.name)
        self.queue.put(task)
        return task.wait()
