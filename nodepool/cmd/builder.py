#!/usr/bin/env python
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
import signal
import sys

from nodepool import builder


def main():
    parser = argparse.ArgumentParser(description='NodePool Image Builder.')
    parser.add_argument('-c', dest='config',
                        default='/etc/nodepool/nodepool.yaml',
                        help='path to config file')
    args = parser.parse_args()

    nb = builder.NodePoolBuilder(args.config)

    def sigint_handler(signal, frame):
        nb.stop()
    signal.signal(signal.SIGINT, sigint_handler)

    nb.start()
    signal.pause()


if __name__ == "__main__":
    sys.exit(main())
