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
from contextlib import contextmanager
import operator

import shade

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.nodeutils import iterate_timeout
from nodepool.task_manager import ManagerStoppedException
from nodepool.task_manager import TaskManager


IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list


@contextmanager
def shade_inner_exceptions():
    try:
        yield
    except shade.OpenStackCloudException as e:
        e.log_error()
        raise


class OpenStackProvider(Provider):
    log = logging.getLogger("nodepool.driver.openstack.OpenStackProvider")

    def __init__(self, provider, use_taskmanager):
        self.provider = provider
        self._images = {}
        self._networks = {}
        self.__flavors = {}
        self.__azs = None
        self._use_taskmanager = use_taskmanager
        self._taskmanager = None

    def start(self):
        if self._use_taskmanager:
            self._taskmanager = TaskManager(None, self.provider.name,
                                            self.provider.rate)
            self._taskmanager.start()
        self.resetClient()

    def stop(self):
        if self._taskmanager:
            self._taskmanager.stop()

    def join(self):
        if self._taskmanager:
            self._taskmanager.join()

    @property
    def _flavors(self):
        if not self.__flavors:
            self.__flavors = self._getFlavors()
        return self.__flavors

    def _getClient(self):
        if self._use_taskmanager:
            manager = self._taskmanager
        else:
            manager = None
        return shade.OpenStackCloud(
            cloud_config=self.provider.cloud_config,
            manager=manager,
            **self.provider.cloud_config.config)

    def resetClient(self):
        self._client = self._getClient()
        if self._use_taskmanager:
            self._taskmanager.setClient(self._client)

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(key=operator.itemgetter('ram'))
        return flavors

    # TODO(mordred): These next three methods duplicate logic that is in
    #                shade, but we can't defer to shade until we're happy
    #                with using shade's resource caching facility. We have
    #                not yet proven that to our satisfaction, but if/when
    #                we do, these should be able to go away.
    def _findFlavorByName(self, flavor_name):
        for f in self._flavors:
            if flavor_name in (f['name'], f['id']):
                return f
        raise Exception("Unable to find flavor: %s" % flavor_name)

    def _findFlavorByRam(self, min_ram, flavor_name):
        for f in self._flavors:
            if (f['ram'] >= min_ram
                    and (not flavor_name or flavor_name in f['name'])):
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def findFlavor(self, flavor_name, min_ram):
        # Note: this will throw an error if the provider is offline
        # but all the callers are in threads (they call in via CreateServer) so
        # the mainloop won't be affected.
        if min_ram:
            return self._findFlavorByRam(min_ram, flavor_name)
        else:
            return self._findFlavorByName(flavor_name)

    def findImage(self, name):
        if name in self._images:
            return self._images[name]

        with shade_inner_exceptions():
            image = self._client.get_image(name)
        self._images[name] = image
        return image

    def findNetwork(self, name):
        if name in self._networks:
            return self._networks[name]

        with shade_inner_exceptions():
            network = self._client.get_network(name)
        self._networks[name] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]

        with shade_inner_exceptions():
            return self._client.delete_image(name)

    def createServer(self, name, image,
                     flavor_name=None, min_ram=None,
                     az=None, key_name=None, config_drive=True,
                     nodepool_node_id=None, nodepool_node_label=None,
                     nodepool_image_name=None,
                     networks=None, boot_from_volume=False, volume_size=50):
        if not networks:
            networks = []
        if not isinstance(image, dict):
            # if it's a dict, we already have the cloud id. If it's not,
            # we don't know if it's name or ID so need to look it up
            image = self.findImage(image)
        flavor = self.findFlavor(flavor_name=flavor_name, min_ram=min_ram)
        create_args = dict(name=name,
                           image=image,
                           flavor=flavor,
                           config_drive=config_drive)
        if boot_from_volume:
            create_args['boot_from_volume'] = boot_from_volume
            create_args['volume_size'] = volume_size
            # NOTE(pabelanger): Always cleanup volumes when we delete a server.
            create_args['terminate_volume'] = True
        if key_name:
            create_args['key_name'] = key_name
        if az:
            create_args['availability_zone'] = az
        nics = []
        for network in networks:
            net_id = self.findNetwork(network)['id']
            nics.append({'net-id': net_id})
        if nics:
            create_args['nics'] = nics
        # Put provider.name and image_name in as groups so that ansible
        # inventory can auto-create groups for us based on each of those
        # qualities
        # Also list each of those values directly so that non-ansible
        # consumption programs don't need to play a game of knowing that
        # groups[0] is the image name or anything silly like that.
        groups_list = [self.provider.name]

        if nodepool_image_name:
            groups_list.append(nodepool_image_name)
        if nodepool_node_label:
            groups_list.append(nodepool_node_label)
        meta = dict(
            groups=",".join(groups_list),
            nodepool_provider_name=self.provider.name,
        )
        if nodepool_node_id:
            meta['nodepool_node_id'] = nodepool_node_id
        if nodepool_image_name:
            meta['nodepool_image_name'] = nodepool_image_name
        if nodepool_node_label:
            meta['nodepool_node_label'] = nodepool_node_label
        create_args['meta'] = meta

        with shade_inner_exceptions():
            return self._client.create_server(wait=False, **create_args)

    def getServer(self, server_id):
        with shade_inner_exceptions():
            return self._client.get_server(server_id)

    def getServerConsole(self, server_id):
        try:
            with shade_inner_exceptions():
                return self._client.get_server_console(server_id)
        except shade.OpenStackCloudException:
            return None

    def waitForServer(self, server, timeout=3600, auto_ip=True):
        with shade_inner_exceptions():
            return self._client.wait_for_server(
                server=server, auto_ip=auto_ip,
                reuse=False, timeout=timeout)

    def waitForNodeCleanup(self, server_id, timeout=600):
        for count in iterate_timeout(
                timeout, exceptions.ServerDeleteException,
                "server %s deletion" % server_id):
            if not self.getServer(server_id):
                return

    def waitForImage(self, image_id, timeout=3600):
        last_status = None
        for count in iterate_timeout(
                timeout, exceptions.ImageCreateException, "image creation"):
            try:
                image = self.getImage(image_id)
            except exceptions.NotFound:
                continue
            except ManagerStoppedException:
                raise
            except Exception:
                self.log.exception('Unable to list images while waiting for '
                                   '%s will retry' % (image_id))
                continue

            # shade returns None when not found
            if not image:
                continue

            status = image['status']
            if (last_status != status):
                self.log.debug(
                    'Status of image in {provider} {id}: {status}'.format(
                        provider=self.provider.name,
                        id=image_id,
                        status=status))
                if status == 'ERROR' and 'fault' in image:
                    self.log.debug(
                        'ERROR in {provider} on {id}: {resason}'.format(
                            provider=self.provider.name,
                            id=image_id,
                            resason=image['fault']['message']))
            last_status = status
            # Glance client returns lower case statuses - but let's be sure
            if status.lower() in ['active', 'error']:
                return image

    def createImage(self, server, image_name, meta):
        with shade_inner_exceptions():
            return self._client.create_image_snapshot(
                image_name, server, **meta)

    def getImage(self, image_id):
        with shade_inner_exceptions():
            return self._client.get_image(image_id)

    def labelReady(self, label):
        if not label.cloud_image:
            return False
        image = self.getImage(label.cloud_image.external)
        if not image:
            self.log.warning(
                "Provider %s is configured to use %s as the"
                " cloud-image for label %s and that"
                " cloud-image could not be found in the"
                " cloud." % (self.provider.name,
                             label.cloud_image.external_name,
                             label.name))
            return False
        return True

    def uploadImage(self, image_name, filename, image_type=None, meta=None,
            md5=None, sha256=None):
        # configure glance and upload image.  Note the meta flags
        # are provided as custom glance properties
        # NOTE: we have wait=True set here. This is not how we normally
        # do things in nodepool, preferring to poll ourselves thankyouverymuch.
        # However - two things to note:
        #  - PUT has no aysnc mechanism, so we have to handle it anyway
        #  - v2 w/task waiting is very strange and complex - but we have to
        #              block for our v1 clouds anyway, so we might as well
        #              have the interface be the same and treat faking-out
        #              a shade-level fake-async interface later
        if not meta:
            meta = {}
        if image_type:
            meta['disk_format'] = image_type
        with shade_inner_exceptions():
            image = self._client.create_image(
                name=image_name,
                filename=filename,
                is_public=False,
                wait=True,
                md5=md5,
                sha256=sha256,
                **meta)
        return image.id

    def listImages(self):
        with shade_inner_exceptions():
            return self._client.list_images()

    def listFlavors(self):
        with shade_inner_exceptions():
            return self._client.list_flavors(get_extra=False)

    def listNodes(self):
        # shade list_servers carries the nodepool server list caching logic
        with shade_inner_exceptions():
            return self._client.list_servers()

    def deleteServer(self, server_id):
        with shade_inner_exceptions():
            return self._client.delete_server(server_id, delete_ips=True)

    def cleanupNode(self, server_id):
        server = self.getServer(server_id)
        if not server:
            raise exceptions.NotFound()

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)

    def cleanupLeakedFloaters(self):
        with shade_inner_exceptions():
            self._client.delete_unattached_floating_ips()

    def getAZs(self):
        if self.__azs is None:
            self.__azs = self._client.list_availability_zone_names()
            if not self.__azs:
                # If there are no zones, return a list containing None so that
                # random.choice can pick None and pass that to Nova. If this
                # feels dirty, please direct your ire to policy.json and the
                # ability to turn off random portions of the OpenStack API.
                self.__azs = [None]
        return self.__azs
