#!/usr/bin/env python
#
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

import argparse
import os

import nodepool.config
import nodepool.zk


def main():
    parser = argparse.ArgumentParser(
        description="Update the zookeeper auth ACLs")
    parser.add_argument(
        "-c", dest="config", default="/etc/nodepool/nodepool.yaml",
        help="path to config file")
    parser.add_argument(
        "-s", dest="secure", default="/etc/nodepool/secure.conf",
        help="path to secure file")
    parser.add_argument(
        "--chroot", default="/nodepool", help="zookeeper root node")
    args = parser.parse_args()

    config = nodepool.config.loadConfig(args.config)
    if os.path.exists(args.secure):
        nodepool.config.loadSecureConfig(config, args.secure)

    if not config.zookeeper_auth:
        print("No zookeeper-auth defined.")
        exit(1)

    acl = nodepool.zk.kazoo.security.make_acl(
        config.zookeeper_auth[0], config.zookeeper_auth[1], all=True)
    zk = nodepool.zk.ZooKeeper(enable_cache=False)
    zk.connect(
        list(config.zookeeper_servers.values()),
        auth_data=config.zookeeper_auth)

    # Check we can access root node
    if not zk.client.get(args.chroot):
        print("oops: can't access /nodepool node")
        exit(1)

    # Ask for confirmation
    try:
        if input("Update zookeeper ACL? [Y/n] ").strip() not in ('y', 'Y', ''):
            exit(1)
    except KeyboardInterrupt:
        exit(1)

    def walk(node):
        for child in zk.client.get_children(node):
            for child_node in walk(os.path.join(node, child)):
                yield child_node
        yield node

    for node in walk(args.chroot):
        zk.client.set_acls(node, (acl,))
    print("Done.")


if __name__ == "__main__":
    main()
