# Copyright (C) 2011-2013 OpenStack Foundation
# Copyright 2022 Acme Gating, LLC
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

import logging
import threading
import time
import uuid

import openstack.exceptions
from openstack.cloud.exc import OpenStackCloudCreateException

from nodepool.driver.openstack.adapter import OpenStackAdapter
from nodepool import exceptions


class Dummy(object):
    IMAGE = 'Image'
    INSTANCE = 'Instance'
    FLAVOR = 'Flavor'
    LOCATION = 'Server.Location'
    PORT = 'Port'

    def __init__(self, kind, **kw):
        self.__kind = kind
        self.__kw = kw
        for k, v in kw.items():
            setattr(self, k, v)
        try:
            if self.should_fail:
                raise openstack.exceptions.OpenStackCloudException(
                    'This image has SHOULD_FAIL set to True.')
            if self.over_quota:
                raise openstack.exceptions.HttpException(
                    message='Quota exceeded for something', http_status=403)
        except AttributeError:
            pass

    def _get_dict(self):
        data = {}
        for k in self.__kw.keys():
            data[k] = getattr(self, k)
        data.pop('event')
        data.pop('_kw')
        data.pop('manager')
        return data

    def __repr__(self):
        args = []
        for k in self.__kw.keys():
            args.append('%s=%s' % (k, getattr(self, k)))
        args = ' '.join(args)
        return '<%s %s %s>' % (self.__kind, id(self), args)

    def __contains__(self, key):
        return key in self.__dict__

    def __getitem__(self, key, default=None):
        return getattr(self, key, default)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)

    def copy(self):
        data = self._get_dict()
        return Dummy(self.__kind, **data)


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.links = []

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, cloud):
        self.cloud = cloud

    def get(self, uri, headers, params):
        if uri == '/servers/detail':
            server_list = []
            for server in self.cloud._server_list:
                data = server._get_dict()
                data['hostId'] = data.pop('host_id')
                data['OS-EXT-AZ:availability_zone'] = data.pop('location').zone
                data['os-extended-volumes:volumes_attached'] =\
                    data.pop('volumes')
                server_list.append(data)
            return FakeResponse({'servers': server_list})


