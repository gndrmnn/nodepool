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

import json
import logging
import paramiko

import threading
import time
import requests.exceptions
import sys

import shade

from nodeutils import iterate_timeout
from task_manager import TaskManager, ManagerStoppedException


SERVER_LIST_AGE = 5   # How long to keep a cached copy of the server list
IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list


def get_public_ip(server, version=4):
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


def get_private_ip(server):
    ret = []
    for (name, network) in server.addresses.iteritems():
        if name == 'private':
            ret.extend([addrs['addr']
                        for addrs in network if addrs['version'] == 4])
        else:
            for interface_spec in network:
                if interface_spec['version'] != 4:
                    continue
                if ('OS-EXT-IPS:type' in interface_spec
                        and interface_spec['OS-EXT-IPS:type'] == 'fixed'):
                    ret.append(interface_spec['addr'])
    if not ret:
        if server.status == 'ACTIVE':
            # Server expected to have at least one address in ACTIVE status
            # TODO: uncomment this code when all nodes have private IPs
            # raise KeyError('No private ip found for server')
            return None
        else:
            return None
    return ret[0]


def make_image_dict(image):
    d = dict(id=str(image.id), name=image.name, status=image.status,
             metadata=image.metadata)
    if hasattr(image, 'progress'):
        d['progress'] = image.progress
    return d


class NotFound(Exception):
    pass


