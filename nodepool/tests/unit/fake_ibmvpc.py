# Copyright (C) 2021 Acme Gating, LLC
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
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import time
import re
import urllib
import uuid

import fixtures

import responses


class CRUDManager:
    name = ''
    list_name = None

    def __init__(self, cloud):
        self.cloud = cloud
        self.items = []
        self.requests = []

    def list(self, request):
        listname = self.list_name or self.name
        resp = {
            "limit": 50,
            "first": {
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"{self.name}?limit=50")
            },
            "total_count": len(self.items),
            listname: self.items,
        }
        return (200, {}, json.dumps(resp))

    def get(self, request):
        url = urllib.parse.urlparse(request.path_url)
        uid = url.path.split('/')[-1]
        item = self.lookup(id=uid)
        if item:
            return (200, {}, json.dumps(item))
        else:
            return (404, {}, json.dumps({
                'message': 'Item not found',
            }))

    def lookup(self, **kw):
        for item in self.items:
            for k, v in kw.items():
                if item[k] == v:
                    return item


class ResourceGroupsCRUD(CRUDManager):
    name = "resources"

    def make(self, name):
        uid = str(uuid.uuid4().hex)
        uid2 = str(uuid.uuid4().hex)
        data = {
            "id": uid,
            "crn": ("crn:v1:bluemix:public:resource-controller::a/"
                    f"{self.cloud.account_id}::resource-group:{uid}"),
            "account_id": self.cloud.account_id,
            "name": name,
            "state": "ACTIVE",
            "default": False,
            "enable_reclamation": False,
            "quota_id": uid2,
            "quota_url": f"/v2/quota_definitions/{uid2}",
            "payment_methods_url": (f"/v2/resource_groups/{uid}"
                                    "/payment_methods"),
            "resource_linkages": [],
            "teams_url": f"/v2/resource_groups/{uid}/teams",
            "created_at": "2021-12-01T20:41:25.202Z",
            "updated_at": "2021-12-01T20:41:25.202Z"
        }
        return data

    def post(self, request):
        data = json.loads(request.body)
        obj = self.make(data['name'])
        self.items.append(obj)
        return (200, {}, json.dumps(obj))


class ResourceInstancesCRUD(CRUDManager):
    name = "resource_instances"
    list_name = "resources"

    def make(self, name, kind, resource_group):
        uid = str(uuid.uuid4().hex)
        uid2 = str(uuid.uuid4().hex)
        uid3 = str(uuid.uuid4().hex)
        data = {
            "id": (f"crn:v1:bluemix:public:{kind}:global:"
                   f"a/{self.cloud.account_id}:{uid}::"),
            "guid": uid,
            "url": f"/v2/resource_instances/{uid}",
            "created_at": "2021-12-11T01:01:19.588831392Z",
            "updated_at": "2021-12-11T01:01:21.814042061Z",
            "deleted_at": None,
            "created_by": "IBMid-something",
            "updated_by": "",
            "deleted_by": "",
            "scheduled_reclaim_at": None,
            "restored_at": None,
            "scheduled_reclaim_by": "",
            "restored_by": "",
            "name": name,
            "region_id": "global",
            "account_id": f"{self.cloud.account_id}",
            "reseller_channel_id": "",
            "resource_plan_id": uid2,
            "resource_group_id": resource_group['id'],
            "resource_group_crn": resource_group['crn'],
            "target_crn": (f"crn:v1:bluemix:public:globalcatalog::::"
                           f"deployment:{uid2}%3Aglobal"),
            "allow_cleanup": False,
            "crn": (f"crn:v1:bluemix:public:{kind}:global:"
                    f"a/{self.cloud.account_id}:{uid}::"),
            "state": "active",
            "type": "service_instance",
            "resource_id": uid3,
            "dashboard_url": "https://cloud.ibm.com/something/",
            "last_operation": {
                "type": "create",
                "state": "succeeded",
                "async": False,
                "description": "Completed create instance operation"
            },
            "resource_aliases_url": (
                f"/v2/resource_instances/{uid}/resource_aliases"),
            "resource_bindings_url": (
                f"/v2/resource_instances/{uid}/resource_bindings"),
            "resource_keys_url": f"/v2/resource_instances/{uid}/resource_keys",
            "plan_history": [
                {
                    "resource_plan_id": uid2,
                    "start_date": "2021-12-11T01:01:19.588831392Z",
                    "requestor_id": "IBMid-something"
                }
            ],
            "migrated": False,
            "controlled_by": "",
            "locked": False,
        }
        return data


