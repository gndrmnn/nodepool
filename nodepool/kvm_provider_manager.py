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
import os.path
import re
import time
import uuid

from lxml import etree

import nodeutils as utils
import provider_manager
from task_manager import Task, TaskManager


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


def make_server_dict(server, ips):

    global libvirt
    if libvirt is None:
        libvirt = __import__('libvirt')

    d = dict(id=str(server.ID()),
             name=server.name())
    status = server.state(0)
    if status[0] == libvirt.VIR_DOMAIN_SHUTOFF:
        d['status'] = 'SHUTOFF'
    elif status[0] == libvirt.VIR_DOMAIN_RUNNING and not ips:
        d['status'] = 'BUILDING'
    elif status[0] == libvirt.VIR_DOMAIN_RUNNING and ips:
        d['status'] = 'ACTIVE'
        d['public_v4'] = ips[0]
    else:
        d['status'] = 'UNKNOWN'
    return d


class CreateServerTask(Task):
    def main(self, client):
        dom = client.defineXML(self.args['xml'])
        dom.create()
        return dom.ID()


class GetServerTask(Task):
    def main(self, client):
        try:
            dom = client.lookupByID(int(self.args['server_id']))
            #find all interface's mac for fetch ip address
            macs = []
            xml = etree.fromstring(dom.XMLDesc(0))
            for iface in xml.findall('.//devices/interface'):
                mac = iface.find('mac').get('address')
                macs.append(mac)
            #get arp from host
            connect_kwargs = dict(key_filename=self.args['keypair'])
            host = utils.ssh_connect(self.args['auth_url'],
                                     self.args['username'],
                                     connect_kwargs)
            command = "arp -n"
            arp_table = host.ssh("Get mac and ip addresses from arp table",
                                 command,
                                 output=True)
            #search for matches ip address and mac in output
            ips = []
            ip_pattern = "^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            for string in arp_table.split('\n'):
                for mac in macs:
                    if mac in string:
                        ip = re.findall(ip_pattern, string)
                        ips.append(ip[0])
            ips = list(set(ips))
        except Exception:
            raise provider_manager.NotFound()

        return make_server_dict(dom, ips)


class DeleteServerTask(Task):
    def main(self, client):
        try:
            deleted = False
            dom = client.lookupByID(int(self.args['server_id']))
            xml = etree.fromstring(dom.XMLDesc(0))
            if dom.isActive():
                dom.shutdown()
            for count in iterate_timeout(600, "waiting for server deletion"):
                if not dom.isActive():
                    dom.undefine()
                    deleted = True
                    break

            if not deleted:
                dom.destroy()
                dom.undefine()
                raise Exception("virtual machine shutdown incorrectly")

            images = []
            for image in xml.findall('.//devices/disk'):
                path_to_image = image.find('source').get('file')
                images.append(path_to_image)
            return images

        except Exception:
            raise Exception("Error while deleting a server")


class GetAllServersIds(Task):
    def main(self, client):
        doms_ids = client.listDomainsID()
        return doms_ids


