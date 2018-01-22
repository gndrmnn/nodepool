#!/usr/bin/env python
# Copyright 2012 Hewlett-Packard Development Company, L.P.
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

from copy import deepcopy
import logging
import os
import sys
import signal

import nodepool.cmd
import nodepool.launcher
import nodepool.webapp

log = logging.getLogger(__name__)


class NodePoolLauncherApp(nodepool.cmd.NodepoolDaemonApp):

    app_name = 'nodepool'

    def create_parser(self):
        parser = super(NodePoolLauncherApp, self).create_parser()

        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            help='path to secure file')
        parser.add_argument('--no-webapp', action='store_true')
        return parser

    def exit_handler(self, signum, frame):
        self.pool.stop()
        if not self.args.no_webapp:
            self.webapp.stop()
            if self.admin_webapp:
                self.admin_webapp.stop()
        sys.exit(0)

    def term_handler(self, signum, frame):
        os._exit(0)

    def run(self):
        self.pool = nodepool.launcher.NodePool(self.args.secure,
                                               self.args.config)
        if not self.args.no_webapp:
            config = self.pool.loadConfig()
            if (config.webapp.get('admin_port') and
                config.webapp.get('admin_listen_address')):
                adminconf = deepcopy(config.webapp)
                adminconf['port'] = adminconf['admin_port']
                adminconf['listen_address'] = adminconf['admin_listen_address']
                del adminconf['admin_port']
                del adminconf['admin_listen_address']
                self.admin_webapp = nodepool.webapp.AdminWebApp(self.pool,
                                                                **adminconf)
            else:
                self.admin_webapp = None
            webconf = deepcopy(config.webapp)
            if 'admin_port' in config.webapp:
                del webconf['admin_port']
            if 'admin_listen_address' in config.webapp:
                del webconf['admin_listen_address']
            self.webapp = nodepool.webapp.WebApp(self.pool,
                                                 **webconf)

        signal.signal(signal.SIGINT, self.exit_handler)
        # For back compatibility:
        signal.signal(signal.SIGUSR1, self.exit_handler)

        signal.signal(signal.SIGTERM, self.term_handler)

        self.pool.start()

        if not self.args.no_webapp:
            self.webapp.start()
            if self.admin_webapp:
                self.admin_webapp.start()

        while True:
            signal.pause()


def main():
    return NodePoolLauncherApp.main()


if __name__ == "__main__":
    sys.exit(main())