class ProfilesCRUD(CRUDManager):
    name = "profiles"

    def make(self, name, cpu, ram):
        data = {
            'bandwidth': {'type': 'fixed', 'value': 4000},
            'disks': [],
            'family': 'compute',
            'href': ('https://us-south.iaas.cloud.ibm.com/v1/instance/'
                     f'profiles/{name}'),
            'memory': {'type': 'fixed', 'value': ram},
            'name': 'cx2-2x4',
            'os_architecture': {
                'default': 'amd64', 'type': 'enum', 'values': ['amd64']
            },
            'port_speed': {'type': 'fixed', 'value': 16000},
            'total_volume_bandwidth': {
                'default': 1000, 'max': 3500, 'min': 500,
                'step': 1, 'type': 'range'
            },
            'vcpu_architecture': {'type': 'fixed', 'value': 'amd64'},
            'vcpu_count': {'type': 'fixed', 'value': cpu}
        }
        return data


class VpcsCRUD(CRUDManager):
    name = "vpcs"

    def make(self, name, resource_group):
        uid = str(uuid.uuid4())
        net_acl = str(uuid.uuid4())
        routing_table = str(uuid.uuid4())
        security_group = str(uuid.uuid4())
        data = {
            "id": uid,
            "crn": ("crn:v1:bluemix:public:is:us-south:"
                    f"a/{self.cloud.account_id}::vpc:{uid}"),
            "href": f"https://us-south.iaas.cloud.ibm.com/v1/vpcs/{uid}",
            "name": name,
            "resource_type": "vpc",
            "status": "available",
            "classic_access": False,
            "created_at": "2021-12-09T17:11:13Z",
            "default_network_acl": {
                "id": net_acl,
                "crn": ("crn:v1:bluemix:public:is:us-south:"
                        f"a/{self.cloud.account_id}::network-acl:{net_acl}"),
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/network_acls/"
                         f"{net_acl}"),
                "name": net_acl,
            },
            "default_routing_table": {
                "id": routing_table,
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/vpcs/"
                         f"{uid}/routing_tables/{routing_table}"),
                "name": routing_table,
                "resource_type": "routing_table"
            },
            "default_security_group": {
                "id": security_group,
                "crn": ("crn:v1:bluemix:public:is:us-south:"
                        f"a/{self.cloud.account_id}::"
                        f"security-group:{security_group}"),
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"security_groups/{security_group}"),
                "name": security_group,
            },
            "resource_group": {
                "id": resource_group['id'],
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"resource_groups/{resource_group['id']}"),
                "name": resource_group['name']
            },
            "cse_source_ips": [
                {
                    "ip": {"address": "10.16.234.155"},
                    "zone": {
                        "name": "us-south-1",
                        "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                                 "regions/us-south/zones/us-south-1")
                    }
                },
                {
                    "ip": {"address": "10.16.246.112"},
                    "zone": {
                        "name": "us-south-2",
                        "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                                 "regions/us-south/zones/us-south-2")
                    }
                },
                {
                    "ip": {"address": "10.16.250.50"},
                    "zone": {
                        "name": "us-south-3",
                        "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                                 "regions/us-south/zones/us-south-3")
                    }
                }
            ]
        }
        return data


class SubnetsCRUD(CRUDManager):
    name = "subnets"

    def make(self, name, vpc):
        uid = str(uuid.uuid4())
        data = {
            "id": uid,
            "crn": ("crn:v1:bluemix:public:is:us-south-1:"
                    f"a/{self.cloud.account_id}::subnet:{uid}"),
            "href": f"https://us-south.iaas.cloud.ibm.com/v1/subnets/{uid}",
            "name": name,
            "resource_type": "subnet",
            "available_ipv4_address_count": 250,
            "ipv4_cidr_block": "10.240.0.0/24",
            "ip_version": "ipv4",
            "zone": {
                "name": "us-south-1",
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         "regions/us-south/zones/us-south-1")
            },
            "vpc": {
                "id": vpc['id'],
                "crn": ("crn:v1:bluemix:public:is:us-south:"
                        f"a/{self.cloud.account_id}::vpc:{vpc['id']}"),
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"vpcs/{vpc['id']}"),
                "name": vpc['name'],
                "resource_type": "vpc"
            },
            "status": "available",
            "total_ipv4_address_count": 256,
            "created_at": "2021-12-09T17:11:18Z",
            "network_acl": vpc['default_network_acl'],
            "resource_group": vpc['resource_group'],
            "routing_table": vpc['default_routing_table'],
        }
        return data