class KVMProviderManager(TaskManager):
    log = logging.getLogger("nodepool.KVMProviderManager")

    def __init__(self, provider):
        super(KVMProviderManager, self).__init__(None, provider.name,
                                                 provider.rate)

        global libvirt
        if libvirt is None:
            libvirt = __import__('libvirt')

        self.provider = provider
        self._client = self._getConnection()
        self._servers = []
        self._servers_time = 0
        self._images = {}

    def _getConnection(self):
        """ Return libvirt connection.
            Expected that ssh key already on server
        """
        host_url = 'qemu+ssh://{0}@{1}/system'.format(self.provider.username,
                                                      self.provider.auth_url)
        conn = libvirt.open(host_url)
        return conn

    def submitTask(self, task):
        self.queue.put(task)
        return task.wait()

    def deleteImage(self, path_to_image):
        host = self._getSshConnect()
        command = "rm -f {0} ".format(path_to_image)
        host.ssh("Delete image from host", command)

    def _getSshConnect(self):
        connect_kwargs = dict(key_filename=self.provider.keypair)
        return utils.ssh_connect(self.provider.auth_url,
                                 self.provider.username,
                                 connect_kwargs)

    def createServer(self, name, min_ram, vcpu,
                     image_name, for_template=False):
        image = self.provider.images[image_name]
        if for_template:
            path_to_image = os.path.join(self.provider.templates_images_dir,
                                         image.base_image)
        else:
            #clone template image on host
            start_time = time.time()
            timestamp = str(int(start_time))
            image_end_name = timestamp + image.base_image
            path_to_template = os.path.join(self.provider.templates_images_dir,
                                            image.base_image)
            path_to_image = os.path.join(self.provider.dest_images_dir,
                                         image_end_name)
            command = "cp {0} {1}".format(path_to_template,
                                          path_to_image)
            host = self._getSshConnect()
            host.ssh("clone template image", command)

        #generate uuid for vm
        vm_uuid = str(uuid.uuid4())
        # We are using default unit for memory: KiB.
        #<memory unit='MB'> in xml description not work
        ram = str(int(image.min_ram) * 1024)
        xml = self._generateXML(name,
                                vcpu,
                                ram,
                                path_to_image,
                                self.provider.nics,
                                vm_uuid)
        return self.submitTask(CreateServerTask(xml=xml))

    def _generateXML(self, name, vcpu, ram, disk, nics, uuid):
        root = etree.Element("os")
        etree.SubElement(root, "type",
                         arch='x86_64',
                         machine='pc-1.0').text = "hvm"
        sub_el = etree.tostring(root)

        root = etree.Element("domain", type='kvm')
        etree.SubElement(root, "name").text = name
        etree.SubElement(root, "uuid").text = uuid
        etree.SubElement(root, "memory").text = ram
        etree.SubElement(root, "vcpu").text = vcpu
        root.append(etree.XML(sub_el))

        subroot = etree.Element("features")
        etree.SubElement(subroot, "acpi")
        etree.SubElement(subroot, "apic")
        etree.SubElement(subroot, "pae")
        root.append(subroot)

        subroot = etree.Element("devices")
        etree.SubElement(subroot, "emulator").text = \
            '/usr/bin/kvm'

        insubroot = etree.Element("disk", type='file', device='disk')
        insubroot.append(etree.Element("driver", name='qemu', type='qcow2'))
        insubroot.append(etree.Element("source", file=disk))
        insubroot.append(etree.Element("target", dev='vda', bus='virtio'))
        subroot.append(insubroot)

        for nic in nics:
            insubroot = etree.Element("interface", type='network')
            insubroot.append(etree.Element("source", network=nic['net-id']))
            subroot.append(insubroot)

        root.append(subroot)

        return etree.tostring(root, pretty_print=True)

    def listServers(self):
        doms_id = self.submitTask(GetAllServersIds())
        servers = []
        for dom_id in doms_id:
            servers.append(self.getServer(dom_id))
        return servers

    def getServer(self, server_id):
        return self.submitTask(GetServerTask(server_id=server_id,
                                             username=self.provider.username,
                                             auth_url=self.provider.auth_url,
                                             keypair=self.provider.keypair))

    def _waitForResource(self, resource_type, resource_id, timeout):
        last_status = None
        for count in iterate_timeout(timeout,
                                     "waiting for %s %s" % (resource_type,
                                                            resource_id)):
            try:
                if resource_type == 'server':
                    resource = self.getServer(resource_id)
            except provider_manager.NotFound:
                continue
            except Exception:
                self.log.exception('Unable to list %ss while waiting for '
                                   '%s will retry' % (resource_type,
                                                      resource_id))
                continue

            status = resource.get('status')
            if (last_status != status):
                self.log.debug('Status of %s %s: %s' %
                               (resource_type, resource_id, status))
            last_status = status
            if status == 'ACTIVE':
                return resource

    def waitForServer(self, server_id, timeout=3600):
        return self._waitForResource('server', server_id, timeout)

    def deleteServer(self, server_id, for_template):
        images = self.submitTask(DeleteServerTask(server_id=server_id))
        if for_template:
            return
        else:
            for image in images:
                self.deleteImage(image)

    def cleanupServer(self, server_id, for_template=False):
        try:
            self.getServer(server_id)
        except provider_manager.NotFound:
            self.log.debug('Get server %s failure' % server_id)
        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id, for_template)
