# Copyright 2014 Hewlett-Packard Development Company, L.P.
# Copyright 2012 New Dream Network, LLC (DreamHost)
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
import os
import sys

from alembic import command as alembic_command
from alembic import config as alembic_config
from alembic import util as alembic_util

from nodepool import nodepool


def get_alembic_config():
    config = alembic_config.Config(
        os.path.join(os.path.dirname(__file__), 'alembic.ini'))
    config.set_main_option('script_location',
                           'nodepool.migration:alembic_migrations')
    return config


class NodePoolDbManage(object):

    def __init__(self):
        self.args = None
        self._alembic_config = get_alembic_config()

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='Nodepool Db Management')
        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('--debug', dest='debug', action='store_true',
                            help='show DEBUG level logging')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           dest='command',
                                           help='additional help')

        for name in ['current', 'history', 'branches']:
            named_parser = subparsers.add_parser(name)
            named_parser.set_defaults(func=self.do_alembic_command)

        check_migration_parser = subparsers.add_parser('check_migration')
        check_migration_parser.set_defaults(func=self.do_check_migration)

        for name in ['upgrade', 'downgrade']:
            named_parser = subparsers.add_parser(name)
            named_parser.add_argument('--delta', type=int)
            named_parser.add_argument('--sql', action='store_true')
            named_parser.add_argument('revision', nargs='?')
            named_parser.set_defaults(func=self.do_upgrade_downgrade)

        stamp_parser = subparsers.add_parser('stamp')
        stamp_parser.add_argument('--sql', action='store_true')
        stamp_parser.add_argument('revision')
        stamp_parser.set_defaults(func=self.do_stamp)

        revision_parser = subparsers.add_parser('revision')
        revision_parser.add_argument('-m', '--message')
        revision_parser.add_argument('--autogenerate', action='store_true')
        revision_parser.add_argument('--sql', action='store_true')
        revision_parser.set_defaults(func=self.do_revision)

        self.args = parser.parse_args()

    def do_alembic_command(self, *args, **kwargs):
        try:
            getattr(alembic_command, self.args.command)(
                self._alembic_config, *args, **kwargs)
        except alembic_util.CommandError as e:
            alembic_util.err(str(e))

    def do_check_migration(self):
        self.do_alembic_command('branches')

    def do_upgrade_downgrade(self):
        if not self.args.revision and not self.args.delta:
            raise SystemExit('You must provide a revision or relative delta')

        revision = self.args.revision

        if self.args.delta:
            sign = '+' if self.args.name == 'upgrade' else '-'
            revision = sign + str(self.args.delta)
        else:
            revision = self.args.revision

        self.do_alembic_command(revision, sql=self.args.sql)

    def do_stamp(self):
        self.do_alembic_command(self.args.revision, sql=self.args.sql)

    def do_revision(self):
        self.do_alembic_command(
            message=self.args.message,
            autogenerate=self.args.autogenerate,
            sql=self.args.sql)

    def main(self):
        self.parse_arguments()
        self.pool = nodepool.NodePool(self.args.config)
        config = self.pool.loadConfig()
        self._alembic_config.dburi = config.dburi
        self._alembic_config.engine = None
        self.args.func()


def main():
    return NodePoolDbManage().main()


if __name__ == "__main__":
    sys.exit(main())
