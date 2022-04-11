# Copyright 2021 Acme Gating, LLC
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

import math
import logging
import json
import time

from nodepool.driver.utils import QuotaInformation, RateLimiter
from nodepool.driver import statemachine
from nodepool import zk


""" This driver behaves like a static driver execpt that the backing
nodes come from other Nodepool drivers.

The intent is that users will request nodes from this driver, and if
any nodes already exist, the request will be satisfied with those
first.  If not, then this driver will request a new node from another
driver in Nodepool, and then add that to this driver's pool of
available nodes.  Each backing node may supply one or more nodes to
the end user.

For example, a user might request 3 nodes from this driver.  Having
none available, this driver would then request a single node from an
AWS driver.  Once that node is available, this driver might configure
8 terminal nodes all backed by the single AWS node, and fulfill the
request by allocating 3 of them.

If a further request arrived for 5 nodes, they would be fulfilled from
the remaining 5 slots.

Once all 8 nodes have been returned, this driver will release the
underlying AWS node and the AWS driver will reclaim it.

To accomplish this, the process is roughly:

* Upon request, if insufficient nodes available, request new backing
  node(s).  The requestor should be "NodePool:metastatic:{providername}".

* Lock backing nodes with a non-ephemeral lock (so that they persist
  even if this launcher is stopeed) and use
  "NodePool:metastatic:{providername}" as identifier.

* Update the Node.user_data field in ZK to include
  "NodePool:metastatic:{providername}" along with label and slot count
  information for the backing node.

* Set the Node.driver_data field in ZK to include the backing node and
  slot information of which backing node the requested node is
  associated with.

* Periodically, delete unused backing nodes.

To identify our backing nodes:
  Our id is: "NodePool:metastatic:{providername}".
  For every node with our id in user_data:
    If node locked and first lock contender has our id as identifier:
      This is one of our nodes
  The first check is for efficiency, the second is to avoid issues with
  falsified user_data fields.

To cleanup unused backing nodes:
  For each of our end nodes in use: mark backing node in use.

  If a backing node hasn't been in use for {grace-time} seconds,
  set the node to used and remove the lock on the backing node.

To identify our end nodes:
  Check that the node provider matches us, and then consult the
  driver_data field to find which backing node it's assigned to.


This driver acts as both a provider and a user of Nodepool.  It
provides requested nodes and it uses backing nodes.

As a user, it stores node allocation data in the "user_data" field of
the backing node.  As a provider, it stores node allocation in the
"driver_data" field of the requested node.

In order to avoid extra write calls to ZK on every allocation (and to
avoid race conditions that could cause double accounting errors), the
only data we store on the backing node is: that we own it, its label,
and the number of slots it supports.  On the requested node, we store
the backing node id and which slot in the backing node this node
occupies.

We have an in-memory proxy (BackingNodeRecord) for the backing nodes
to keep track of node allocation.  When we start up, we initialize the
proxy based on the data in ZK.
"""


class MetastaticInstance(statemachine.Instance):
    def __init__(self, backing_node, slot, node, metadata=None):
        super().__init__()
        if metadata:
            self.metadata = metadata
        else:
            self.metadata = node.driver_data['metadata']
        if backing_node:
            self.interface_ip = backing_node.interface_ip
            self.public_ipv4 = backing_node.public_ipv4
            self.public_ipv6 = backing_node.public_ipv6
            self.private_ipv4 = backing_node.private_ipv4
            self.az = backing_node.az
            self.region = backing_node.region
            # Image overrides:
            self.username = backing_node.username
            self.python_path = backing_node.python_path
            self.shell_type = backing_node.shell_type
            self.connection_port = backing_node.connection_port
            self.connection_type = backing_node.connection_type
            self.host_keys = backing_node.host_keys
            backing_node_id = backing_node.id
        else:
            backing_node_id = None
        self.driver_data = {
            'metadata': self.metadata,
            'backing_node': backing_node_id,
            'slot': slot,
            'node': node.id,
        }
        self.external_id = node.id

    def getQuotaInformation(self):
        return QuotaInformation(instances=1)


class MetastaticResource(statemachine.Resource):
    def __init__(self, metadata, type, name):
        super().__init__(metadata)
        self.type = type
        self.name = name


class MetastaticDeleteStateMachine(statemachine.StateMachine):
    DEALLOCATING = 'deallocating node'
    COMPLETE = 'complete'

    def __init__(self, adapter, external_id):
        super().__init__()
        self.adapter = adapter
        self.node_id = external_id

    def advance(self):
        if self.state == self.START:
            self.adapter._deallocateBackingNode(self.node_id)
            self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            self.complete = True


