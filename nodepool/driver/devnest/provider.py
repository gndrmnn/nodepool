# Copyright 2019 Red Hat
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

import json
import logging
import subprocess
import time

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.devnest import handler

from shutil import which  # >= Python3.3
from subprocess import PIPE

DEVNEST_CLI = "devnest"
FORCE_RESERVE_TIMEOUT = 180  # minutes


class DevnestError(Exception):
    pass


class DevnestInstance:
    '''Class to interact with devnest CLI, which is a simple CLI
       to manage hardware "reservations". It is available via PyPI
       or as github project:
          https://github.com/rhos-infra/devnest.git
    '''

    log = logging.getLogger("nodepool.driver.devnest.devnestProvider")

    def validate_devnest(self):
        '''Ensure devnest is available on the system.
           Note: This does not check if devnest is properly configured on the
                 system. For that please refer to PyPI project or devnest
                 project page on github.
        '''
        return which(DEVNEST_CLI) is not None

    def _exec_devnest_cmd(self, argv):
        '''Executes devnest command

        Raises:
            DevnestError: If there was error while running devnest command or
                          error while parsing json output from devnest.

        Args:
            argv (:obj:`list`): Arguments passed to the CLI

        Returns:
            (:obj:`dict`): json output from devnest or None
        '''
        return_data = None
        proc = subprocess.Popen(argv, stdout=PIPE, stderr=PIPE)
        (stdout, stderr) = proc.communicate()
        if (proc.returncode is 0):
            if not stdout:
                return None
            try:
                return_data = json.loads(stdout.decode('utf-8'))
            except Exception:
                raise DevnestError("Devnest output decode error: (stdout:"
                                   " %s) (stderr: %s)" % (stdout, stderr))
        else:
            raise DevnestError(
                "Devnest error: %s" % stderr)
        return return_data

    def _reserve(self, label, reservation_time, force=False):
        '''Reserve devnest node from label group for a given time in Hours.
        If there is no free node available, attempt to force reserve the
        node and wait for maximum time specified by FORCE_RESERVE_TIMEOUT.

        Raises:
            DevnestError: If there was no node within given label available
                          for instant or force reservations.

        Args:
            label (:obj:`dict`): Should contain the 'group' item
            reservation_time (:obj:`int`): Time in hours defining for how long
                                           system should be reserved
            force (:obj:`bool`): Force reserve system, see devnest CLI help

        Returns:
            (:obj:`dict`): json output from devnest or None
        '''
        reserved_data = None

        reserve_argv = [DEVNEST_CLI, "reserve",
                        "-g", label['group'],
                        "-t", str(reservation_time),
                        "-j"]
        if force:
            reserve_argv.append("--force")

        try:
            reserved_data = self._exec_devnest_cmd(reserve_argv)
            if reserved_data and force:
                wait_for_node = 1
                node_host = reserved_data['host']
                self.log.debug("Waiting for node to be free: %s" % node_host)
                while wait_for_node <= FORCE_RESERVE_TIMEOUT:
                    list_argv = [DEVNEST_CLI, "list", "-a",
                                 "-f", "json", str(node_host)]
                    check_online = self._exec_devnest_cmd(list_argv)

                    if len(check_online) > 0 and \
                       check_online[0]['state'] == 'reserved':
                        break
                    time.sleep(60)
                    wait_for_node += 1
        except DevnestError as devnest_error:
            if 'found 0 nodes maching' in str(devnest_error).lower():
                self.log.debug("0 nodes found matching ")
                return None
            else:
                raise devnest_error

        return reserved_data

    def reserve(self, label, reservation_time=5):
        '''Reserve devnest node from label group for a given time in Hours.

        Args:
            label (:obj:`dict`): Should contain the 'group' item
            reservation_time (:obj:`int`): Time in hours defining for how long
                                           system should be reserved

        Returns:
            (:obj:`dict`): json output from devnest or None
        '''
        reserved_data = None

        reserved_data = self._reserve(label, reservation_time)
        if not reserved_data:
            reserved_data = self._reserve(label, reservation_time, force=True)

        return reserved_data

    def release(self, server_id):
        '''Release devnest node from reservation.

        Raises:
            Exception: If there was error calling devnest

        Args:
            server_id (:obj:`str`): Node identifier

        Returns:
            (:obj:`bool`): True if server_id was not provided or
                           there was no node matched while calling devnest
        '''
        if not server_id or len(str(server_id)) == 0:
            return True

        release_argv = [DEVNEST_CLI, "release", "-o", str(server_id)]
        try:
            self._exec_devnest_cmd(release_argv)
        except Exception as e:
            if "by  and" in str(e):
                return True  # that's ok, an issue with devnest api
            raise


class DevnestNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.devnest.devnestProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.ready = False
        self.devnest_instance = DevnestInstance()

    def start(self, zk_conn):
        self.log.debug("Starting")
        if self.ready:
            return True

        self.ready = self.devnest_instance.validate_devnest()

    def stop(self):
        self.log.debug("Stopping")
        self.ready = False

    def listNodes(self):
        ''' Cleanup of devnest nodes is happening on Jenkins side
            return empty list
        '''
        servers = []
        return servers

    def labelReady(self, name):
        # Labels are always ready
        return True

    def join(self):
        pass

    def cleanupLeakedResources(self):
        pass

    def cleanupNode(self, server_id):
        if not self.ready:
            return
        self.log.debug("%s: removing node", server_id)
        self.devnest_instance.release(server_id)

    def waitForNodeCleanup(self, server_id):
        self.log.debug("%s: waiting for cleanup", server_id)
        for retry in range(300):

            # TODO: check node is not in devnest list
            if True:
                break

            time.sleep(5)

    def createDevnestNode(self, label, node):
        print("Creating label: ", label)
        reserved_data = self.devnest_instance.reserve(label)
        if not reserved_data:
            raise DevnestError("Could not reserve node from devnest")
        node.external_id = reserved_data['host']
        return reserved_data['ip_address']

    def getRequestHandler(self, poolworker, request):
        return handler.DevnestNodeRequestHandler(poolworker, request)
