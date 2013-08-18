#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
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

import uuid
import time
import threading
import novaclient


class Dummy(object):
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def delete(self):
        self.manager.delete(self)


class FakeList(object):
    def __init__(self, l):
        self._list = l

    def list(self):
        return self._list

    def find(self, name):
        for x in self._list:
            if x.name == name:
                return x

    def get(self, id):
        for x in self._list:
            if x.id == id:
                return x
        raise novaclient.exceptions.NotFound(404)

    def _finish(self, obj, delay, status):
        time.sleep(delay)
        obj.status = status

    def delete(self, obj):
        self._list.remove(obj)

    def create(self, **kw):
        s = Dummy(id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  addresses=dict(public=[dict(version=4, addr='fake')]),
                  manager=self)
        self._list.append(s)
        t = threading.Thread(target=self._finish, args=(s, 0.5, 'ACTIVE'))
        t.start()
        return s

    def create_image(self, server, name):
        x = self.api.images.create(name=name)
        return x.id


class FakeHTTPClient(object):
    def get(self, path):
        if path == '/extensions':
            return None, dict(extensions=dict())


class FakeClient(object):
    def __init__(self):
        self.flavors = FakeList([Dummy(id='f1', ram=8192)])
        self.images = FakeList([Dummy(id='i1', name='Fake Precise')])
        self.client = FakeHTTPClient()
        self.servers = FakeList([])
        self.servers.api = self

        self.user = 'fake'
        self.password = 'fake'
        self.projectid = 'fake'
        self.service_type = None
        self.service_name = None
        self.region_name = None


class FakeSSHClient(object):
    def ssh(self, description, cmd):
        return True

    def scp(self, src, dest):
        return True


class FakeJenkins(object):
    def __init__(self):
        self._nodes = {}

    def node_exists(self, name):
        return name in self._nodes

    def create_node(self, name, **kw):
        self._nodes[name] = kw

    def delete_node(self, name):
        del self._nodes[name]


FAKE_CLIENT = FakeClient()