class KeysCRUD(CRUDManager):
    name = "keys"

    def make(self, name, vpc):
        uid = str(uuid.uuid4())
        data = {
            "created_at": "2021-12-09T17:14:53Z",
            "crn": ("crn:v1:bluemix:public:is:us-south:"
                    f"a/{self.cloud.account_id}::key:{uid}"),
            "fingerprint": "SHA256:something",
            "href": f"https://us-south.iaas.cloud.ibm.com/v1/keys/{uid}",
            "id": uid,
            "length": 2048,
            "name": name,
            "public_key": "ssh-rsa something\n",
            "resource_group": vpc['resource_group'],
            "type": "rsa"
        }
        return data


class FloatingIPsCRUD(CRUDManager):
    name = "floating_ips"

    def addTarget(self, fip, instance):
        if 'target' in fip:
            raise Exception("FIP already has target")
        fip['target'] = {
            "href": instance['primary_network_interface']['href'],
            "id": instance['primary_network_interface']['id'],
            "name": instance['primary_network_interface']['name'],
            "primary_ipv4_address": instance['primary_network_interface'
                                             ]['primary_ipv4_address'],
            "resource_type": "network_interface"
        }

    def removeTarget(self, fip):
        del fip['target']

    def make(self, name, resource_group):
        uid = str(uuid.uuid4())
        addr = 'fake'
        data = {
            "address": addr,
            "created_at": "2021-12-11T00:07:08Z",
            "crn": ("crn:v1:bluemix:public:is:us-south-1:"
                    f"a/{self.cloud.account_id}::floating-ip:{uid}"),
            "href": ("https://us-south.iaas.cloud.ibm.com/"
                     f"v1/floating_ips/{uid}"),
            "id": uid,
            "name": name,
            "resource_group": {
                "id": resource_group['id'],
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"resource_groups/{resource_group['id']}"),
                "name": resource_group['name']
            },
            "status": "available",
            "zone": {
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         "regions/us-south/zones/us-south-1"),
                "name": "us-south-1"
            }
        }
        return data

    def post(self, request):
        data = json.loads(request.body)
        resource_group = self.cloud.crud['resources'].lookup(
            id=data['resource_group']['id'])
        zone = data['zone']['name']
        # Make sure this argument is supplied
        if zone != 'us-south-1':
            raise Exception("Unsupported zone")
        obj = self.make(data['name'], resource_group)
        self.items.append(obj)
        return (200, {}, json.dumps(obj))

    def delete(self, request):
        url = urllib.parse.urlparse(request.path_url)
        uid = url.path.split('/')[-1]
        instance = self.lookup(id=uid)
        if instance:
            self.items.remove(instance)
        else:
            return (404, {}, json.dumps({
                'message': 'Floating IP not found',
            }))

        return self.list(request)


