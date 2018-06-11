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

import threading
import logging
import queue
import time

from openstack import task_manager as openstack_task_manager

from nodepool import stats


class TaskManager(openstack_task_manager.RateLimitingTaskManager):
    log = logging.getLogger("nodepool.TaskManager")

    def __init__(self, name, rate, workers=5):
        super(TaskManager, self).__init__(
            name=name, client=client, workers=workers)
        self.statsd = stats.get_client()

    def post_run_task(self, elapsed_time, task):
        super(TaskManager, self).post_run_task(elapsed_time, task)
        if self.statsd:
            # nodepool.task.PROVIDER.TASK_NAME
            key = 'nodepool.task.%s.%s' % (self.name, task.name)
            self.statsd.timing(key, int(elapsed_time * 1000))
            self.statsd.incr(key)
