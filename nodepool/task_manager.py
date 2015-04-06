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
from statsd import statsd


class ManagerStoppedException(Exception):
    pass


class Task(object):

    def __init__(self, **kw):
        self._wait_event = threading.Event()
        self._exception = None
        self._traceback = None
        self._result = None
        self.args = kw

    def done(self, result):
        self._result = result
        self._wait_event.set()

    def exception(self, e, tb):
        self._exception = e
        self._traceback = tb
        self._wait_event.set()

    def wait(self):
        self._wait_event.wait()
        if self._exception:
            raise self._exception, None, self._traceback
        return self._result

    def run(self, client):
        try:
            self.done(self.main(client))
        except Exception as e:
            self.exception(e, sys.exc_info()[2])


class TaskWorker(threading.Thread):
    log = logging.getLogger("nodepool.TaskWorker")

    def __init__(self, name, rate, queue):
        super(TaskWorker, self).__init__(name=name)
        self.daemon = True
        self.queue = queue
        self._running = True
        self.name = name
        self.rate = float(rate)

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
            self.log.debug("Task worker %s running task %s (queue: %s)" %
                           (self.name, task, self.queue.qsize()))
            start = time.time()
            task.run(self._client)
            last_ts = time.time()
            dt = last_ts - start
            self.log.debug("Task worker %s ran task %s in %ss" %
                           (self.name, task, dt))
            if statsd:
                #nodepool.task.PROVIDER.subkey
                subkey = type(task).__name__
                key = 'nodepool.task.%s.%s' % (self.name, subkey)
                statsd.timing(key, dt)
                statsd.incr(key)

            self.queue.task_done()


class TaskManager(object):
    log = logging.getLogger("nodepool.TaskManager")

    def __init__(self, client, name, rate):
        self.main_queue = Queue.Queue()
        self.second_queue = Queue.Queue()
        self._slow_tasks = [ 'CreateServerTask', 'DeleteServerTask', ]

        self.main_worker = TaskWorker(
            name + '_main', rate, self.main_queue)
        self.second_worker = TaskWorker(
            name + '_second', rate, self.second_queue)
        self.name = name
        self._client = None
        self.setClient(client)

    def setClient(self, client):
        self._client = client
        self.main_worker._client = client
        self.second_worker._client = client

    def start(self):
        self.main_worker.start()
        self.second_worker.start()

    def submitTask(self, task):
        if not self.main_worker._running or not self.second_worker._running:
            raise ManagerStoppedException(
                "Task worker(s) for task Manager %s no longer running" % self.name)

        # add to queues depending on election
        if type(task).__name in self._slow_tasks:
            self.second_queue.put(task)
        else:
            self.main_queue.put(task)
        return task.wait()
