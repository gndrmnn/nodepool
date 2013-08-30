#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
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

import argparse
import logging.config
import os
import sys
import signal
import time
from prettytable import PrettyTable

import nodepool.nodepool as nodepool
import nodepool.nodedb as nodedb

class NodePoolCmd(object):
    def __init__(self):
        self.args = None

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='Node pool.')
        parser.add_argument('-c', dest='config',
                            help='path to config file')
        parser.add_argument('--version', dest='version', action='store_true',
                            help='show version')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           help='additional help')

        cmd_list = subparsers.add_parser('list', help='list nodes')
        cmd_list.set_defaults(func=self.list)
        cmd_image_list = subparsers.add_parser('image-list', help='list images')
        cmd_image_list.set_defaults(func=self.image_list)

        self.args = parser.parse_args()

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO)

    def list(self):
        t = PrettyTable(["Provider", "Image", "Target", "Hostname", "NodeName",
                         "Server ID", "IP", "State", "Age (hours)"])
        t.align='l'
        now = time.time()
        with self.pool.getDB().getSession() as session:
            for node in session.getNodes():
                t.add_row([node.provider_name, node.image_name,
                           node.target_name, node.hostname, node.nodename,
                           node.external_id, node.ip,
                           nodedb.STATE_NAMES[node.state],
                           '%.02f' % ((now - node.state_time)/3600)])
            print t

    def image_list(self):
        t = PrettyTable(["Provider", "Image", "Hostname", "Version",
                         "Image ID", "Server ID", "State", "Age (hours)"])
        t.align='l'
        now = time.time()
        with self.pool.getDB().getSession() as session:
            for image in session.getSnapshotImages():
                t.add_row([image.provider_name, image.image_name,
                           image.hostname, image.version,
                           image.external_id, image.server_external_id,
                           nodedb.STATE_NAMES[image.state],
                           '%.02f' % ((now - image.state_time)/3600)])
            print t


    def main(self):
        self.setup_logging()
        self.pool = nodepool.NodePool(self.args.config)
        config = self.pool.loadConfig()
        self.pool.reconfigureDatabase(config)
        self.pool.setConfig(config)
        self.args.func()

def main():
    npc = NodePoolCmd()
    npc.parse_arguments()

    if npc.args.version:
        from nodepool.version import version_info as npc_version_info
        print "Nodepool version: %s" % npc_version_info.version_string()
        return(0)

    npc.main()


if __name__ == "__main__":
    sys.exit(main())
