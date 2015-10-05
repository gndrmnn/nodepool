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

import StringIO
import logging
import requests.exceptions
import threading
import time
import uuid

from jenkins import JenkinsException
import shade


class Dummy(object):
    IMAGE = 'Image'
    INSTANCE = 'Instance'
    FLAVOR = 'Flavor'
    KEYPAIR = 'Keypair'

    def __init__(self, kind, **kw):
        self.__kind = kind
        self.__kw = kw
        for k, v in kw.items():
            setattr(self, k, v)
        try:
            if self.should_fail:
                raise shade.OpenStackCloudException('This image has '
                                                    'SHOULD_FAIL set to True.')
        except AttributeError:
            pass

    def __repr__(self):
        args = []
        for k in self.__kw.keys():
            args.append('%s=%s' % (k, getattr(self, k)))
        args = ' '.join(args)
        return '<%s %s %s>' % (self.__kind, id(self), args)

    def __getitem__(self, key):
        return getattr(self, key)

    def get(self, key, default=None):
        if hasattr(self, key):
            return getattr(self, key)
        return default


def fake_get_one_cloud(cloud_config, cloud_kwargs):
    cloud_kwargs['validate'] = False
    return cloud_config.get_one_cloud(**cloud_kwargs)



class FakeOpenStackCloud(object):
    log = logging.getLogger("nodepool.FakeOpenStackCloud")

    def __init__(self, images=None):
        self._image_list = images
        if self._image_list is None:
            self._image_list = [
                Dummy(
                    Dummy.IMAGE,
                    id='fake-image-id',
                    status='READY',
                    name='Fake Precise',
                    metadata={})
            ]
        self._flavor_list = [
            Dummy(Dummy.FLAVOR, id='f1', ram=8192, name='Fake Flavor'),
            Dummy(Dummy.FLAVOR, id='f2', ram=8192, name='Unreal Flavor'),
        ]
        self._server_list = []
        self._keypair_list = []

    def _get(self, name_or_id, instance_list):
        self.log.debug("Get %s in %s" % (name_or_id, repr(instance_list)))
        for instance in instance_list:
            if instance.name == name_or_id or instance.id == name_or_id:
                return instance
        return None

    def _create(
            self, instance_list, instance_type=Dummy.INSTANCE,
            done_status='ACTIVE', **kw):
        should_fail = kw.get('SHOULD_FAIL', '').lower() == 'true'
        nics = kw.get('nics', [])
        addresses = None
        # if keyword 'ipv6-uuid' is found in provider config,
        # ipv6 address will be available in public addr dict.
        for nic in nics:
            if 'ipv6-uuid' not in nic['net-id']:
                continue
            addresses = dict(
                public=[dict(version=4, addr='fake'),
                        dict(version=6, addr='fake_v6')],
                private=[dict(version=4, addr='fake')]
            )
            break
        if not addresses:
            addresses = dict(
                public=[dict(version=4, addr='fake')],
                private=[dict(version=4, addr='fake')]
            )
        s = Dummy(instance_type,
                  id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=addresses,
                  metadata=kw.get('meta', {}),
                  manager=self,
                  key_name=kw.get('key_name', None),
                  should_fail=should_fail)
        instance_list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.1, done_status))
        t.start()
        return s

    def _delete(self, name_or_id, instance_list):
        self.log.debug("Delete from %s" % repr(instance_list))
        instance = None
        for maybe in instance_list:
            if maybe.name == name_or_id or maybe.id == name_or_id:
                instance = maybe
        if instance:
            instance_list.remove(instance)
        self.log.debug("Deleted from %s" % repr(instance_list))

    def _finish(self, obj, delay, status):
        time.sleep(delay)
        obj.status = status

    def create_image(self, **kwargs):
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            done_status='READY', **kwargs)

    def get_image(self, name_or_id):
        return self._get(name_or_id, self._image_list)

    def list_images(self):
        return self._image_list

    def delete_image(self, name_or_id):
        self._delete(name_or_id, self._image_list)

    def create_image_snapshot(self, name, **metadata):
        # XXX : validate metadata?
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            done_status='active', name=name, **metadata)

    def list_flavors(self):
        return self._flavor_list

    def create_keypair(self, name, public_key):
        return self._create(
            self._image_list, instance_type=Dummy.KEYPAIR,
            name=name, public_key=public_key)

    def list_keypairs(self):
        return self._keypair_list

    def delete_keypair(self, name_or_id):
        self._delete(name_or_id, self._keypair_list)

    def _expand_server_vars(self, server):
        server.public_v4 = ''
        server.public_v6 = ''
        server.private_v4 = ''
        for address in server.addresses.get('public', []):
            if address['version'] == 4:
                server.public_v4 = address['addr']
            elif address['version'] == 6:
                server.public_v6 = address['addr']
        for address in server.addresses.get('private', []):
            if address['version'] == 4:
                server.private_v4 = address['addr']
        return server

    def create_server(self, **kw):
        return self._create(self._server_list, **kw)

    def get_server(self, name_or_id):
        return self._get(name_or_id, self._server_list)

    def list_servers(self):
        return self._server_list

    def delete_server(self, name_or_id):
        self._delete(name_or_id, self._server_list)

    def add_ips_to_server(self, server, **kwargs):
        return server


class FakeFile(StringIO.StringIO):
    def __init__(self, path):
        StringIO.StringIO.__init__(self)
        self.__path = path

    def close(self):
        print "Wrote to %s:" % self.__path
        print self.getvalue()
        StringIO.StringIO.close(self)


class FakeSFTPClient(object):
    def open(self, path, mode):
        return FakeFile(path)

    def close(self):
        pass


class FakeSSHClient(object):
    def __init__(self):
        self.client = self

    def ssh(self, description, cmd, output=False):
        return True

    def scp(self, src, dest):
        return True

    def open_sftp(self):
        return FakeSFTPClient()


class FakeJenkins(object):
    def __init__(self, user):
        self._nodes = {}
        self.quiet = False
        self.down = False
        if user == 'quiet':
            self.quiet = True
        if user == 'down':
            self.down = True

    def node_exists(self, name):
        return name in self._nodes

    def create_node(self, name, **kw):
        self._nodes[name] = kw

    def delete_node(self, name):
        del self._nodes[name]

    def get_info(self):
        if self.down:
            raise JenkinsException("Jenkins is down")
        d = {u'assignedLabels': [{}],
             u'description': None,
             u'jobs': [{u'color': u'red',
                        u'name': u'test-job',
                        u'url': u'https://jenkins.example.com/job/test-job/'}],
             u'mode': u'NORMAL',
             u'nodeDescription': u'the master Jenkins node',
             u'nodeName': u'',
             u'numExecutors': 1,
             u'overallLoad': {},
             u'primaryView': {u'name': u'Overview',
                              u'url': u'https://jenkins.example.com/'},
             u'quietingDown': self.quiet,
             u'slaveAgentPort': 8090,
             u'unlabeledLoad': {},
             u'useCrumbs': False,
             u'useSecurity': True,
             u'views': [
                 {u'name': u'test-view',
                  u'url': u'https://jenkins.example.com/view/test-view/'}]}
        return d
