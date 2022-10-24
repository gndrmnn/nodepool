# Copyright (C) 2011-2013 OpenStack Foundation
# Copyright 2017 Red Hat
# Copyright 2022 Acme Gating, LLC
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

import functools
import logging
import math
import operator

import openstack

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine
from nodepool import version

class OpenStackInstance(statemachine.Instance):
    def __init__(self, provider, server):
        super().__init__()
        self.external_id = server.id
        self.metadata = server.metadata
        self.private_ipv4 = server.private_v4
        self.private_ipv6 = None
        self.public_ipv4 = server.public_v4
        self.public_ipv6 = server.public_v6
        self.host_id = server.host_id
        self.cloud = provider.cloud_config.name
        self.region = provider.region_name
        self.az = server.location.zone

        self.interface_ip = server.interface_ip

    def getQuotaInformation(self):
        #XXX
        return QuotaInformation(instances=1)


class OpenStackResource(statemachine.Resource):
    def __init__(self, metadata, type, id):
        super().__init__(metadata)
        self.type = type
        self.id = id


class OpenStackDeleteStateMachine(statemachine.StateMachine):
    FLOATING_IP_DELETING = 'deleting floating ip'
    SERVER_DELETE = 'delete server'
    SERVER_DELETING = 'deleting server'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.external_id = external_id
        self.floating_ips = None

    def advance(self):
        if self.state == self.START:
            self.server = self.adapter._getServer(external_id)
            if self.server and self.adapter._hasFloatingIps() and self.server.addresses:
                self.floating_ips = openstack.meta.find_nova_interfaces(
                    self.server['addresses'], ext_tag='floating')
                for fip in self.floating_ips:
                    self.adapter._deleteFloatingIp(self, fip)
                    self.state = self.FLOATING_IP_DELETING
            if not self.floating_ips:
                self.state = self.SERVER_DELETE

        if self.state == self.FLOATING_IP_DELETING:
            fips = []
            for fip in self.floating_ips:
                fip = self.adapter._refreshFloatingIpDelete(self.floating_ip)
                if not fip or fip['status'] == 'DOWN':
                    fip = None
                if fip:
                    fips.append(fip)
            self.floating_ips = fips
            if self.floating_ips:
                return
            else:
                self.state = self.SERVER_DELETE

        if self.state == self.SERVER_DELETE:
            self.adapter._deleteServer(self.external_id)
            self.state = self.SERVER_DELETING

        if self.state == self.SERVER_DELETING:
            self.server = self.adapter._refreshServerDelete(self.server)
            if self.server:
                return
            else:
                self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True


