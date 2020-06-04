# Copyright (C) 2018 Red Hat
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

import fixtures
import logging

from nodepool import config as nodepool_config
from nodepool import tests
from nodepool import zk
from nodepool.driver.cobbler import provider


class FakeCobblerServer(object):
    def version(self):
        return 2.8049999999999997

    def login(self, username, password):
        return "FakeToken"

    def logout(self, token):
        return True

    def check_access(self, token, resource):
        return True

    def new_distro(self, token):
        return "fake_distro_id"

    def modify_distro(self, oid, attr, arg, token):
        return True

    def save_distro(self, oid, token):
        return True

    def remove_distro(self, name, token):
        return True

    def new_profile(self, token):
        return "fake_profile_id"

    def modify_profile(self, oid, attr, arg, token):
        return True

    def save_profile(self, oid, token):
        return True

    def remove_profile(self, name, token):
        return True

    def find_profile(self, query):
        fake_profiles = {
            "{'name': 'zuul-nodepool-default'}": "['zuul-nodepool-default']",
        }
        result = fake_profiles.get(str(query), "[]")
        return eval(result)

    def find_system(self, query):
        fake_systems = {
            "{'owners': 'zuul-user-A'}": "['ng0007', 'ng0008']",
            "{'owners': 'zuul-user-B'}": "['ng0009']",
        }
        result = fake_systems.get(str(query), "[]")
        return eval(result)

    def modify_system(self, oid, attr, arg, token):
        return True

    def save_system(self, oid, token):
        return True

    def power_system(self, oid, state, token):
        return True

    def get_system_handle(self, system, token):
        return "system::" + system

    def get_system(self, query: str):
        fake_systems = {
            "ng0007":
                "{'comment': '', 'profile': 'fedora-server-31-x86_64', "
                "'kickstart': '/var/lib/cobbler/kickstarts/fedora-gpu-base', "
                "'name_servers_search': [], 'ks_meta': {}, "
                "'kernel_options_post': {}, 'image': '', "
                "'redhat_management_key': '<<inherit>>', "
                "'power_type': 'ipmilan', 'power_user': 'root', "
                "'kernel_options': {}, 'virt_file_size': '<<inherit>>', "
                "'mtime': 1591805528.27864, 'enable_gpxe': False, "
                "'template_files': {}, 'gateway': '10.6.199.254', "
                "'uid': 'MTU5MDU0MjY3NC44ODQxMjE0NTAuOTczOA', "
                "'virt_auto_boot': 0, 'monit_enabled': False, "
                "'virt_cpus': '<<inherit>>', "
                "'mgmt_parameters': '<<inherit>>', 'boot_files': {}, "
                "'hostname': 'ng0007', 'repos_enabled': False, "
                "'name': 'ng0007', 'virt_type': 'xenpv', 'mgmt_classes': [], "
                "'power_pass': 'superuser', 'netboot_enabled': True, "
                "'ipv6_autoconfiguration': False, 'status': 'production', "
                "'virt_path': '<<inherit>>', "
                "'interfaces': {'eth0': {'ipv6_address': '', "
                "'interface_type': '', 'static': False, 'cnames': [], "
                "'bridge_opts': '', 'management': False, "
                "'interface_master': '', 'mac_address': 'a0:42:3f:3b:82:36', "
                "'ipv6_prefix': '', 'virt_bridge': 'xenbr0', 'netmask': '', "
                "'bonding_opts': '', 'ip_address': '10.6.198.66', "
                "'dhcp_tag': '', 'ipv6_mtu': '', 'static_routes': [], "
                "'ipv6_static_routes': [], 'if_gateway': '', "
                "'dns_name': 'ng0007.linux.amd.com', 'mtu': '', "
                "'connected_mode': False, 'ipv6_secondaries': [], "
                "'ipv6_default_gateway': ''}}, "
                "'power_address': 'ng0007-bmc.linux.amd.com', "
                "'proxy': '<<inherit>>', 'fetchable_files': {}, "
                "'name_servers': [], 'ldap_enabled': False, "
                "'ipv6_default_device': '', 'virt_pxe_boot': 0, "
                "'virt_disk_driver': '<<inherit>>', 'owners': ['zuul'], "
                "'ctime': 1590542869.024451, 'virt_ram': '<<inherit>>', "
                "'power_id': '', 'server': '10.6.198.1', "
                "'redhat_management_server': '<<inherit>>', 'depth': 2, "
                "'ldap_type': 'authconfig', 'template_remote_kickstarts': 0}",
            "ng0008":
                "{'comment': '', 'profile': 'fedora-server-31-x86_64', "
                "'kickstart': '/var/lib/cobbler/kickstarts/fedora-gpu-base', "
                "'name_servers_search': [], 'ks_meta': {}, "
                "'kernel_options_post': {}, 'image': '', "
                "'redhat_management_key': '<<inherit>>', "
                "'power_type': 'ipmilan', 'power_user': 'root', "
                "'kernel_options': {}, 'virt_file_size': '<<inherit>>', "
                "'mtime': 1591805528.27864, 'enable_gpxe': False, "
                "'template_files': {}, 'gateway': '10.6.199.254', "
                "'uid': 'MTU5MDU0Mjg2OS4wMjgyOTU0NzYuODM0MjU', "
                "'virt_auto_boot': 0, 'monit_enabled': False, "
                "'virt_cpus': '<<inherit>>', "
                "'mgmt_parameters': '<<inherit>>', 'boot_files': {}, "
                "'hostname': 'ng0008', 'repos_enabled': False, "
                "'name': 'ng0008', 'virt_type': 'xenpv', 'mgmt_classes': [], "
                "'power_pass': 'superuser', 'netboot_enabled': True, "
                "'ipv6_autoconfiguration': False, 'status': 'production', "
                "'virt_path': '<<inherit>>', "
                "'interfaces': {'eth0': {'ipv6_address': '', "
                "'interface_type': '', 'static': False, 'cnames': [], "
                "'bridge_opts': '', 'management': False, "
                "'interface_master': '', 'mac_address': 'a0:42:3f:3b:82:42', "
                "'ipv6_prefix': '', 'virt_bridge': 'xenbr0', 'netmask': '', "
                "'bonding_opts': '', 'ip_address': '10.6.198.67', "
                "'dhcp_tag': '', 'ipv6_mtu': '', 'static_routes': [], "
                "'ipv6_static_routes': [], 'if_gateway': '', "
                "'dns_name': 'ng0008.linux.amd.com', 'mtu': '', "
                "'connected_mode': False, 'ipv6_secondaries': [], "
                "'ipv6_default_gateway': ''}}, "
                "'power_address': 'ng0008-bmc.linux.amd.com', "
                "'proxy': '<<inherit>>', 'fetchable_files': {}, "
                "'name_servers': [], 'ldap_enabled': False, "
                "'ipv6_default_device': '', 'virt_pxe_boot': 0, "
                "'virt_disk_driver': '<<inherit>>', 'owners': ['zuul'], "
                "'ctime': 1590542869.024451, 'virt_ram': '<<inherit>>', "
                "'power_id': '', 'server': '10.6.198.1', "
                "'redhat_management_server': '<<inherit>>', 'depth': 2, "
                "'ldap_type': 'authconfig', 'template_remote_kickstarts': 0}",
            "ng0009":
                "{'comment': '', 'profile': 'fedora-server-31-x86_64', "
                "'kickstart': '/var/lib/cobbler/kickstarts/fedora-gpu-base', "
                "'name_servers_search': [], 'ks_meta': {}, "
                "'kernel_options_post': {}, 'image': '', "
                "'redhat_management_key': '<<inherit>>', "
                "'power_type': 'ipmilan', 'power_user': 'root', "
                "'kernel_options': {}, 'virt_file_size': '<<inherit>>', "
                "'mtime': 1591805528.27864, 'enable_gpxe': False, "
                "'template_files': {}, 'gateway': '10.6.199.254', "
                "'uid': 'MTU5MDU0MzA5MS42Mjc4MDU3MDAuMzU2Mjc', "
                "'virt_auto_boot': 0, 'monit_enabled': False, "
                "'virt_cpus': '<<inherit>>', "
                "'mgmt_parameters': '<<inherit>>', 'boot_files': {}, "
                "'hostname': 'ng0009', 'repos_enabled': False, "
                "'name': 'ng0009', 'virt_type': 'xenpv', 'mgmt_classes': [], "
                "'power_pass': 'superuser', 'netboot_enabled': True, "
                "'ipv6_autoconfiguration': False, 'status': 'production', "
                "'virt_path': '<<inherit>>', "
                "'interfaces': {'eth0': {'ipv6_address': '', "
                "'interface_type': '', 'static': False, 'cnames': [], "
                "'bridge_opts': '', 'management': False, "
                "'interface_master': '', 'mac_address': 'a0:42:3f:3b:7c:4e', "
                "'ipv6_prefix': '', 'virt_bridge': 'xenbr0', 'netmask': '', "
                "'bonding_opts': '', 'ip_address': '10.6.198.68', "
                "'dhcp_tag': '', 'ipv6_mtu': '', 'static_routes': [], "
                "'ipv6_static_routes': [], 'if_gateway': '', "
                "'dns_name': 'ng0009.linux.amd.com', 'mtu': '', "
                "'connected_mode': False, 'ipv6_secondaries': [], "
                "'ipv6_default_gateway': ''}}, "
                "'power_address': 'ng0009-bmc.linux.amd.com', "
                "'proxy': '<<inherit>>', 'fetchable_files': {}, "
                "'name_servers': [], 'ldap_enabled': False, "
                "'ipv6_default_device': '', 'virt_pxe_boot': 0, "
                "'virt_disk_driver': '<<inherit>>', 'owners': ['zuul'], "
                "'ctime': 1590542869.024451, 'virt_ram': '<<inherit>>', "
                "'power_id': '', 'server': '10.6.198.1', "
                "'redhat_management_server': '<<inherit>>', 'depth': 2, "
                "'ldap_type': 'authconfig', 'template_remote_kickstarts': 0}",
        }
        result = fake_systems.get(query, "{}")
        result = eval(result)
        if len(result) == 0:
            result = '~'
        return result