class InstancesCRUD(CRUDManager):
    name = "instances"

    def make(self, name, vpc, profile, image):
        uid = str(uuid.uuid4())
        boot_volume_id = str(uuid.uuid4())
        boot_volume_attachment_id = str(uuid.uuid4())
        nic_id = str(uuid.uuid4())
        subnet = self.cloud.crud['subnets'].lookup(name='sn-nodepool')

        nic = {
            "href": ("https://us-south.iaas.cloud.ibm.com/v1/instances/"
                     f"{uid}/network_interfaces/{nic_id}"),
            "id": nic_id,
            "name": "eth0",
            "primary_ipv4_address": f"10.240.0.{len(self.items) + 1}",
            "resource_type": "network_interface",
            "subnet": {
                "crn": subnet['crn'],
                "href": subnet['href'],
                "id": subnet['id'],
                "name": subnet['name'],
                "resource_type": "subnet"
            }
        }
        boot_volume_attachment = {
            "device": {"id": f"{boot_volume_attachment_id}-25fcc"},
            "href": ("https://us-south.iaas.cloud.ibm.com/v1/instances/"
                     f"{uid}/volume_attachments/{boot_volume_attachment_id}"),
            "id": boot_volume_attachment_id,
            "name": boot_volume_attachment_id,
            "volume": {
                "crn": ("crn:v1:bluemix:public:is:us-south-1:"
                        f"a/{self.cloud.account_id}::volume:{boot_volume_id}"),
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/volumes/"
                         f"{boot_volume_id}"),
                "id": boot_volume_id,
                "name": f"{name}-boot-1639181087000"
            }
        }

        data = {
            "bandwidth": 4000,
            "boot_volume_attachment": boot_volume_attachment,
            "created_at": "2021-12-11T00:05:16Z",
            "crn": ("crn:v1:bluemix:public:is:us-south-1:"
                    f"a/{self.cloud.account_id}::instance:{uid}"),
            "disks": [],
            "href": f"https://us-south.iaas.cloud.ibm.com/v1/instances/{uid}",
            "id": uid,
            "image": {
                "crn": image['crn'],
                "href": image['href'],
                "id": image['id'],
                "name": image['name'],
            },
            "memory": 4,
            "name": name,
            "network_interfaces": [nic],
            "primary_network_interface": nic,
            "profile": {
                "href": profile['href'],
                "name": profile['name'],
            },
            "resource_group": vpc['resource_group'],
            "resource_type": "instance",
            "startable": True,
            "status": "pending",
            "status_reasons": [],
            "total_network_bandwidth": 3000,
            "total_volume_bandwidth": 1000,
            "vcpu": {
                "architecture": "amd64",
                "count": 2
            },
            "volume_attachments": [boot_volume_attachment],
            "vpc": {
                "crn": vpc['crn'],
                "href": vpc['href'],
                "id": vpc['id'],
                "name": vpc['name'],
                "resource_type": "vpc"
            },
            "zone": {
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         "regions/us-south/zones/us-south-1"),
                "name": "us-south-1"
            }
        }
        return data

    def post(self, request):
        data = json.loads(request.body)
        vpc = self.cloud.crud['vpcs'].lookup(id=data['vpc']['id'])
        profile = self.cloud.crud['profiles'].lookup(
            name=data['profile']['name'])
        image = self.cloud.crud['images'].lookup(id=data['image']['id'])
        obj = self.make(data['name'], vpc, profile, image)
        self.items.append(obj)
        ret = json.dumps(obj)
        # Finish provisioning after return
        obj['status'] = 'running'
        return (200, {}, ret)

    def delete(self, request):
        url = urllib.parse.urlparse(request.path_url)
        uid = url.path.split('/')[-1]
        instance = self.lookup(id=uid)
        if instance:
            self.items.remove(instance)
        else:
            return (404, {}, json.dumps({
                'message': 'Instance not found',
            }))

        return self.list(request)


class ImagesCRUD(CRUDManager):
    name = "images"

    def make(self, name, operating_system, owner_type, resource_group=None):
        uid = str(uuid.uuid4())
        if resource_group:
            rg = {
                "id": resource_group['id'],
                "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                         f"resource_groups/{resource_group['id']}"),
            }
        else:
            rgid = str(uuid.uuid4())
            rg = {
                "id": rgid,
                "href": ("https://resource-controller.cloud.ibm.com/"
                         f"v1/resource_groups/{rgid}")
            }

        data = {
            "id": uid,
            "crn": ("crn:v1:bluemix:public:is:us-south:"
                    f"a/something::image:{uid}"),
            "href": f"https://us-south.iaas.cloud.ibm.com/v1/images/{uid}",
            "name": name,
            "minimum_provisioned_size": 100,
            "resource_group": rg,
            "created_at": "2020-01-11T01:01:01Z",
            "file": {
                "checksums": {"sha256": "something"},
                "size": 2
            },
            "operating_system": operating_system,
            "status": "available",
            "visibility": "public",
            "encryption": "none",
            "status_reasons": [],
            "owner_type": owner_type,
        }
        return data

    def post(self, request):
        data = json.loads(request.body)
        resource_group = self.cloud.crud['resources'].lookup(
            id=data['resource_group']['id'])
        os = self.cloud.operating_systems[data['operating_system']['name']]
        obj = self.make(data['name'], os, 'user', resource_group)
        self.items.append(obj)
        return (200, {}, json.dumps(obj))

    def delete(self, request):
        url = urllib.parse.urlparse(request.path_url)
        uid = url.path.split('/')[-1]
        instance = self.lookup(id=uid)
        if instance:
            self.items.remove(instance)
        else:
            return (404, {}, json.dumps({
                'message': 'Image not found',
            }))

        return self.list(request)


