# Copyright 2019 Red Hat
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

import time
import logging
import queue
import threading

from nodepool.driver import Provider
from nodepool.nodeutils import iterate_timeout
import nodepool.exceptions

class Task:
    name = "Task Name"

    def __init__(self, **kw):
        self._wait_event = threading.Event()
        self._exception = None
        self._traceback = None
        self._result = None
        self.args = kw

    def done(self, result):
        self._result = result
        self._wait_event.set()

    def exception(self, e):
        self._exception = e
        self._wait_event.set()

    def wait(self):
        self._wait_event.wait()
        if self._exception:
            raise self._exception
        return self._result

    def run(self, manager):
        try:
            self.done(self.main(manager))
        except Exception as e:
            self.exception(e)

    def main(self, manager):
        pass

class StopTask(Task):
    name = "Stop TaskManager"

    def run(self, manager):
        manager._running = False
        self.done(None)

class RateLimitContextManager:
    def __init__(self, task_manager):
        self.task_manager = task_manager

    def __enter__(self):
        if self.task_manager.last_ts is None:
            return
        while True:
            delta = time.monotonic() - self.task_manager.last_ts
            if delta >= self.task_manager.delta:
                break
            time.sleep(self.task_manager.delta - delta)

    def __exit__(self, etype, value, tb):
        self.task_manager.last_ts = time.monotonic()

class TaskManager:
    log = logging.getLogger("nodepool.driver.taskmanager.TaskManager")

    def __init__(self, name, rate_limit):
        self._running = True
        self.name = name
        self.queue = queue.Queue()
        self.delta = 1.0/rate_limit
        self.last_ts = None

    def rateLimit(self):
        return RateLimitContextManager(self)

    def submitTask(self, task):
        self.queue.put(task)
        return task

    def stop(self):
        self.submitTask(StopTask())

    def run(self):
        try:
            while True:
                task = self.queue.get()
                if not task:
                    if not self._running:
                        break
                    continue
                self.log.debug("Manager %s running task %s (queue %s)" %
                               (self.name, task.name, self.queue.qsize()))
                task.run(self)
                self.queue.task_done()
        except Exception:
            self.log.exception("Task manager died")
            raise


class BaseTaskManagerProvider(Provider):
    """Subclass this to build a Provider with an included taskmanager"""

    log = logging.getLogger("nodepool.driver.taskmanager.TaskManagerProvider")

    def __init__(self, provider):
        self.provider = provider
        self.thread = None
        self.task_manager = TaskManager(provider.name, provider.rate_limit)

    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.thread is None:
            self.log.debug("Starting thread")
            self.thread = threading.Thread(target=self.task_manager.run)
            self.thread.start()

    def stop(self):
        self.log.debug("Stopping")
        if self.thread is not None:
            self.log.debug("Stopping thread")
            self.task_manager.stop()

    def join(self):
        self.log.debug("Joining")
        if self.thread is not None:
            self.thread.join()