class ProviderManager(TaskManager):
    log = logging.getLogger("nodepool.ProviderManager")

    def __init__(self, provider):
        super(ProviderManager, self).__init__(None, provider.name,
                                              provider.rate)
        self.provider = provider
        self.resetClient()
        self._images = {}
        self._networks = {}
        self._cloud_metadata_read = False
        self.__flavors = {}
        self.__extensions = {}
        self._servers = []
        self._servers_time = 0
        self._servers_lock = threading.Lock()
        self._ips = []
        self._ips_time = 0
        self._ips_lock = threading.Lock()

    @property
    def _flavors(self):
        if not self._cloud_metadata_read:
            self._getCloudMetadata()
        return self.__flavors

    @property
    def _extensions(self):
        if not self._cloud_metadata_read:
            self._getCloudMetadata()
        return self.__extensions

    def _getCloudMetadata(self):
        self.__flavors = self._getFlavors()
        self._cloud_metadata_read = True

    def _getClient(self):
        return shade.OpenStackCloud(
            cloud_config=self.provider.cloud_config,
            manager=self,
            **self.provider.cloud_config.config)

    def runTask(self, task):
        # Run the given task in the TaskManager passed to shade. It turns
        # out that this provider manager is the TaskManager we pass, so
        # this is a way of running each cloud operation in its own thread
        task.run(self._client)

    def resetClient(self):
        self._client = self._getClient()

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(lambda a, b: cmp(a['ram'], b['ram']))
        return flavors

    def findFlavor(self, min_ram, name_filter=None):
        # Note: this will throw an error if the provider is offline
        # but all the callers are in threads (they call in via CreateServer) so
        # the mainloop won't be affected.
        for f in self._flavors:
            if (f['ram'] >= min_ram
                    and (not name_filter or name_filter in f['name'])):
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def findImage(self, name):
        if name in self._images:
            return self._images[name]
        image = self._client.get_image(name)
        self._images[name] = image
        return image

    def findNetwork(self, label):
        if label in self._networks:
            return self._networks[label]
        network = self._client.get_network(label)
        self._networks[label] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]
        return self._client.delete_image(name)

    def addKeypair(self, name):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()
        self._client.create_keypair(name=name, public_key=public_key)
        return key

    def listKeypairs(self):
        return self._client.list_keypairs()

    def deleteKeypair(self, name):
        return self._client.delete_keypair(name_or_id=name)

    def createServer(self, name, min_ram, image_id=None, image_name=None,
                     az=None, key_name=None, name_filter=None,
                     config_drive=None, nodepool_node_id=None,
                     nodepool_image_name=None,
                     nodepool_snapshot_image_id=None):
        if image_name:
            image_id = self.findImage(image_name)['id']
        flavor = self.findFlavor(min_ram, name_filter)
        create_args = dict(name=name, image=image_id, flavor=flavor['id'],
                           config_drive=config_drive)
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
        # Put provider.name and image_name in as groups so that ansible
        # inventory can auto-create groups for us based on each of those
        # qualities
        # Also list each of those values directly so that non-ansible
        # consumption programs don't need to play a game of knowing that
        # groups[0] is the image name or anything silly like that.
        nodepool_meta = dict(provider_name=self.provider.name)
        groups_meta = [self.provider.name]
        if nodepool_node_id:
            nodepool_meta['node_id'] = nodepool_node_id
        if nodepool_snapshot_image_id:
            nodepool_meta['snapshot_image_id'] = nodepool_snapshot_image_id
        if nodepool_image_name:
            nodepool_meta['image_name'] = nodepool_image_name
            groups_meta.append(nodepool_image_name)
        create_args['meta'] = dict(
            groups=json.dumps(groups_meta),
            nodepool=json.dumps(nodepool_meta)
        )

        return self._client.get_openstack_vars(self._client.create_server(
            wait=False, auto_ip=False, **create_args))

    def getServer(self, server_id):
        return self._client.get_server(server_id)

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

            status = resource['status']
            if (last_status != status):
                self.log.debug(
                    'Status of {type} in {provider} {id}: {status}'.format(
                        type=resource_type,
                        provider=self.provider.name,
                        id=resource_id,
                        status=status))
            last_status = status
            if status in ['READY', 'ACTIVE', 'ERROR']:
                return resource

    def waitForServer(self, server_id, timeout=3600):
        server = self._waitForResource('server', server_id, timeout)
        server_with_ip = self._client.add_ips_to_server(
            server, wait=True, timeout=timeout)
        return self._client.get_openstack_vars(server_with_ip)

    def waitForServerDeletion(self, server_id, timeout=600):
        for count in iterate_timeout(600, "server %s deletion in %s" %
                                     (server_id, self.provider.name)):
            try:
                self.getServerFromList(server_id)
            except NotFound:
                return

    def waitForImage(self, image_id, timeout=3600):
        return self._waitForResource('image', image_id, timeout)

    def createImage(self, server_id, image_name, meta):
        return self._client.create_image_snapshot(
            name=image_name, server=server_id, metadata=meta)

    def getImage(self, image_id):
        return self._client.get_image(image_id)

    def uploadImage(self, image_name, filename, disk_format, container_format,
                    meta):
        # configure glance and upload image.  Note the meta flags
        # are provided as custom glance properties
        # NOTE: we have wait=True set here. This is not how we normally
        # do things in nodepool, preferring to poll ourselves thankyouverymuch.
        # However - two things to note:
        #  - glance v1 has no aysnc mechanism, so we have to handle it anyway
        #  - glance v2 waiting is very strange and complex - but we have to
        #              block for our v1 clouds anyway, so we might as well
        #              have the interface be the same and treat faking-out
        #              a shade-level fake-async interface later
        image = self._client.create_image(
            name=image_name,
            filename='%s.%s' % (filename, disk_format),
            is_public=False,
            disk_format=disk_format,
            container_format=container_format,
            wait=True,
            **meta)
        return image.id

    def listImages(self):
        return self._client.list_images()

    def listFlavors(self):
        return self._client.list_flavors()

    def listServers(self, cache=True):
        if (not cache or
            time.time() - self._servers_time >= SERVER_LIST_AGE):
            # Since we're using cached data anyway, we don't need to
            # have more than one thread actually submit the list
            # servers task.  Let the first one submit it while holding
            # a lock, and the non-blocking acquire method will cause
            # subsequent threads to just skip this and use the old
            # data until it succeeds.
            if self._servers_lock.acquire(False):
                try:
                    self._servers = [
                        self._client.get_openstack_vars(server) for server in
                        self._client.list_servers()
                    ]
                    self._servers_time = time.time()
                finally:
                    self._servers_lock.release()
        return self._servers

    def deleteServer(self, server_id):
        return self._client.delete_server(server_id)

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

        self._client.delete_keypair(server['key_name'])

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)
