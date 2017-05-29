# Copyright 2017 Red Hat
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
import paramiko
import paramiko.client

from nodepool.driver import ProviderManager


class OpenContainerProviderManager(ProviderManager):
    log = logging.getLogger("nodepool.driver.oci.provider."
                            "OpenContainerProviderManager")

    def __init__(self, provider):
        self.provider = provider

    def getClient(self):
        client = paramiko.client.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()
        client.connect(self.provider.hypervisor, username='root')
        return client

    def start(self):
        self.log.debug("Starting...")

    def stop(self):
        self.log.debug("Stopping...")

    def listServers(self):
        client = self.getClient()
        stdin, stdout, stderr = client.exec_command('runc list -q')
        servers = []
        while True:
            line = stdout.readline()
            if not line:
                break
            servers.append({'name': line})
        client.close()
        return servers

    def cleanupServer(self, server_id):
        client = self.getClient()
        cmds = [
            'runc kill %s KILL' % server_id,
            'umount /var/lib/nodepool/oci/%s/rootfs' % server_id,
        ]
        client.exec_command(';'.join(cmds))
        self.log.warning("Running %s" % ';'.join(cmds))
        client.close()
        return True

    def waitForServerDeletion(self, server_id):
        return True
