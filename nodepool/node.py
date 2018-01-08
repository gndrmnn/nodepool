#!/usr/bin/env python
#
# Copyright 2018 OpenStack Foundation
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

from nodepool import launcher
from nodepool import provider_manager
from nodepool import zk as _zk


def hold(zk, node_id, reason=None):
    node = zk.getNode(node_id)
    if not node:
        raise ValueError("Node id %s not found" % node_id)
    node.state = _zk.HOLD
    if reason:
        node.comment = reason
    zk.lockNode(node, blocking=True)
    zk.storeNode(node)
    zk.unlockNode(node)


def delete(zk, pool, node_id, now=True):
    node = zk.getNode(node_id)
    if not node:
        raise ValueError("Node id %s npt found" % node_id)
    zk.lockNode(node, blocking=True, timeout=5)
    if now:
        if node.provider not in pool.config.providers:
            raise Exception(
                "Provider %s for node %s not defined on this launcher" %
                (node.provider, node.id))
        provider = pool.config.providers[node.provider]
        manager = provider_manager.get_provider(provider, True)
        manager.start()
        launcher.NodeDeleter.delete(zk, manager, node)
        manager.stop()
    else:
        node.state = _zk.DELETING
        zk.storeNode(node)
        zk.unlockNode(node)