class OpenStackCreateStateMachine(statemachine.StateMachine):
    SERVER_CREATE = 'creating server'
    SERVER_RETRY = 'retrying server creation'
    FLOATING_IP_CREATE = 'creating floating ip'
    FLOATING_IP_ATTACH = 'attaching floating ip'
    COMPLETE = 'complete'
    """
    add_ips_to_server(auto_ip=pool.auto_floating_ip)
      if auto_ip:
        create_floating_ip  (possibly call this with wait=False)
        waitloop: get_floating_ip_by_id
        _attach_ip_to_server
        waitloop: get_server_ip
    """

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries, request, az, log):
        self.log = log
        super().__init__()
        self.adapter = adapter
        self.provider = adapter.provider
        self.retries = retries
        self.attempts = 0
        self.label = label
        self.server = None
        self.hostname = hostname
        self.az = az

        if image_external_id:
            diskimage = self.provider_config.diskimages[
                label.diskimage.name]
            self.image_external = image_external_id
        else:
            # launch using unmanaged cloud image
            self.config_drive = label.cloud_image.config_drive

            if label.cloud_image.image_id:
                # Using a dict with the ID bypasses an image search during
                # server creation.
                self.image_external = dict(id=label.cloud_image.image_id)
            else:
                self.image_external = label.cloud_image.external_name
            image_name = label.cloud_image.name

        props = label.instance_properties.copy()
        for k, v in label.dynamic_instance_properties.items():
            try:
                #XXX
                props[k] = v.format(request=self.request.getSafeAttributes())
            except Exception:
                self.log.exception(
                    "Error formatting dynamic instance property %s", k)
        if not props:
            props = None

        # Put provider.name and image_name in as groups so that ansible
        # inventory can auto-create groups for us based on each of those
        # qualities
        # Also list each of those values directly so that non-ansible
        # consumption programs don't need to play a game of knowing that
        # groups[0] is the image name or anything silly like that.
        groups_list = [self.provider.name]
        groups_list.append(image_name)
        groups_list.append(label.name)
        meta = dict(
            groups=",".join(groups_list),
        )
        # merge in any provided properties
        if props:
            meta.update(props)
        meta.update(metadata)
        self.metadata = metadata
        self.external_id = None

    def advance(self):
        if self.state == self.START:
            self.external_id = None
            try:
                self.server = self.adapter._createServer(
                    self.hostname,
                    image=self.image_external,
                    min_ram=self.label.min_ram,
                    flavor_name=self.label.flavor_name,
                    key_name=self.label.key_name,
                    az=self.az,
                    config_drive=self.config_drive,
                    networks=self.label.networks,
                    security_groups=self.label.pool.security_groups,
                    boot_from_volume=self.label.boot_from_volume,
                    volume_size=self.label.volume_size,
                    instance_properties=self.metadata,
                    userdata=self.label.userdata)
            except openstack.cloud.exc.OpenStackCloudCreateException as e:
                if e.resource_id:
                    self.external_id = e.resource_id
                    raise
            self.external_id = self.server.id
            self.state = self.SERVER_CREATE

        if self.state == self.SERVER_CREATE:
            self.server = self.adapter._refreshServer(self.server)

            if self.server.status == 'ACTIVE':
                if (self.label.pool.auto_floating_ip and
                    self.adapter._needsFloatingIp(self.server)):
                    self.floating_ip = self.adapter._createFloatingIp(self.server)
                    self.state = self.FLOATING_IP_CREATE
                else:
                    self.state = self.COMPLETE
            elif self.server.status == 'ERROR':
                if ('fault' in server and server['fault'] is not None
                    and 'message' in server['fault']):
                    self.log.error(
                        "Error in creating the server."
                        " Compute service reports fault: {reason}".format(
                            reason=server['fault']['message']))
                if self.attempts >= self.retries:
                    raise Exception("Too many retries")
                self.attempts += 1
                if self.external_id:
                    self.server = self.adapter._deleteServer(self.external_id)
                else:
                    self.server = None
                self.state = self.SERVER_RETRY
            else:
                return

        if self.state == self.SERVER_RETRY:
            self.server = self.adapter._refreshServerDelete(self.server)
            if self.server is None:
                self.state = self.START
                return

        if self.state == self.FLOATING_IP_CREATE:
            self.floating_ip = self.adapter._refreshFloatingIp(self.floating_ip)
            if fip and fip['status'] == 'ACTIVE':
                self.state = self.FLOATING_IP_ATTACH
            else:
                return

        if self.state == self.FLOATING_IP_ATTACH:
            self.server = self.adapter._refresh(self.server)
            ext_ip = openstack.meta.get_server_ip(
                self.server, ext_tag='floating', public=True)
            if ext_ip == self.floating_ip['floating_ip_address']:
                self.state = self.COMPLETE
            else:
                return

        if self.state == self.COMPLETE:
            self.complete = True
            return OpenStackInstance(self.adapter.provider, self.server)