class MetastaticCreateStateMachine(statemachine.StateMachine):
    REQUESTING = 'requesting backing node'
    ALLOCATING = 'allocating node'
    COMPLETE = 'complete'

    def __init__(self, adapter, hostname, label, image_external_id,
                 metadata, retries):
        super().__init__()
        self.adapter = adapter
        self.retries = retries
        self.attempts = 0
        self.image_external_id = image_external_id
        self.metadata = metadata
        self.hostname = hostname
        self.label = label
        self.node_id = metadata['nodepool_node_id']

    def advance(self):
        if self.state == self.START:
            self.backing_node_record, self.slot = \
                self.adapter._allocateBackingNode(
                    self.label, self.node_id)
            if self.backing_node_record.node_id is None:
                # We need to make a new request
                self.state = self.REQUESTING
            else:
                # We have an existing node
                self.state = self.COMPLETE
            self.external_id = self.node_id

        if self.state == self.REQUESTING:
            self.adapter._checkBackingNodeRequests()
            if self.backing_node_record.failed:
                raise Exception("Backing node failed")
            if self.backing_node_record.node_id is None:
                return
            self.state = self.COMPLETE

        if self.state == self.COMPLETE:
            backing_node = self.adapter._getNode(
                self.backing_node_record.node_id)
            node = self.adapter._getNode(self.node_id)
            instance = MetastaticInstance(backing_node, self.slot,
                                          node, self.metadata)
            self.complete = True
            return instance


class BackingNodeRecord:
    """An in-memory record of backing nodes and what nodes are allocated
    to them.
    """
    def __init__(self, label_name, slot_count):
        self.label_name = label_name
        self.slot_count = slot_count
        self.node_id = None
        self.request_id = None
        self.allocated_nodes = [None for x in range(slot_count)]
        self.failed = False
        self.last_used = time.time()

    def hasAvailableSlot(self):
        return None in self.allocated_nodes

    def isEmpty(self):
        return not any(self.allocated_nodes)

    def allocateSlot(self, node_id, slot_id=None):
        if slot_id is None:
            idx = self.allocated_nodes.index(None)
        else:
            idx = slot_id
        if self.allocated_nodes[idx] is not None:
            raise Exception("Slot %s of %s is already allocated",
                            idx, self.node_id)
        self.allocated_nodes[idx] = node_id
        return idx

    def deallocateSlot(self, node_id):
        idx = self.allocated_nodes.index(node_id)
        self.allocated_nodes[idx] = None
        self.last_used = time.time()

    def backsNode(self, node_id):
        return node_id in self.allocated_nodes