class TestDriverCobbler(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverCobbler")

    def setUp(self):
        super().setUp()
        self.fake_cobbler_server = FakeCobblerServer()

        def fake_get_cobbler(*args):
            return self.fake_cobbler_server

        self.useFixture(fixtures.MockPatchObject(
            provider.CobblerProvider, '_get_cobbler',
            fake_get_cobbler
        ))

    def test_cobbler_config(self):
        configfile = self.setup_config('cobbler_basic.yaml')
        config = nodepool_config.loadConfig(configfile)
        self.assertIn('cobbler.example.org', config.providers)

    def test_cobbler_basic(self):
        '''
        Test that basic node registration works.
        '''
        configfile = self.setup_config('cobbler_basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.log.debug("Waiting for node pre-registration")
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        nodes = self.waitForNodes('cobbler1')
        self.assertEqual(len(nodes), 1)
        nodes = self.waitForNodes('cobbler2')
        self.assertEqual(len(nodes), 1)

        self.assertEqual(nodes[0].state, zk.READY)
        self.assertEqual(nodes[0].provider, "cobbler.example.org")
        self.assertEqual(nodes[0].pool, "main")
        self.assertEqual(nodes[0].connection_type, 'namespace')

    def test_cobbler_request_handled(self):
        '''
        Test that a node is reregistered after handling a request.
        '''
        configfile = self.setup_config('cobbler_basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 1)
        self.assertEqual(req.nodes[0], nodes[0].id)

        # Mark node as used
        nodes[0].state = zk.USED
        self.zk.storeNode(nodes[0])

        # Our single node should have been used, deleted, then reregistered
        new_nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(new_nodes), 1)
        self.assertEqual(nodes[0].hostname, new_nodes[0].hostname)
        # self.assertEqual(new_nodes[0].provider, "cobbler.example.org")