class FakeIBMVPCFixture(fixtures.Fixture):
    operating_systems = {
        'debian-9-amd64': {
            "href": ("https://us-south.iaas.cloud.ibm.com/v1/"
                     "operating_systems/debian-9-amd64"),
            "name": "debian-9-amd64",
            "architecture": "amd64",
            "display_name": ("Debian GNU/Linux 9.x Stretch/Stable - "
                             "Minimal Install (amd64)"),
            "family": "Debian GNU/Linux",
            "vendor": "Debian",
            "version": "9.x Stretch/Stable - Minimal Install",
            "dedicated_host_only": False
        },
    }

    def _setUp(self):
        self.crud = {}
        self.responses = responses.RequestsMock()
        self.responses.start()
        self.account_id = str(uuid.uuid4())

        self._setup_crud(
            'https://resource-controller.cloud.ibm.com/v2/resource_groups',
            ResourceGroupsCRUD)
        p = self.crud['resources']
        p.items.append(p.make('Default'))

        self._setup_crud(
            r'https://resource-controller.cloud.ibm.com/v2/'
            r'resource_instances\?name=(.*?)&type=(.*?)',
            ResourceInstancesCRUD)
        p = self.crud['resource_instances']
        p.items.append(p.make(
            "Cloud Object Storage-r6", "cloud-object-storage",
            self.crud['resources'].lookup(name='Default')))

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/vpcs',
            VpcsCRUD)
        p = self.crud['vpcs']
        p.items.append(p.make(
            'nodepool', self.crud['resources'].lookup(name='Default')))

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/subnets',
            SubnetsCRUD)
        p = self.crud['subnets']
        p.items.append(p.make(
            'sn-nodepool', self.crud['vpcs'].lookup(name='nodepool')))

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/keys',
            KeysCRUD)
        p = self.crud['keys']
        p.items.append(p.make(
            'testuser', self.crud['vpcs'].lookup(name='nodepool')))

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/instance/profiles',
            ProfilesCRUD)
        p = self.crud['profiles']
        p.items.append(p.make('cx2-2x4', 2, 4))

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/floating_ips',
            FloatingIPsCRUD)

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/instances',
            InstancesCRUD)

        self._setup_crud(
            'https://us-south.iaas.cloud.ibm.com/v1/images',
            ImagesCRUD)
        p = self.crud['images']
        p.items.append(p.make('ibm-debian-9-13-minimal-amd64-4',
                              self.operating_systems['debian-9-amd64'],
                              'provider'))

        self.attach_fip_re = re.compile(
            r'https://us-south.iaas.cloud.ibm.com/v1/instances/(.*?)'
            r'/network_interfaces/(.*?)'
            r'/floating_ips/(.*?)'
            r'\?version=(.+?)&generation=(\d+)')
        self.responses.add_callback(
            responses.PUT,
            self.attach_fip_re,
            callback=self._attach_floating_ip,
            content_type='application/json')

        self.responses.add(
            responses.POST,
            'https://iam.cloud.ibm.com/identity/token',
            json={
                'token_type': 'Bearer',
                'access_token': 'secret_token',
                'expires_in': 600,
                'expiration': time.time() + 600,
            })

        self.addCleanup(self.responses.stop)
        self.addCleanup(self.responses.reset)

    def _setup_crud(self, url, manager):
        self.crud[manager.name] = manager(self)
        list_re = re.compile(
            '^' + url + r'(\?version=(.+?)&generation=(\d+))?$')
        crud_re = re.compile(
            '^' + url + r'/(.+?)(\?version=(.+?)&generation=(\d+))?$')
        self.responses.add_callback(
            responses.GET, list_re, callback=self.crud[manager.name].list,
            content_type='application/json')
        self.responses.add_callback(
            responses.GET, crud_re, callback=self.crud[manager.name].get,
            content_type='application/json')
        if hasattr(self.crud[manager.name], 'post'):
            self.responses.add_callback(
                responses.POST, list_re, callback=self.crud[manager.name].post,
                content_type='application/json')
        if hasattr(self.crud[manager.name], 'delete'):
            self.responses.add_callback(
                responses.DELETE, crud_re,
                callback=self.crud[manager.name].delete,
                content_type='application/json')

    def _attach_floating_ip(self, request):
        m = self.attach_fip_re.match(request.url)
        instance_id, nic_id, fip_id, version, generation = m.groups()
        fip = self.crud['floating_ips'].lookup(id=fip_id)
        instance = self.crud['instances'].lookup(id=instance_id)
        if nic_id != instance['primary_network_interface']['id']:
            raise Exception("Unsupported nic id")
        self.crud['floating_ips'].addTarget(fip, instance)
        return (200, {}, json.dumps(fip))
