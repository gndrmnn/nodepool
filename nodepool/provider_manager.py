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
import Queue
import logging
import paramiko
import novaclient
import time

import fakeprovider

def iterate_timeout(max_seconds, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(2)
    raise Exception("Timeout waiting for %s" % purpose)

class NotFound(Exception):
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

class CreateServerTask(Task):
    def main(self, client):
        server = client.servers.create(**self.args)
        return server.id

class GetServerTask(Task):
    def getPublicIP(self, server, version=4):
        for addr in server.addresses.get('public', []):
            if type(addr) == type(u''):  # Rackspace/openstack 1.0
                return addr
            if addr['version'] == version:  # Rackspace/openstack 1.1
                return addr['addr']
        for addr in server.addresses.get('private', []):
            # HPcloud
            if (addr['version'] == version and version == 4):
                quad = map(int, addr['addr'].split('.'))
                if quad[0] == 10:
                    continue
                if quad[0] == 192 and quad[1] == 168:
                    continue
                if quad[0] == 172 and (16 <= quad[1] <= 31):
                    continue
                return addr['addr']
        return None

    def main(self, client):
        try:
            server = client.servers.get(self.args['server_id'])
        except novaclient.exceptions.NotFound:
            raise NotFound()
        d = dict(id=server.id,
                 status=server.status,
                 addresses=server.addresses)
        if hasattr(server, 'adminPass'):
            d['admin_pass'] = server.adminPass
        if hasattr(server, 'key_name'):
            d['key_name'] = server.key_name
        if hasattr(server, 'progress'):
            d['progress'] = server.progress
        d['public_v4'] = self.getPublicIP(server)
        return d

class DeleteServerTask(Task):
    def main(self, client):
        client.servers.delete(self.args['server_id'])

class AddKeypairTask(Task):
    def main(self, client):
        client.keypairs.create(**self.args)

class ListKeypairsTask(Task):
    def main(self, client):
        keys = client.keypairs.list()
        return [dict(id=key.id, name=key.name) for
                key in keys]

class DeleteKeypairTask(Task):
    def main(self, client):
        client.keypairs.delete(self.args['name'])

class CreateFloatingIPTask(Task):
    def main(self, client):
        ip = client.floating_ips.create()
        return dict(id=ip.id, ip=ip.ip)

class AddFloatingIPTask(Task):
    def main(self, client):
        client.servers.add_floating_ip(**self.args)

class GetFloatingIPTask(Task):
    def main(self, client):
        ip = client.floating_ips.get(self.args['ip_id'])
        return dict(id=ip.id, ip=ip.ip, instance_id=ip.instance_id)

class ListFloatingIPsTask(Task):
    def main(self, client):
        ips = client.floating_ips.list()
        return [dict(id=ip.id, ip=ip.ip, instance_id=ip.instance_id) for
                ip in ips]

class RemoveFloatingIPTask(Task):
    def main(self, client):
        client.servers.remove_floating_ip(**self.args)

class DeleteFloatingIPTask(Task):
    def main(self, client):
        client.floating_ips.delete(self.args['ip_id'])

class CreateImageTask(Task):
    def main(self, client):
        # This returns an id
        return client.servers.create_image(**self.args)

class GetImageTask(Task):
    def main(self, client):
        try:
            image = client.images.get(**self.args)
        except novaclient.exceptions.NotFound:
            raise NotFound()
        d = dict(id=image.id, status=image.status)
        if hasattr(image, 'progress'):
            d['progress'] = image.progress
        return d

class FindImageTask(Task):
    def main(self, client):
        image = client.images.find(**self.args)
        return dict(id=image.id)

class DeleteImageTask(Task):
    def main(self, client):
        client.images.delete(**self.args)

class ProviderManager(threading.Thread):
    log = logging.getLogger("nodepool.ProviderManager")

    def __init__(self, provider):
        threading.Thread.__init__(self)
        self.queue = Queue.Queue()
        self._running = True
        self.provider = provider
        self._client = self._getClient()
        self._flavors = self._getFlavors()
        self._images = {}
        self._extensions = self._getExtensions()
        self._rate = 1.0  # 1 second between calls

    def _getClient(self):
        args = ['1.1', self.provider.username, self.provider.password,
                self.provider.project_id, self.provider.auth_url]
        kwargs = {}
        if self.provider.service_type:
            kwargs['service_type'] = self.provider.service_type
        if self.provider.service_name:
            kwargs['service_name'] = self.provider.service_name
        if self.provider.region_name:
            kwargs['region_name'] = self.provider.region_name
        if self.provider.auth_url == 'fake':
            return fakeprovider.FAKE_CLIENT
        return novaclient.client.Client(*args, **kwargs)

    def _getFlavors(self):
        l = [dict(id=f.id, ram=f.ram) for f in self._client.flavors.list()]
        l.sort(lambda a, b: cmp(a['ram'], b['ram']))
        return l

    def _getExtensions(self):
        try:
            resp, body = self._client.client.get('/extensions')
            return [x['alias'] for x in body['extensions']]
        except novaclient.exceptions.NotFound:
            return []

    def hasExtension(self, extension):
        if extension in self._extensions:
            return True
        return False

    def stop(self):
        self._running = False
        self.queue.put(None)

    def run(self):
        last_ts = 0
        while self._running:
            task = self.queue.get()
            if not task:
                continue
            while True:
                delta = time.time() - last_ts
                if delta >= self._rate:
                    break
                time.sleep(self._rate-delta)
            self.log.debug("Provider %s running task %s" % (self.provider.name,
                                                            task))
            task.run(self._client)
            last_ts = time.time()
            self.queue.task_done()

    def submitTask(self, task):
        self.queue.put(task)
        return task.wait()

    def findFlavor(self, min_ram):
        for f in self._flavors:
            if f['ram'] >= min_ram:
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def findImage(self, name):
        if name in self._images:
            return self._images[name]
        image = self.submitTask(FindImageTask(name=name))
        self._images[name] = image
        return image

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]
        return self.submitTask(DeleteImageTask(image=name))

    def addKeypair(self, name):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()
        self.submitTask(AddKeypairTask(name=name, public_key=public_key))
        return key

    def listKeypairs(self):
        return self.submitTask(ListKeypairsTask())

    def deleteKeypair(self, name):
        return self.submitTask(DeleteKeypairTask(name=name))

    def createServer(self, name, min_ram, image_id=None,
                     image_name=None, key_name=None):
        if image_name:
            image_id = self.findImage(image_name)['id']
        flavor = self.findFlavor(min_ram)
        create_args = dict(name=name, image=image_id, flavor=flavor['id'])
        if key_name:
            create_args['key_name'] = key_name

        return self.submitTask(CreateServerTask(**create_args))

    def getServer(self, server_id):
        return self.submitTask(GetServerTask(server_id=server_id))

    def getFloatingIP(self, ip_id):
        return self.submitTask(GetFloatingIPTask(ip_id=ip_id))

    def _waitForResource(self, resource_type, resource_id, timeout):
        last_progress = None
        last_status = None
        for count in iterate_timeout(timeout,
                                     "waiting for %s %s" % (resource_type,
                                                            resource_id)):
            try:
                if resource_type == 'server':
                    resource = self.getServer(resource_id)
                elif resource_type == 'image':
                    resource = self.getImage(resource_id)
            except:
                self.log.exception('Unable to list %ss while waiting for '
                                   '%s will retry' % (resource_type,
                                                      resource_id))
                continue

            # In Rackspace v1.0, there is no progress attribute while queued
            progress = resource.get('progress')
            status = resource.get('status')
            if (last_progress != progress or
                last_status != status):
                self.log.debug('Status of %s %s: %s %s' %
                               (resource_type, resource_id,
                                status, progress))
            last_status = status
            last_progress = progress
            if status == 'ACTIVE':
                return resource

    def waitForServer(self, server_id, timeout=3600):
        return self._waitForResource('server', server_id, timeout)

    def waitForImage(self, image_id, timeout=3600):
        return self._waitForResource('image', image_id, timeout)

    def createFloatingIP(self):
        return self.submitTask(CreateFloatingIPTask())

    def addFloatingIP(self, server_id, address):
        self.submitTask(AddFloatingIPTask(server=server_id,
                                          address=address))

    def addPublicIP(self, server_id):
        ip = self.createFloatingIP()
        self.addFloatingIP(server_id, ip['ip'])
        for count in iterate_timeout(600, "ip to be added"):
            try:
                newip = self.getFloatingIP(ip['id'])
            except Exception:
                self.log.exception('Unable to get IP details for server %s, '
                                   'will retry' % (server_id))
                continue
            if newip['instance_id'] == server_id:
                return newip['ip']

    def createImage(self, server_id, image_name):
        return self.submitTask(CreateImageTask(server=server_id,
                                               image_name=image_name))

    def getImage(self, image_id):
        return self.submitTask(GetImageTask(image=image_id))

    def listFloatingIPs(self):
        return self.submitTask(ListFloatingIPsTask())

    def removeFloatingIP(self, server_id, address):
        return self.submitTask(RemoveFloatingIPTask(server=server_id,
                                                    address=address))

    def deleteFloatingIP(self, ip_id):
        return self.submitTask(DeleteFloatingIPTask(floating_ip=ip_id))

    def deleteServer(self, server_id):
        return self.submitTask(DeleteServerTask(server_id=server_id))

    def cleanupServer(self, server_id):
        server = self.getServer(server_id)

        if self.hasExtension('os-floating-ips'):
            for ip in self.listFloatingIPs():
                if ip['instance_id'] == server_id:
                    self.log.debug('Deleting floating ip for server %s' %
                                   server_id)
                    self.removeFloatingIP(server_id, ip['ip'])
                    self.deleteFloatingIP(ip['id'])

        if self.hasExtension('os-keypairs'):
            for kp in self.listKeyPairs():
                if kp['name'] == server['key_name']:
                    self.log.debug('Deleting keypair for server %s' % server_id)
                    self.deleteKeypair(kp['name'])

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)

        for count in iterate_timeout(3600, "waiting for server %s deletion" %
                                     server_id):
            try:
                self.getServer(server_id)
            except NotFound:
                return