class OpenStackAdapter(statemachine.Adapter):

    def __init__(self, provider_config):
        # Wrap these instance methods with a per-instance LRU cache so
        # that we don't leak memory over time when the adapter is
        # occasionally replaced.
        self._findImage = functools.lru_cache(maxsize=None)(
            self._findImage)
        self._listFlavors = functools.lru_cache(maxsize=None)(
            self._listFlavors)
        self._findNetwork = functools.lru_cache(maxsize=None)(
            self._findNetwork)

        self.log = logging.getLogger(
            f"nodepool.OpenStackAdapter.{provider_config.name}")
        self.provider = provider_config
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)
        self._client = self._getClient()
        #self._running = True

    def getCreateStateMachine(self, hostname, label, image_external_id,
                              metadata, retries, request, az, log):
        return OpenStackCreateStateMachine(self, hostname, label, image_external_id,
                                           metadata, retries, request, az, log)

    def getDeleteStateMachine(self, external_id, log):
        return OpenStackDeleteStateMachine(self, external_id, log)

    def listResources(self):
        return []

    def deleteResource(self, resource):
        self.log.info(f"Deleting leaked {resource.type}: {resource.id}")

    def listInstances(self):
        return []

    def getQuotaLimits(self):
        return QuotaInformation(default=math.inf)

    def getQuotaForLabel(self, label):
        return QuotaInformation(instances=1)

    ### Local implementation

    def _getClient(self):
        rate_limit = None
        # nodepool tracks rate limit in time between requests.
        # openstacksdk tracks rate limit in requests per second.
        # 1/time = requests-per-second.
        if self.provider.rate:
            rate_limit = 1 / self.provider.rate
        return openstack.connection.Connection(
            config=self.provider.cloud_config,
            use_direct_get=False,
            rate_limit=rate_limit,
            app_name='nodepool',
            app_version=version.version_info.version_string()
        )

    def _createServer(self, name, image, metadata=None,
                      flavor_name=None, min_ram=None,
                      az=None, key_name=None, config_drive=True,
                      networks=None, security_groups=None,
                      boot_from_volume=False, volume_size=50,
                      instance_properties=None, userdata=None):
        if not networks:
            networks = []
        if not isinstance(image, dict):
            # if it's a dict, we already have the cloud id. If it's not,
            # we don't know if it's name or ID so need to look it up
            image = self._findImage(image)
        flavor = self._findFlavor(flavor_name=flavor_name, min_ram=min_ram)
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
        if security_groups:
            create_args['security_groups'] = security_groups
        if userdata:
            create_args['userdata'] = userdata
        nics = []
        for network in networks:
            net_id = self._findNetwork(network)['id']
            nics.append({'net-id': net_id})
        if nics:
            create_args['nics'] = nics
        if metadata:
            create_args['meta'] = metadata

        try:
            return self._client.create_server(wait=False, **create_args)
        except openstack.exceptions.BadRequestException:
            # We've gotten a 400 error from nova - which means the request
            # was malformed. The most likely cause of that, unless something
            # became functionally and systemically broken, is stale az, image
            # or flavor cache. Log a message, invalidate the caches so that
            # next time we get new caches.
            self.log.info(
                "Clearing az, flavor and image caches due to 400 error "
                "from nova")
            self._findImage.clear_cache()
            self._listFlavors.clear_cache()
            self._findNetwork.clear_cache()
            raise

    # This method is wrapped with an LRU cache in the constructor.
    def _findImage(self, name):
        return self._client.get_image(name, filters={'status': 'active'})

    # This method is wrapped with an LRU cache in the constructor.
    def _listFlavors(self):
        return self._client.list_flavors(get_extra=False)

    def _getFlavors(self):
        flavors = self._listFlavors()
        flavors.sort(key=operator.itemgetter('ram'))
        return flavors

    def _findFlavorByName(self, flavor_name):
        for f in self._getFlavors():
            if flavor_name in (f['name'], f['id']):
                return f
        raise Exception("Unable to find flavor: %s" % flavor_name)

    def _findFlavorByRam(self, min_ram, flavor_name):
        for f in self._getFlavors():
            if (f['ram'] >= min_ram
                    and (not flavor_name or flavor_name in f['name'])):
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def _findFlavor(self, flavor_name, min_ram):
        if min_ram:
            return self._findFlavorByRam(min_ram, flavor_name)
        else:
            return self._findFlavorByName(flavor_name)

    # This method is wrapped with an LRU cache in the constructor.
    def _findNetwork(self, name):
        network = self._client.get_network(name)
        if not network:
            raise Exception("Unable to find network %s in provider %s" % (
                name, self.provider.name))
        return network

    # list_servers includes list caching logic
    def _listServers(self):
        return self._client.list_servers()

    # list_floating_ips includes list caching logic
    def _listFloatingIps(self):
        return self._client.list_floating_ips()

    def _refreshServer(self, obj):
        for server in self._listServers():
            if server.id == obj.id:
                return server
        return obj

    def _getServer(self, external_id):
        for server in self._listServers():
            if server.id == external_id:
                return server
        return None

    def _refreshServerDelete(self, obj):
        if obj is None:
            return obj
        for server in self._listServers():
            if server.id == obj.id:
                if server.status.lower() == 'deleted':
                    return None
                return server
        return None

    def _refreshFloatingIp(self, obj):
        for fip in self._listFloatingIps():
            if fip.id == obj.id:
                return fip
        return obj

    def _refreshFloatingIpDelete(self, obj):
        if obj is None:
            return obj
        for fip in self._listFloatingIps():
            if fip.id == obj.id:
                if ip.status == 'DOWN':
                    return None
                return fip
        return obj

    def _needsFloatingIp(self, server):
        return self._client._needs_floating_ip(server=server, nat_destination=None)

    def _createFloatingIp(self, server):
        return self._client.create_floating_ip(server=server)

    def _hasFloatingIps(self):
        return self._client._has_floating_ips()

    def _deleteFloatingIp(self, fip):
        self._client.delete_floating_ip(fip.id, retry=0)

    def _deleteServer(self, external_id):
        self._client.delete_server(external_id)
