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


class Task:
    """Base task class for use with :py:class:`TaskManager`

    Subclass this to implement your own tasks.

    Set the `name` field to the name of your task and override the
    :py:meth:`main` method.

    Keyword arguments to the constructor are stored on `self.args` for
    use by the :py:meth:`main` method.

    """
    name = "task_name"

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
        """Call this method after submitting the task to the TaskManager to
        receieve the results."""
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
        """Implement the work of the task

        :param TaskManager manager: The instance of
            :py:class:`TaskManager` running this task.

        Arguments passed to the constructor are available as `self.args`.
        """
        pass


class StopTask(Task):
    name = "stop_taskmanager"

    def main(self, manager):
        manager._running = False


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
    """A single-threaded task dispatcher

    This class is meant to be instantiated by a Provider in order to
    execute remote API calls from a single thread with rate limiting.

    :param str name: The name of the TaskManager (usually the provider name)
        used in logging.
    :param float rate_limit: The rate limit of the task manager expressed in
        requests per second.
    """
    log = logging.getLogger("nodepool.driver.taskmanager.TaskManager")

    def __init__(self, name, rate_limit):
        self._running = True
        self.name = name
        self.queue = queue.Queue()
        self.delta = 1.0 / rate_limit
        self.last_ts = None

    def rateLimit(self):
        """Return a context manager to perform rate limiting.  Use as follows:

        .. code: python

           with task_manager.rateLimit():
               <execute API call>
        """
        return RateLimitContextManager(self)

    def submitTask(self, task):
        """Submit a task to the task manager.

        :param Task task: An instance of a subclass of :py:class:`Task`.
        :returns: The submitted task for use in function chaning.
        """
        self.queue.put(task)
        return task

    def stop(self):
        """Stop the task manager."""
        self.submitTask(StopTask())

    def run(self):
        try:
            while True:
                task = self.queue.get()
                if not task:
                    continue
                self.log.debug("Manager %s running task %s (queue %s)" %
                               (self.name, task.name, self.queue.qsize()))
                task.run(self)
                self.queue.task_done()
                if not self._running:
                    break
        except Exception:
            self.log.exception("Task manager died")
            raise


class BaseTaskManagerProvider(Provider):
    """Subclass this to build a Provider with an included taskmanager"""

    log = logging.getLogger("nodepool.driver.taskmanager.TaskManagerProvider")

    def __init__(self, provider):
        super().__init__()
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