class FakeOpenStackCloud(object):
    log = logging.getLogger("nodepool.FakeOpenStackCloud")

    @staticmethod
    def _get_quota():
        return 100, 20, 1000000

    @staticmethod
    def _get_volume_quota():
        return 100, 1000000

    def __init__(self, images=None, networks=None):
        self.compute = FakeSession(self)
        self.pause_creates = False
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
        if networks is None:
            self.ipv6_network_uuid = uuid.uuid4().hex
            self.no_auto_ip_network_uuid = uuid.uuid4().hex
            networks = [dict(id=uuid.uuid4().hex,
                             name='fake-public-network-name'),
                        dict(id=uuid.uuid4().hex,
                             name='fake-private-network-name'),
                        dict(id=self.no_auto_ip_network_uuid,
                             name='no-auto-ip-network-name'),
                        dict(id=self.ipv6_network_uuid,
                             name='fake-ipv6-network-name')]
        self.networks = networks
        self._flavor_list = [
            Dummy(Dummy.FLAVOR, id=uuid.uuid4().hex, ram=8192,
                  name='Fake Flavor', vcpus=4),
            Dummy(Dummy.FLAVOR, id=uuid.uuid4().hex, ram=8192,
                  name='Unreal Flavor', vcpus=4),
        ]
        self._azs = ['az1', 'az2']
        self._server_list = []
        self._update_quota()
        self._down_ports = [
            Dummy(Dummy.PORT, id=uuid.uuid4().hex, status='DOWN',
                  device_owner="compute:nova"),
            Dummy(Dummy.PORT, id=uuid.uuid4().hex, status='DOWN',
                  device_owner=None),
        ]
        self._floating_ip_list = []
        self._volume_list = []

    def _update_quota(self):
        self.max_cores, self.max_instances, self.max_ram = FakeOpenStackCloud.\
            _get_quota()
        self.max_volumes, self.max_volume_gb = FakeOpenStackCloud.\
            _get_volume_quota()

    def _get(self, name_or_id, instance_list):
        self.log.debug("Get %s in %s" % (name_or_id, repr(instance_list)))
        for instance in instance_list:
            if isinstance(name_or_id, dict):
                if instance.id == name_or_id['id']:
                    return instance
            elif instance.name == name_or_id or instance.id == name_or_id:
                return instance
        return None

    def get_network(self, name_or_id, filters=None):
        for net in self.networks:
            if net['id'] == name_or_id or net['name'] == name_or_id:
                return net
        return self.networks[0]

    def _create(self, instance_list, instance_type=Dummy.INSTANCE,
                done_status='ACTIVE', max_quota=-1, **kw):
        should_fail = kw.get('SHOULD_FAIL', '').lower() == 'true'
        nics = kw.get('nics', [])
        security_groups = kw.get('security_groups', [])
        addresses = None
        # if keyword 'ipv6-uuid' is found in provider config,
        # ipv6 address will be available in public addr dict.
        auto_ip = True
        for nic in nics:
            if nic['net-id'] == self.no_auto_ip_network_uuid:
                auto_ip = False
            if nic['net-id'] != self.ipv6_network_uuid:
                continue
            addresses = dict(
                public=[dict(version=4, addr='fake'),
                        dict(version=6, addr='fake_v6')],
                private=[dict(version=4, addr='fake')]
            )
            public_v6 = 'fake_v6'
            public_v4 = 'fake'
            private_v4 = 'fake'
            host_id = 'fake_host_id'
            interface_ip = 'fake_v6'
            break
        if not addresses:
            host_id = 'fake'
            private_v4 = 'fake'
            if auto_ip:
                addresses = dict(
                    public=[dict(version=4, addr='fake')],
                    private=[dict(version=4, addr='fake')]
                )
                public_v6 = ''
                public_v4 = 'fake'
                interface_ip = 'fake'
            else:
                public_v4 = ''
                public_v6 = ''
            interface_ip = private_v4
        self._update_quota()
        over_quota = False
        if (instance_type == Dummy.INSTANCE and
            self.max_instances > -1 and
            len(instance_list) >= self.max_instances):
            over_quota = True

        az = kw.get('availability_zone')
        if az and az not in self._azs:
            raise openstack.exceptions.BadRequestException(
                message='The requested availability zone is not available',
                http_status=400)

        s = Dummy(instance_type,
                  id=uuid.uuid4().hex,
                  name=kw['name'],
                  status='BUILD',
                  adminPass='fake',
                  addresses=addresses,
                  public_v4=public_v4,
                  public_v6=public_v6,
                  private_v4=private_v4,
                  host_id=host_id,
                  interface_ip=interface_ip,
                  security_groups=security_groups,
                  location=Dummy(Dummy.LOCATION, zone=az),
                  metadata=kw.get('meta', {}),
                  manager=self,
                  key_name=kw.get('key_name', None),
                  should_fail=should_fail,
                  over_quota=over_quota,
                  flavor=kw.get('flavor'),
                  event=threading.Event(),
                  volumes=[],
                  _kw=kw)
        instance_list.append(s)
        t = threading.Thread(target=self._finish,
                             name='FakeProvider create',
                             args=(s, 0.1, done_status))
        t.start()
        return s.copy()

    def _delete(self, name_or_id, instance_list):
        self.log.debug("Delete from %s" % (repr(instance_list),))
        instance = None
        for maybe in instance_list:
            if isinstance(name_or_id, dict):
                if maybe.id == name_or_id.get('id'):
                    instance = maybe
            elif maybe.name == name_or_id or maybe.id == name_or_id:
                instance = maybe
        if instance and not instance.status == 'DELETED':
            # We don't remove DELETED instances as we want to test
            # they are handled properly.
            instance_list.remove(instance)
        self.log.debug("Deleted from %s" % (repr(instance_list),))

    def _finish(self, obj, delay, status):
        self.log.debug("Pause creates %s", self.pause_creates)
        if self.pause_creates:
            self.log.debug("Pausing")
            obj.event.wait()
            self.log.debug("Continuing")
        else:
            time.sleep(delay)
        obj.status = status

    def create_image(self, **kwargs):
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            done_status='READY', **kwargs)

    def get_image(self, name_or_id, filters=None):
        return self._get(name_or_id, self._image_list)

    def list_images(self):
        return self._image_list

    def delete_image(self, name_or_id):
        if not name_or_id:
            raise Exception('name_or_id is Empty')
        self._delete(name_or_id, self._image_list)

    def create_image_snapshot(self, name, server, **metadata):
        # XXX : validate metadata?
        return self._create(
            self._image_list, instance_type=Dummy.IMAGE,
            name=name, **metadata)

    def list_flavors(self, get_extra=False):
        return self._flavor_list

    def get_openstack_vars(self, server):
        server.public_v4 = 'fake'
        server.public_v6 = 'fake'
        server.private_v4 = 'fake'
        server.host_id = 'fake'
        server.interface_ip = 'fake'
        return server

    def create_server(self, **kw):
        return self._create(self._server_list, **kw)

    def get_server(self, name_or_id):
        result = self._get(name_or_id, self._server_list)
        return result

    def get_server_by_id(self, server_id):
        return self.get_server(server_id)

    def list_floating_ips(self):
        return self._floating_ip_list

    def create_floating_ip(self, server):
        fip = Dummy('floating_ips',
                    id=uuid.uuid4().hex,
                    floating_ip_address='fake',
                    status='ACTIVE')
        self._floating_ip_list.append(fip)
        return fip

    def _needs_floating_ip(self, server, nat_destination):
        return False

    def _has_floating_ips(self):
        return False

    def list_servers(self, bare=False):
        return self._server_list

    def delete_server(self, name_or_id, delete_ips=True):
        self._delete(name_or_id, self._server_list)

    def list_availability_zone_names(self):
        return self._azs.copy()

    def get_compute_limits(self):
        self._update_quota()
        return Dummy(
            'limits',
            max_total_cores=self.max_cores,
            max_total_instances=self.max_instances,
            max_total_ram_size=self.max_ram,
            total_cores_used=4 * len(self._server_list),
            total_instances_used=len(self._server_list),
            total_ram_used=8192 * len(self._server_list)
        )

    def get_volume_limits(self):
        self._update_quota()
        return Dummy(
            'limits',
            absolute={
                'maxTotalVolumes': self.max_volumes,
                'maxTotalVolumeGigabytes': self.max_volume_gb,
            })

    def list_volumes(self):
        return self._volume_list

    def list_ports(self, filters=None):
        if filters and filters.get('status') == 'DOWN':
            return self._down_ports
        return []

    def delete_port(self, port_id):
        tmp_ports = []
        for port in self._down_ports:
            if port.id != port_id:
                tmp_ports.append(port)
            else:
                self.log.debug("Deleted port ID: %s", port_id)
        self._down_ports = tmp_ports


