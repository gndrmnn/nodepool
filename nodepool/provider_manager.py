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

import logging
import paramiko
import shade
import threading
import time

import fakeprovider
from task_manager import Task, TaskManager, ManagerStoppedException


SERVER_LIST_AGE = 5   # How long to keep a cached copy of the server list
IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list
ITERATE_INTERVAL = 2  # How long to sleep while waiting for something
                      # in a loop


def iterate_timeout(max_seconds, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(ITERATE_INTERVAL)
    raise Exception("Timeout waiting for %s" % purpose)


class NotFound(Exception):
    pass


class CreateServerTask(Task):
    def main(self, client):
        server = client.create_server(auto_ip=True,
            wait=True, **self.args)
        return str(server.id)


class GetServerTask(Task):
    def main(self, client):
        server = client.get_server_dict(self.args['server_id'])
        if not server:
            raise NotFound()
        return server


class DeleteServerTask(Task):
    def main(self, client):
        client.delete_server(self.args['server_id'])


class ListServersTask(Task):
    def main(self, client):
        return client.list_server_dicts()


class AddKeypairTask(Task):
    def main(self, client):
        return client.create_keypair(**self.args)


class ListKeypairsTask(Task):
    def main(self, client):
        return client.list_keypair_dicts()


class DeleteKeypairTask(Task):
    def main(self, client):
        client.delete_keypair(self.args['name'])


class CreateImageTask(Task):
    def main(self, client):
        # This returns an id
        return str(client.servers.create_image(**self.args))


class GetImageTask(Task):
    def main(self, client):
        image = client.get_image_dict(**self.args)
        if not image:
            raise NotFound()
        return image


class GetFlavorByRamTask(Task):
    def main(self, client):
        return client.get_flavor_by_ram(min_ram=args['min_ram'],
            include=args['name_filter'])


def make_image_dict(image):
    d = dict(id=str(image.id), name=image.name, status=image.status,
             metadata=image.metadata)
    if hasattr(image, 'progress'):
        d['progress'] = image.progress
    return d


class ListImagesTask(Task):
    def main(self, client):
        images = client.images.list()
        return [make_image_dict(image) for image in images]


class FindImageTask(Task):
    def main(self, client, exclude=None):
        return client.get_image_dict(**self.args)


class DeleteImageTask(Task):
    def main(self, client):
        client.images.delete(**self.args)


class FindNetworkTask(Task):
    def main(self, client):
        network = client.tenant_networks.find(**self.args)
        return dict(id=str(network.id))


class ProviderManager(TaskManager):
    log = logging.getLogger("nodepool.ProviderManager")

    def __init__(self, provider):
        super(ProviderManager, self).__init__(None, provider.name,
                                              provider.rate)
        self.provider = provider
        self._client = self._getClient()
        self._images = {}
        self._networks = {}
        self._cloud_metadata_read = False
        self._servers = []
        self._servers_time = 0
        self._servers_lock = threading.Lock()
        self._ips = []
        self._ips_time = 0
        self._ips_lock = threading.Lock()

    def _getCloudMetadata(self):
        self._cloud_metadata_read = True

    def _getClient(self):
        kwargs = dict(
            username=self.provider.username,
            password=self.provider.password,
            project_name=self.provider.project_id,
            auth_url=self.provider.auth_url,
        )
        if self.provider.service_type:
            kwargs['service_type'] = self.provider.service_type
        if self.provider.service_name:
            kwargs['service_name'] = self.provider.service_name
        if self.provider.region_name:
            kwargs['region_name'] = self.provider.region_name
        if self.provider.api_timeout:
            kwargs['timeout'] = self.provider.api_timeout
        if self.provider.auth_url == 'fake':
            return fakeprovider.FAKE_CLIENT
        return shade.openstack_cloud(**kwargs)

    def findFlavor(self, min_ram, name_filter=None):
        return self.submitTask(GetFlavorByRamTask(min_ram,
                                               name_filter=name_filter))

    def findImage(self, name_or_id, exclude=None):
        return self.submitTask(FindImageTask(name_or_id=name_or_id,
                                             exclude=exclude))

    def findNetwork(self, label):
        if label in self._networks:
            return self._networks[label]
        network = self.submitTask(FindNetworkTask(label=label))
        self._networks[label] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]
        return self.submitTask(DeleteImageTask(image=name))

    def ensureKeypair(self, key_name, hostname):
        if key_name:
            return key_name, None, False
        else:
            key_name = hostname.split('.')[0]
            key = self.addKeypair(key_name)
            return key_name, key, False

    def addKeypair(self, name):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()
        self.submitTask(AddKeypairTask(name=name, public_key=public_key))
        return key

    def listKeypairs(self):
        return self.submitTask(ListKeypairsTask())

    def deleteKeypair(self, name):
        return self.submitTask(DeleteKeypairTask(name=name))

    def deleteFailedKeypair(self, name):
        for kp in self.listKeypairs():
            if kp['name'] == name:
                self.deleteKeypair(name)
                break

    def createServer(self, name, min_ram, image_name_or_id,
                     az=None, key_name=None, name_filter=None, exclude=None):
        image_id = self.findImage(
            name_or_id=image_name_or_id, exclude=exclude)['id']
        flavor = self.findFlavor(min_ram, name_filter=name_filter)
        create_args = dict(name=name, image=image_id, flavor=flavor.id)
        if key_name:
            create_args['key_name'] = key_name
        if az:
            create_args['availability_zone'] = az
        if self.provider.use_neutron:
            nics = []
            for network in self.provider.networks:
                if 'net-id' in network:
                    nics.append({'net-id': network['net-id']})
                elif 'net-label' in network:
                    net_id = self.findNetwork(network['net-label'])['id']
                    nics.append({'net-id': net_id})
                else:
                    raise Exception("Invalid 'networks' configuration.")
            create_args['nics'] = nics
        if self.provider.pool:
            create_args['pool'] = self.provider.pool

        return self.submitTask(CreateServerTask(**create_args))

    def getServer(self, server_id):
        return self.submitTask(GetServerTask(server_id=server_id))

    def getServerFromList(self, server_id):
        for s in self.listServers():
            if s['id'] == server_id:
                return s
        raise NotFound()

    def _waitForResource(self, resource_type, resource_id, timeout):
        last_status = None
        for count in iterate_timeout(timeout,
                                     "%s %s in %s" % (resource_type,
                                                      resource_id,
                                                      self.provider.name)):
            try:
                if resource_type == 'server':
                    resource = self.getServerFromList(resource_id)
                elif resource_type == 'image':
                    resource = self.getImage(resource_id)
            except NotFound:
                continue
            except ManagerStoppedException:
                raise
            except Exception:
                self.log.exception('Unable to list %ss while waiting for '
                                   '%s will retry' % (resource_type,
                                                      resource_id))
                continue

            status = resource.get('status')
            if (last_status != status):
                self.log.debug(
                    'Status of {type} in {provider} {id}: {status}'.format(
                        type=resource_type,
                        provider=self.provider.name,
                        id=resource_id,
                        status=status))
            last_status = status
            if status in ['ACTIVE', 'ERROR']:
                return resource

    def waitForServer(self, server_id, timeout=3600):
        return self._waitForResource('server', server_id, timeout)

    def waitForServerDeletion(self, server_id, timeout=600):
        for count in iterate_timeout(600, "server %s deletion in %s" %
                                     (server_id, self.provider.name)):
            try:
                self.getServerFromList(server_id)
            except NotFound:
                return

    def waitForImage(self, image_id, timeout=3600):
        if image_id == 'fake-glance-id':
            return True
        return self._waitForResource('image', image_id, timeout)

    def createImage(self, server_id, image_name, meta):
        return self.submitTask(CreateImageTask(server=server_id,
                                               image_name=image_name,
                                               metadata=meta))

    def getImage(self, image_id):
        return self.submitTask(GetImageTask(image=image_id))

    def uploadImage(self, image_name, filename, disk_format, container_format,
                    meta):
        if image_name.startswith('fake-'):
            image = fakeprovider.FakeGlanceClient()
            image.update(data='fake')
        else:
            # upload image using shade wrapper
            image = self.client.create_image(
                image_name,
                filename,
                is_public=False,
                disk_format=disk_format,
                container_format=container_format,
                **meta)
        return image.id

    def listServers(self):
        if time.time() - self._servers_time >= SERVER_LIST_AGE:
            # Since we're using cached data anyway, we don't need to
            # have more than one thread actually submit the list
            # servers task.  Let the first one submit it while holding
            # a lock, and the non-blocking acquire method will cause
            # subsequent threads to just skip this and use the old
            # data until it succeeds.
            if self._servers_lock.acquire(False):
                try:
                    self._servers = self.submitTask(ListServersTask())
                    self._servers_time = time.time()
                finally:
                    self._servers_lock.release()
        return self._servers

    def deleteServer(self, server_id):
        return self.submitTask(DeleteServerTask(server_id=server_id))

    def cleanupServer(self, server_id):
        done = False
        while not done:
            try:
                server = self.getServerFromList(server_id)
                done = True
            except NotFound:
                # If we have old data, that's fine, it should only
                # indicate that a server exists when it doesn't; we'll
                # recover from that.  However, if we have no data at
                # all, wait until the first server list task
                # completes.
                if self._servers_time == 0:
                    time.sleep(SERVER_LIST_AGE + 1)
                else:
                    done = True

        # This will either get the server or raise an exception
        server = self.getServerFromList(server_id)

        if (self._client.has_extension('os-keypairs') and
            server['key_name'] != self.provider.keypair):
            for kp in self.listKeypairs():
                if kp['name'] == server['key_name']:
                    self.log.debug('Deleting keypair for server %s' %
                                   server_id)
                    self.deleteKeypair(kp['name'])

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)