class MetastaticAdapter(statemachine.Adapter):
    log = logging.getLogger("nodepool.driver.metastatic.MetastaticAdapter")

    def __init__(self, provider_config):
        self.provider = provider_config
        self.rate_limiter = RateLimiter(self.provider.name,
                                        self.provider.rate)
        self.backing_node_records = {}  # label -> [BackingNodeRecord]
        self.pending_requests = []
        # The requestor id
        self.my_id = f'NodePool:metastatic:{self.provider.name}'
        # On startup we need to recover our state from the ZK db, this
        # flag ensures we only do that once.
        self.performed_init = False

    @property
    def zk(self):
        return self._provider._zk

    def getCreateStateMachine(self, hostname, label,
                              image_external_id, metadata, retries, log):
        return MetastaticCreateStateMachine(self, hostname, label,
                                            image_external_id, metadata,
                                            retries)

    def getDeleteStateMachine(self, external_id, log):
        return MetastaticDeleteStateMachine(self, external_id)

    def listResources(self):
        self._init()
        # Since this is called periodically, this is a good place to
        # see about deleting unused backing nodes.
        now = time.time()
        for label_name, backing_node_records in \
            self.backing_node_records.items():
            for bnr in backing_node_records[:]:
                label_config = self.provider._getLabel(bnr.label_name)
                if (bnr.isEmpty() and
                    now - bnr.last_used > label_config.grace_time):
                    self.log.info("Backing node %s has been idle for "
                                  "%s seconds, releasing",
                                  bnr.node_id, now - bnr.last_used)
                    node = self._getNode(bnr.node_id)
                    node.state = zk.USED
                    self.zk.storeNode(node)
                    self.zk.forceUnlockNode(node)
                    backing_node_records.remove(bnr)
        return []

    def deleteResource(self, resource):
        self.log.warning("Unhandled request to delete leaked "
                         f"{resource.type}: {resource.name}")
        # Unused; change log message if we ever use this.

    def listInstances(self):
        # We don't need this unless we're managing quota
        self._init()
        return []

    def getQuotaLimits(self):
        return QuotaInformation(default=math.inf)

    def getQuotaForLabel(self, label):
        return QuotaInformation(instances=1)

    # Local implementation below

    def _init(self):
        if self.performed_init:
            return
        self.log.debug("Performing init")
        # Find backing nodes
        backing_node_map = {}
        for node in self.zk.nodeIterator():
            try:
                user_data = json.loads(node.user_data)
            except Exception:
                continue
            if 'owner' not in user_data:
                continue
            if user_data['owner'] == self.my_id:
                # This may be a backing node for us, but double check
                contenders = self.zk.getNodeLockContenders(node)
                if contenders and contenders[0] == self.my_id:
                    # We hold the lock on this node
                    backing_node_record = BackingNodeRecord(user_data['label'],
                                                            user_data['slots'])
                    backing_node_record.node_id = node.id
                    self.log.info("Found backing node %s for %s",
                                  node.id, user_data['label'])
                    self._addBackingNode(user_data['label'],
                                         backing_node_record)
                    backing_node_map[node.id] = backing_node_record
        # Assign nodes to backing nodes
        for node in self.zk.nodeIterator():
            if node.provider == self.provider.name:
                if not node.driver_data:
                    continue
                bn_id = node.driver_data.get('backing_node')
                bn_slot = node.driver_data.get('slot')
                if bn_id and bn_id in backing_node_map:
                    backing_node_record = backing_node_map[bn_id]
                    backing_node_record.allocateSlot(node.id, bn_slot)
                    self.log.info("Found node %s assigned to backing node %s "
                                  "slot %s",
                                  node.id, backing_node_record.node_id,
                                  bn_slot)
        self.performed_init = True

    def _setProvider(self, provider):
        self._provider = provider

    def _allocateBackingNode(self, label, node_id):
        self._init()
        # if we have room for the label, allocate and return existing slot
        # otherwise, make a new backing node
        backing_node_record = None
        for bnr in self.backing_node_records.get(label.name, []):
            if bnr.hasAvailableSlot():
                backing_node_record = bnr
                break
        if backing_node_record is None:
            req = zk.NodeRequest()
            req.node_types = [label.backing_label]
            req.state = zk.REQUESTED
            req.requestor = self.my_id
            self.zk.storeNodeRequest(req, priority='100')
            backing_node_record = BackingNodeRecord(
                label.name, label.max_parallel_jobs)
            backing_node_record.request_id = req.id
            self._addBackingNode(label.name, backing_node_record)
        slot = backing_node_record.allocateSlot(node_id)
        self.log.info("Assigned node %s to backing node %s slot %s",
                      node_id, backing_node_record.node_id, slot)
        return backing_node_record, slot

    def _addBackingNode(self, label_name, backing_node_record):
        nodelist = self.backing_node_records.setdefault(label_name, [])
        nodelist.append(backing_node_record)

    def _deallocateBackingNode(self, node_id):
        self._init()
        for label_name, backing_node_records in \
            self.backing_node_records.items():
            for bn in backing_node_records:
                if bn.backsNode(node_id):
                    bn.deallocateSlot(node_id)
                    return

    def _checkBackingNodeRequests(self):
        self._init()
        waiting_requests = {}
        for label_name, backing_node_records in \
            self.backing_node_records.items():
            for bnr in backing_node_records:
                if bnr.request_id:
                    waiting_requests[bnr.request_id] = bnr
        if not waiting_requests:
            return
        for request in self.zk.nodeRequestIterator():
            if request.id not in waiting_requests:
                continue
            if request.state == zk.FAILED:
                self.log.error("Backing request %s failed", request.id)
                for label_name, records in self.backing_node_records.items():
                    for bnr in records[:]:
                        if bnr.request_id == request.id:
                            bnr.failed = True
                            records.remove(bnr)
            if request.state == zk.FULFILLED:
                bnr = waiting_requests[request.id]
                node_id = request.nodes[0]
                self.log.info("Backing request %s fulfilled with node id %s",
                              request.id, node_id)
                node = self._getNode(node_id)
                self.zk.lockNode(node, blocking=True, timeout=30,
                                 ephemeral=False, identifier=self.my_id)
                node.user_data = json.dumps({
                    'owner': self.my_id,
                    'label': bnr.label_name,
                    'slots': bnr.slot_count,
                })
                node.state = zk.IN_USE
                self.zk.storeNode(node)
                self.zk.deleteNodeRequest(request)
                bnr.request_id = None
                bnr.node_id = node_id

    def _getNode(self, node_id):
        return self.zk.getNode(node_id)
