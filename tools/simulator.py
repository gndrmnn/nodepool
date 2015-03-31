#!/usr/bin/env python
#
# Copyright 2015 Hewlett-Packard Development Company, L.P.
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

class Server(object):
    def __init__(self):
        self.state = 'init'
        self.complete_time = 0

    def advance(self, app, elapsed):
        if self.state == 'init':
            #app.queue.append(Task(self, 'create', 21.0))
            app.queue2.append(Task(self, 'create', 21.0))
            self.state = 'create'
            return
        if self.state == 'create':
            app.queue.append(Task(self, 'server-list', 10.4))
            self.state = 'server-list'
            return
        if self.state =='server-list':
            app.queue.append(Task(self, 'fip-create', 3.0))
            self.state = 'fip-create'
            return
        if self.state =='fip-create':
            app.queue.append(Task(self, 'fip-list', 3.5))
            self.state = 'fip-list'
            return
        if self.state =='fip-list':
            app.queue.append(Task(self, 'fip-attach', 2.9))
            self.state = 'fip-attach'
            return
        if self.state =='fip-attach':
            self.complete_time = elapsed + 1043
            self.state = 'run'
            return
        if self.state =='run':
            app.queue.append(Task(self, 'fip-delete', 3.6))
            self.state = 'fip-delete'
            return
        if self.state =='fip-delete':
            #app.queue.append(Task(self, 'delete', 12.4))
            app.queue2.append(Task(self, 'delete', 12.4))
            self.state = 'init'
            return

class Task(object):
    def __init__(self, server, name, time):
        self.server = server
        self.name = name
        self.time = time
        self.start_time = None
        self.end_time = None

    def start(self, time):
        self.start_time = time
        self.end_time = time + self.time

    def complete(self, time):
        if self.end_time is None:
            return False
        if time >= self.end_time:
            return True
        return False

class App(object):
    queue = []
    queue2 = []
    poll_queue = []
    servers = []
    elapsed = 0.0
    run = 0
    prev = ''

    def create_servers(self):
        while len(self.servers) < 100:
            s = Server()
            self.servers.append(s)
            s.advance(self, self.elapsed)

    def main(self):
        self.create_servers()
        while self.elapsed < 3600*24:
            self.elapsed += 0.1
            self.process_queue(self.queue)
            self.process_queue(self.queue2)
            self.complete_runs()
        print self.elapsed, self.run

    def process_queue(self, queue):
        if not queue:
            return
        task = queue[0]
        run = False
        if self.prev == task.name == 'fip-list':
            run = True
        elif self.prev == task.name == 'server-list':
            run = True
        elif task.complete(self.elapsed):
            run = True
        elif task.start_time is None:
            task.start(self.elapsed)
        if run:
            print self.elapsed, task.server, task.name, task.time
            queue.pop(0)
            task.server.advance(self, self.elapsed)
            self.prev = task.name
            if task.name == 'delete':
                self.run += 1

    def complete_runs(self):
        for server in self.servers:
            if server.state != 'run':
                continue
            if self.elapsed > server.complete_time:
                server.advance(self, self.elapsed)

a = App()
a.main()