class FakeUploadFailCloud(FakeOpenStackCloud):
    log = logging.getLogger("nodepool.FakeUploadFailCloud")

    def __init__(self, *args, times_to_fail=None, **kw):
        super(FakeUploadFailCloud, self).__init__(*args, **kw)
        self.times_to_fail = times_to_fail
        self.times_failed = 0

    def create_image(self, **kwargs):
        if self.times_to_fail is None:
            raise exceptions.BuilderError("Test fail image upload.")
        self.times_failed += 1
        if self.times_failed <= self.times_to_fail:
            raise exceptions.BuilderError("Test fail image upload.")
        else:
            return super(FakeUploadFailCloud, self).create_image(**kwargs)


class FakeLaunchAndGetFaultCloud(FakeOpenStackCloud):
    log = logging.getLogger("nodepool.FakeLaunchAndGetFaultCloud")

    def create_server(self, *args, **kwargs):
        # OpenStack provider launch code specifically looks for 'quota' in
        # the failure message.
        server = super().create_server(
            *args, **kwargs,
            done_status='ERROR')
        # Don't wait for the async update
        orig_server = self._get(server.id, self._server_list)
        orig_server.status = 'ERROR'
        orig_server.fault = {'message': 'quota server fault'}
        raise OpenStackCloudCreateException('server', server.id)


class FakeLaunchAndDeleteFailCloud(FakeOpenStackCloud):
    log = logging.getLogger("nodepool.FakeLaunchAndDeleteFailCloud")

    def __init__(self, times_to_fail=None):
        super(FakeLaunchAndDeleteFailCloud, self).__init__()
        self.times_to_fail_delete = times_to_fail
        self.times_to_fail_launch = times_to_fail
        self.times_failed_delete = 0
        self.times_failed_launch = 0
        self.launch_success = False
        self.delete_success = False

    def create_server(self, *args, **kwargs):
        if self.times_to_fail_launch is None:
            raise Exception("Test fail server launch.")
        if self.times_failed_launch < self.times_to_fail_launch:
            self.times_failed_launch += 1
            # Simulate a failure after the server record is created
            ret = super().create_server(*args, **kwargs, done_status='ERROR')
            ret.fault = {'message': 'expected error'}
            return ret
        else:
            self.launch_success = True
            return super().create_server(*args, **kwargs)

    def delete_server(self, *args, **kwargs):
        if self.times_to_fail_delete is None:
            raise exceptions.ServerDeleteException("Test fail server delete.")
        if self.times_failed_delete < self.times_to_fail_delete:
            self.times_failed_delete += 1
            raise exceptions.ServerDeleteException("Test fail server delete.")
        else:
            self.delete_success = True
            return super().delete_server(*args, **kwargs)


class FakeDeleteImageFailCloud(FakeOpenStackCloud):
    log = logging.getLogger("nodepool.FakeDeleteImageFailCloud")

    def __init__(self):
        super().__init__()
        self._fail = True

    def delete_image(self, *args, **kwargs):
        if self._fail:
            raise Exception('Induced failure for testing')
        else:
            return super(FakeDeleteImageFailCloud,
                         self).delete_image(*args, **kwargs)


class FakeAdapter(OpenStackAdapter):
    fake_cloud = FakeOpenStackCloud

    def __init__(self, provider_config):
        self.createServer_fails = 0
        self.createServer_fails_with_external_id = 0
        self.__client = FakeAdapter.fake_cloud()
        super().__init__(provider_config)

    def _getClient(self):
        return self.__client

    def _createServer(self, *args, **kwargs):
        while self.createServer_fails:
            self.createServer_fails -= 1
            raise Exception("Expected createServer exception")
        while self.createServer_fails_with_external_id:
            self.createServer_fails_with_external_id -= 1
            raise OpenStackCloudCreateException('server', 'fakeid')
        return super()._createServer(*args, **kwargs)

    def _expandServer(self, server):
        return server
