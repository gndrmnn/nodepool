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

from contextlib import contextmanager
from copy import copy
import abc
import json
import logging
import queue
import threading
import time
import uuid

from kazoo import exceptions as kze
from kazoo.recipe.lock import Lock
from kazoo.recipe.election import Election
from kazoo.protocol.states import (
    EventType,
    WatchedEvent,
    KazooState,
)

from nodepool import exceptions as npe
from nodepool.logconfig import get_annotated_logger
from nodepool.zk.components import COMPONENT_REGISTRY
from nodepool.zk import ZooKeeperBase
from nodepool.zk.vendor.states import AddWatchMode
from nodepool.nodeutils import Attributes

# States:
# We are building this image (or node) but it is not ready for use.
BUILDING = 'building'
# The image is being uploaded.
UPLOADING = 'uploading'
# The image/upload/node is ready for use.
READY = 'ready'
# The image/upload/node should be deleted.
DELETING = 'deleting'
# The build failed.
FAILED = 'failed'
# Node request is submitted/unhandled.
REQUESTED = 'requested'
# Node request has been processed successfully.
FULFILLED = 'fulfilled'
# Node request is being worked.
PENDING = 'pending'
# Node is being tested
TESTING = 'testing'
# Node is being used
IN_USE = 'in-use'
# Node has been used
USED = 'used'
# Node is being held
HOLD = 'hold'
# Initial node state
INIT = 'init'
# Aborted due to a transient error like overquota that should not count as a
# failed launch attempt
ABORTED = 'aborted'
# The node has actually been deleted and the Znode should be deleted
DELETED = 'deleted'


# NOTE(Shrews): Importing this from nodepool.config causes an import error
# since that file imports this file.
def as_list(item):
    if not item:
        return []
    if isinstance(item, list):
        return item
    return [item]


class ZooKeeperWatchEvent(object):
    '''
    Class representing a watch trigger event.

    This is mostly used to not pass the kazoo event object to the function
    registered by the caller and maintain a consistent API. Attributes of
    this object include::

        * `type` - Event type. E.g., CREATED/DELETED
        * `state` - Event state. E.g., CONNECTED
        * `path` - Event path. E.g., /nodepool/image/trusty/request-build
        * `image` - Image name this event is based on. E.g., trusty
    '''
    def __init__(self, e_type, e_state, e_path, image):
        self.type = e_type
        self.state = e_state
        self.path = e_path
        # Pass image name so callback func doesn't need to parse from the path
        self.image = image


class Serializable(abc.ABC):
    '''
    Abstract base class for objects that will be stored in ZooKeeper.
    '''

    @abc.abstractmethod
    def toDict(self):
        '''
        Return a dictionary representation of the object.
        '''
        pass

    def serialize(self):
        '''
        Return a representation of the object as a string.

        Used for storing the object data in ZooKeeper.
        '''
        return json.dumps(self.toDict()).encode('utf8')


class BaseModel(Serializable):
    VALID_STATES = set([])

    def __init__(self, o_id):
        if o_id:
            # Call the setter for id so we can validate the incoming type.
            self.id = o_id
        else:
            # Bypass the setter for id to set the default.
            self._id = None
        self._state = None
        self.state_time = None
        self.stat = None

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if not isinstance(value, str):
            raise TypeError("'id' attribute must be a string type")
        self._id = value

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value not in self.VALID_STATES:
            raise TypeError("'%s' is not a valid state" % value)
        self._state = value
        self.state_time = time.time()

    def toDict(self):
        '''
        Convert a BaseModel object's attributes to a dictionary.
        '''
        d = {}
        d['state'] = self.state
        d['state_time'] = self.state_time
        return d

    def updateFromDict(self, d):
        '''
        Set base attributes based on the given dict.
        '''
        if 'state' in d:
            self.state = d['state']
        if 'state_time' in d:
            self.state_time = d['state_time']


class ImageBuild(BaseModel):
    '''
    Class representing a DIB image build within the ZooKeeper cluster.

    Note that the 'builder' attribute used to be used to uniquely identify
    the owner of an image build in ZooKeeper. Because hostname was used, if
    it ever changed, then we would get orphaned znodes. The 'builder_id'
    attribute was added as a replacement, keeping 'builder' to mean the
    same thing (which is why this attribute is not called 'hostname' or
    similar).
    '''
    VALID_STATES = set([BUILDING, READY, DELETING, FAILED])

    def __init__(self, image_name=None, build_id=None):
        super(ImageBuild, self).__init__(build_id)
        self._formats = []
        self._image_name = image_name  # Not serialized
        self.builder = None       # Hostname
        self.builder_id = None    # Unique ID
        self.username = None
        self.python_path = None
        self.shell_type = None

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<ImageBuild %s>' % d

    @property
    def formats(self):
        return sorted(self._formats)

    @formats.setter
    def formats(self, value):
        if not isinstance(value, list):
            raise TypeError("'formats' attribute must be a list type")
        self._formats = copy(value)

    def addFormat(self, fmt):
        self._formats.append(fmt)

    def toDict(self):
        '''
        Convert an ImageBuild object's attributes to a dictionary.
        '''
        d = super(ImageBuild, self).toDict()
        if self.builder is not None:
            d['builder'] = self.builder
        if self.builder_id is not None:
            d['builder_id'] = self.builder_id
        if len(self.formats):
            d['formats'] = ','.join(self.formats)
        d['username'] = self.username
        d['python_path'] = self.python_path
        d['shell_type'] = self.shell_type
        return d

    @staticmethod
    def fromDict(d, image_name=None, o_id=None):
        '''
        Create an ImageBuild object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized ImageBuild object.
        '''
        o = ImageBuild(image_name, o_id)
        o.updateFromDict(d)
        return o

    def updateFromDict(self, d):
        super().updateFromDict(d)
        self.builder = d.get('builder')
        self.builder_id = d.get('builder_id')
        self.username = d.get('username', 'zuul')
        self.python_path = d.get('python_path', '/usr/bin/python2')
        self.shell_type = d.get('shell_type')
        # Only attempt the split on non-empty string
        if d.get('formats', ''):
            self.formats = d.get('formats', '').split(',')


class ImageBuildRequest(object):
    """Class representing a manual build request.

    This doesn't need to derive from BaseModel since this class exists only
    to aggregate information about a build request.
    """
    def __init__(self, image_name, pending, state_time):
        self.image_name = image_name
        self.state_time = state_time
        self.pending = pending

    def __repr__(self):
        return "<ImageBuildRequest {}>".format(self.image_name)


class Image:
    """Class representing an image.

    This doesn't need to derive from BaseModel since it is not
    actually serialized to ZK.  It exists to hold some in memory flags
    and attributes.

    """

    def __init__(self, image_name):
        self.image_name = image_name
        self.paused = False

    def updateFromDict(self, data):
        pass


class ImageUpload(BaseModel):
    '''
    Class representing a provider image upload within the ZooKeeper cluster.
    '''
    VALID_STATES = set([UPLOADING, READY, DELETING, FAILED])

    def __init__(self, build_id=None, provider_name=None, image_name=None,
                 upload_id=None, username=None, python_path=None,
                 shell_type=None):
        super(ImageUpload, self).__init__(upload_id)
        self.build_id = build_id
        self.provider_name = provider_name
        self.image_name = image_name
        self.format = None
        self.username = username
        self.python_path = python_path
        self.shell_type = shell_type
        self.external_id = None      # Provider ID of the image
        self.external_name = None    # Provider name of the image

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['build_id'] = self.build_id
        d['provider_name'] = self.provider_name
        d['image_name'] = self.image_name
        d['format'] = self.format
        return '<ImageUpload %s>' % d

    def __eq__(self, other):
        if isinstance(other, ImageUpload):
            return (self.id == other.id and
                    self.provider_name == other.provider_name and
                    self.build_id == other.build_id and
                    self.image_name == other.image_name and
                    self.format == other.format)
        else:
            return False

    def toDict(self):
        '''
        Convert an ImageUpload object's attributes to a dictionary.
        '''
        d = super(ImageUpload, self).toDict()
        d['external_id'] = self.external_id
        d['external_name'] = self.external_name
        d['format'] = self.format
        d['username'] = self.username
        d['python_path'] = self.python_path
        d['shell_type'] = self.shell_type
        return d

    @staticmethod
    def fromDict(d, build_id, provider_name, image_name, upload_id):
        '''
        Create an ImageUpload object from a dictionary.

        :param dict d: The dictionary.
        :param str build_id: The build ID.
        :param str provider_name: The provider name.
        :param str image_name: The image name.
        :param str upload_id: The upload ID.

        :returns: An initialized ImageUpload object.
        '''
        o = ImageUpload(build_id, provider_name, image_name, upload_id)
        o.updateFromDict(d)
        return o

    def updateFromDict(self, d):
        super().updateFromDict(d)
        self.external_id = d.get('external_id')
        self.external_name = d.get('external_name')
        self.format = d.get('format')
        self.username = d.get('username', 'zuul')
        self.python_path = d.get('python_path', '/usr/bin/python2')
        self.shell_type = d.get('shell_type')


class NodeRequestLockStats(object):
    '''
    Class holding the stats of a node request lock znode.

    This doesn't need to derive from BaseModel since this class exists only
    to associate the znode stats with the lock.
    '''
    def __init__(self, lock_id=None):
        self.lock_id = lock_id
        self.stat = None

    def __eq__(self, other):
        if isinstance(other, NodeRequestLockStats):
            return (self.lock_id == other.lock_id)
        else:
            return False

    def __repr__(self):
        return '<NodeRequestLockStats %s>' % self.lock_id


class NodeRequest(BaseModel):
    '''
    Class representing a node request.
    '''
    VALID_STATES = set([REQUESTED, PENDING, FULFILLED, FAILED])

    def __init__(self, id=None):
        super(NodeRequest, self).__init__(id)
        self.lock = None
        # Local thread lock that is acquired when we are manipulating
        # the ZK lock.
        self._thread_lock = threading.Lock()
        self.declined_by = []
        self.node_types = []
        self.nodes = []
        self.reuse = True
        # The identity of the requestor; used in reporting statistics
        self.requestor = None
        # Opaque data for use by the requestor (unused by Nodepool)
        self.requestor_data = None
        self.provider = None
        self.relative_priority = 0
        self.event_id = None
        self.created_time = None
        self.tenant_name = None

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<NodeRequest %s>' % d

    def __eq__(self, other):
        if isinstance(other, NodeRequest):
            return (self.id == other.id and
                    self.declined_by == other.declined_by and
                    self.node_types == other.node_types and
                    self.nodes == other.nodes and
                    self.reuse == other.reuse and
                    self.requestor == other.requestor and
                    self.requestor_data == other.requestor_data and
                    self.provider == other.provider and
                    self.relative_priority == other.relative_priority and
                    self.created_time == other.created_time and
                    self.tenant_name == other.tenant_name)
        else:
            return False

    @property
    def priority(self):
        # Sort requests by queue priority, then, for all requests at
        # the same priority, use the relative_priority field to
        # further sort, then finally, the submission order.
        precedence, sequence = self.id.split('-')
        return precedence, self.relative_priority, sequence

    def toDict(self):
        '''
        Convert a NodeRequest object's attributes to a dictionary.
        '''
        d = super(NodeRequest, self).toDict()
        d['declined_by'] = self.declined_by
        d['node_types'] = self.node_types
        d['nodes'] = self.nodes
        d['reuse'] = self.reuse
        d['requestor'] = self.requestor
        d['requestor_data'] = self.requestor_data
        d['provider'] = self.provider
        d['relative_priority'] = self.relative_priority
        d['event_id'] = self.event_id
        d['created_time'] = self.created_time
        d['tenant_name'] = self.tenant_name
        return d

    @staticmethod
    def fromDict(d, o_id=None):
        '''
        Create a NodeRequest object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized NodeRequest object.
        '''
        o = NodeRequest(o_id)
        o.updateFromDict(d)
        return o

    def updateFromDict(self, d):
        super().updateFromDict(d)
        self.declined_by = d.get('declined_by', [])
        self.node_types = d.get('node_types', [])
        self.nodes = d.get('nodes', [])
        self.reuse = d.get('reuse', True)
        self.requestor = d.get('requestor')
        self.requestor_data = d.get('requestor_data')
        self.provider = d.get('provider')
        self.relative_priority = d.get('relative_priority', 0)
        self.event_id = d.get('event_id')
        self.created_time = d.get('created_time')
        self.tenant_name = d.get('tenant_name')

    def getSafeAttributes(self):
        '''Return a dict of attributes safe for user-visible templating'''
        return Attributes(
            id=self.id,
            labels=self.node_types,
            requestor=self.requestor,
            requestor_data=self.requestor_data,
            relative_priority=self.relative_priority,
            event_id=self.event_id,
            created_time=self.created_time,
            tenant_name=self.tenant_name,
        )


class Node(BaseModel):
    '''
    Class representing a launched node.
    '''
    VALID_STATES = set([BUILDING, TESTING, READY, IN_USE, USED,
                        HOLD, DELETING, FAILED, INIT, ABORTED,
                        DELETED])

    def __init__(self, id=None):
        super(Node, self).__init__(id)
        # Local lock object; not serialized
        self.lock = None
        # Local thread lock that is acquired when we are manipulating
        # the ZK lock.
        self._thread_lock = threading.Lock()
        # Cached list of lock contenders; not serialized (and possibly
        # not up to date; use for status listings only).
        self.lock_contenders = set()
        self.cloud = None
        self.provider = None
        self.pool = None
        self.__type = []
        self.allocated_to = None
        self.az = None
        self.region = None
        self.public_ipv4 = None
        self.private_ipv4 = None
        self.public_ipv6 = None
        self.host_id = None
        self.interface_ip = None
        self.connection_type = None
        self.connection_port = 22
        self.shell_type = None
        self.image_id = None
        self.launcher = None
        self.created_time = None
        self.external_id = None
        self.hostname = None
        self.comment = None
        self.user_data = None
        self.hold_job = None
        self.username = None
        self.host_keys = []
        self.hold_expiration = None
        self.resources = None
        self.slot = None
        self.attributes = None
        self.python_path = None
        self.tenant_name = None
        self.driver_data = None
        self.requestor = None

    def __repr__(self):
        d = self.toDict()
        d['id'] = self.id
        d['stat'] = self.stat
        return '<Node %s>' % d

    def __eq__(self, other):
        if isinstance(other, Node):
            return (self.id == other.id and
                    self.cloud == other.cloud and
                    self.state == other.state and
                    self.state_time == other.state_time and
                    self.provider == other.provider and
                    self.pool == other.pool and
                    self.type == other.type and
                    self.allocated_to == other.allocated_to and
                    self.az == other.az and
                    self.region == other.region and
                    self.public_ipv4 == other.public_ipv4 and
                    self.private_ipv4 == other.private_ipv4 and
                    self.public_ipv6 == other.public_ipv6 and
                    self.host_id == other.host_id and
                    self.interface_ip == other.interface_ip and
                    self.image_id == other.image_id and
                    self.launcher == other.launcher and
                    self.created_time == other.created_time and
                    self.external_id == other.external_id and
                    self.hostname == other.hostname and
                    self.comment == other.comment and
                    self.user_data == other.user_data and
                    self.hold_job == other.hold_job and
                    self.username == other.username and
                    self.connection_type == other.connection_type and
                    self.connection_port == other.connection_port and
                    self.shell_type == other.shell_type and
                    self.host_keys == other.host_keys and
                    self.hold_expiration == other.hold_expiration and
                    self.resources == other.resources and
                    self.slot == other.slot and
                    self.attributes == other.attributes and
                    self.python_path == other.python_path and
                    self.tenant_name == other.tenant_name and
                    self.driver_data == other.driver_data and
                    self.requestor == other.requestor)
        else:
            return False

    @property
    def type(self):
        return self.__type

    @type.setter
    def type(self, value):
        # Using as_list() here helps us to transition from existing Nodes
        # in the ZooKeeper database that still use string.
        self.__type = as_list(value)

    def toDict(self):
        '''
        Convert a Node object's attributes to a dictionary.
        '''
        d = super(Node, self).toDict()
        d['cloud'] = self.cloud
        d['provider'] = self.provider
        d['pool'] = self.pool
        d['type'] = self.type
        d['allocated_to'] = self.allocated_to
        d['az'] = self.az
        d['region'] = self.region
        d['public_ipv4'] = self.public_ipv4
        d['private_ipv4'] = self.private_ipv4
        d['public_ipv6'] = self.public_ipv6
        d['host_id'] = self.host_id
        d['interface_ip'] = self.interface_ip
        d['connection_port'] = self.connection_port
        # TODO(tobiash): ssh_port is kept for backwards compatibility reasons
        # to zuul. It should be removed after some deprecation time.
        d['ssh_port'] = self.connection_port
        d['image_id'] = self.image_id
        d['launcher'] = self.launcher
        d['created_time'] = self.created_time
        d['external_id'] = self.external_id
        d['hostname'] = self.hostname
        d['comment'] = self.comment
        d['user_data'] = self.user_data
        d['hold_job'] = self.hold_job
        d['host_keys'] = self.host_keys
        d['username'] = self.username
        d['connection_type'] = self.connection_type
        d['connection_port'] = self.connection_port
        d['shell_type'] = self.shell_type
        d['hold_expiration'] = self.hold_expiration
        d['resources'] = self.resources
        d['slot'] = self.slot
        d['attributes'] = self.attributes
        d['python_path'] = self.python_path
        d['tenant_name'] = self.tenant_name
        d['driver_data'] = self.driver_data
        d['requestor'] = self.requestor
        return d

    @staticmethod
    def fromDict(d, o_id=None):
        '''
        Create a Node object from a dictionary.

        :param dict d: The dictionary.
        :param str o_id: The object ID.

        :returns: An initialized Node object.
        '''
        o = Node(o_id)
        o.updateFromDict(d)
        return o

    def updateFromDict(self, d):
        '''
        Updates the Node object from a dictionary

        :param dict d: The dictionary
        '''
        super().updateFromDict(d)
        self.cloud = d.get('cloud')
        self.provider = d.get('provider')
        self.pool = d.get('pool')
        self.type = d.get('type')
        self.allocated_to = d.get('allocated_to')
        self.az = d.get('az')
        self.region = d.get('region')
        self.public_ipv4 = d.get('public_ipv4')
        self.private_ipv4 = d.get('private_ipv4')
        self.public_ipv6 = d.get('public_ipv6')
        self.host_id = d.get('host_id')
        self.interface_ip = d.get('interface_ip')
        self.connection_port = d.get('connection_port', d.get('ssh_port', 22))
        self.image_id = d.get('image_id')
        self.launcher = d.get('launcher')
        self.created_time = d.get('created_time')
        self.external_id = d.get('external_id')
        self.hostname = d.get('hostname')
        self.comment = d.get('comment')
        self.user_data = d.get('user_data')
        self.hold_job = d.get('hold_job')
        self.username = d.get('username', 'zuul')
        self.connection_type = d.get('connection_type')
        self.host_keys = d.get('host_keys', [])
        hold_expiration = d.get('hold_expiration')
        if hold_expiration is not None:
            try:
                # We try to force this to an integer value because we do
                # relative second based age comparisons using this value
                # and those need to be a number type.
                self.hold_expiration = int(hold_expiration)
            except ValueError:
                # Coercion to int failed, just use default of 0,
                # which means no expiration
                self.hold_expiration = 0
        else:
            self.hold_expiration = hold_expiration
        self.resources = d.get('resources')
        self.slot = d.get('slot')
        self.attributes = d.get('attributes')
        self.python_path = d.get('python_path')
        self.shell_type = d.get('shell_type')
        self.tenant_name = d.get('tenant_name')
        self.driver_data = d.get('driver_data')
        self.requestor = d.get('requestor')


class NodepoolTreeCache(abc.ABC):
    '''
    Use watchers to keep a cache of local Nodepool objects up to date.
    '''

    log = logging.getLogger("nodepool.zk.ZooKeeper")
    event_log = logging.getLogger("nodepool.zk.cache.event")
    qsize_warning_threshold = 1024

    def __init__(self, zk, root):
        self.zk = zk
        self.root = root
        self._last_event_warning = time.monotonic()
        self._last_playback_warning = time.monotonic()
        self._cached_objects = {}
        self._cached_paths = set()
        self._ready = threading.Event()
        self._init_lock = threading.Lock()
        self._stopped = False
        self._stop_workers = False
        self._event_queue = queue.Queue()
        self._playback_queue = queue.Queue()
        self._event_worker = None
        self._playback_worker = None
        zk.kazoo_client.add_listener(self._sessionListener)
        self._start()

    def _sessionListener(self, state):
        if state == KazooState.LOST:
            self._ready.clear()
            self._stop_workers = True
            self._event_queue.put(None)
            self._playback_queue.put(None)
        elif state == KazooState.CONNECTED and not self._stopped:
            self.zk.kazoo_client.handler.short_spawn(self._start)

    def _cacheListener(self, event):
        self._event_queue.put(event)

    def _start(self):
        with self._init_lock:
            self.log.debug("Initialize cache at %s", self.root)

            self._ready.clear()
            self._stop_workers = True
            self._event_queue.put(None)
            self._playback_queue.put(None)

            # If we have an event worker (this is a re-init), then wait
            # for it to finish stopping.
            if self._event_worker:
                self._event_worker.join()
            # Replace the queue since any events from the previous
            # session aren't valid.
            self._event_queue = queue.Queue()
            # Prepare (but don't start) the new worker.
            self._event_worker = threading.Thread(
                target=self._eventWorker)
            self._event_worker.daemon = True

            if self._playback_worker:
                self._playback_worker.join()
            self._playback_queue = queue.Queue()
            self._playback_worker = threading.Thread(
                target=self._playbackWorker)
            self._playback_worker.daemon = True

            # Clear the stop flag and start the workers now that we
            # are sure that both have stopped and we have cleared the
            # queues.
            self._stop_workers = False
            self._event_worker.start()
            self._playback_worker.start()

            try:
                self.zk.kazoo_client.add_watch(
                    self.root, self._cacheListener,
                    AddWatchMode.PERSISTENT_RECURSIVE)
                self._walkTree()
                self._ready.set()
                self.log.debug("Cache at %s is ready", self.root)
            except Exception:
                self.log.exception("Error initializing cache at %s", self.root)
                self.zk.kazoo_client.handler.short_spawn(self._start)

    def stop(self):
        self._stopped = True
        self._event_queue.put(None)
        self._playback_queue.put(None)

    def _walkTree(self, root=None, seen_paths=None):
        # Recursively walk the tree and emit fake changed events for
        # every item in zk and fake deleted events for every item in
        # the cache that is not in zk
        exists = True
        am_root = False
        if root is None:
            am_root = True
            root = self.root
            seen_paths = set()
            if not self.zk.kazoo_client.exists(root):
                exists = False
        if exists:
            seen_paths.add(root)
            event = WatchedEvent(EventType.NONE,
                                 self.zk.kazoo_client._state,
                                 root)
            self._cacheListener(event)
            try:
                for child in self.zk.kazoo_client.get_children(root):
                    safe_root = root
                    if safe_root == '/':
                        safe_root = ''
                    new_path = '/'.join([safe_root, child])
                    self._walkTree(new_path, seen_paths)
            except kze.NoNodeError:
                self.log.debug("Can't sync non-existent node %s", root)
        if am_root:
            for path in self._cached_paths.copy():
                if path not in seen_paths:
                    event = WatchedEvent(
                        EventType.NONE,
                        self.zk.kazoo_client._state,
                        path)
                    self._cacheListener(event)

    def _eventWorker(self):
        while not (self._stopped or self._stop_workers):
            event = self._event_queue.get()
            if event is None:
                self._event_queue.task_done()
                continue

            qsize = self._event_queue.qsize()
            if qsize > self.qsize_warning_threshold:
                now = time.monotonic()
                if now - self._last_event_warning > 60:
                    self.log.warning("Event queue size for cache at %s is %s",
                                     self.root, qsize)
                    self._last_event_warning = now

            try:
                self._handleCacheEvent(event)
            except Exception:
                self.log.exception("Error handling event %s:", event)
            self._event_queue.task_done()

    def _handleCacheEvent(self, event):
        # Ignore root node since we don't maintain a cached object for
        # it (all cached objects are under the root in our tree
        # caches).
        if event.path == self.root:
            return

        # Start by assuming we need to fetch data for the event.
        fetch = True
        if event.type == EventType.NONE:
            if event.path is None:
                # We're probably being told of a connection change; ignore.
                return
        elif (event.type == EventType.DELETED):
            # If this is a normal deleted event, we don't need to
            # fetch anything.
            fetch = False

        key = self.parsePath(event.path)
        if key is None and event.type != EventType.NONE:
            # The cache doesn't care about this path, so we don't need
            # to fetch (unless the type is none (re-initialization) in
            # which case we always need to fetch in order to determine
            # existence).
            fetch = False

        if fetch:
            future = self.zk.kazoo_client.get_async(event.path)
        else:
            future = None
        self._playback_queue.put((event, future, key))

    def _playbackWorker(self):
        while not (self._stopped or self._stop_workers):
            item = self._playback_queue.get()
            if item is None:
                self._playback_queue.task_done()
                continue

            qsize = self._playback_queue.qsize()
            if qsize > self.qsize_warning_threshold:
                now = time.monotonic()
                if now - self._last_playback_warning > 60:
                    self.log.warning(
                        "Playback queue size for cache at %s is %s",
                        self.root, qsize)
                    self._last_playback_warning = now

            event, future, key = item
            try:
                self._handlePlayback(event, future, key)
            except Exception:
                self.log.exception("Error playing back event %s:", event)
            self._playback_queue.task_done()

    def _handlePlayback(self, event, future, key):
        self.event_log.debug("Cache playback event %s", event)
        exists = None
        data, stat = None, None

        if future:
            try:
                data, stat = future.get()
                exists = True
            except kze.NoNodeError:
                exists = False

        # We set "exists" above in case of cache re-initialization,
        # which happens out of sequence with the normal watch events.
        # and we can't be sure whether the node still exists or not by
        # the time we process it.  Later cache watch events may
        # supercede this (for example, we may process a NONE event
        # here which we interpret as a delete which may be followed by
        # a normal delete event.  That case, and any other variations
        # should be anticipated.

        # If the event tells us whether the node exists, prefer that
        # value, otherwise fallback to what we determined above.
        if (event.type in (EventType.CREATED, EventType.CHANGED)):
            exists = True
        elif (event.type == EventType.DELETED):
            exists = False

        # Keep the cached paths up to date
        if exists:
            self._cached_paths.add(event.path)
        else:
            self._cached_paths.discard(event.path)

        # Some caches have special handling for certain sub-objects
        self.preCacheHook(event, exists)

        # If we don't actually cache this kind of object, return now
        if key is None:
            return

        if data:
            data = self.zk._bytesToDict(data)

            # Perform an in-place update of the cached object if possible
            old_obj = self._cached_objects.get(key)
            if old_obj:
                if stat.mzxid <= old_obj.stat.mzxid:
                    # Don't update to older data
                    return
                if getattr(old_obj, 'lock', None):
                    # Don't update a locked object
                    return
                old_obj.updateFromDict(data)
                old_obj.stat = stat
            else:
                obj = self.objectFromDict(data, key)
                obj.stat = stat
                self._cached_objects[key] = obj
        else:
            try:
                del self._cached_objects[key]
            except KeyError:
                # If it's already gone, don't care
                pass
        self.postCacheHook(event, data, stat)

    def ensureReady(self):
        self._ready.wait()

    # Methods for subclasses:
    def preCacheHook(self, event, exists):
        """Called before the cache is updated

        This is called for any add/update/remove event under the root,
        even for paths that are ignored, so users much test the
        relevance of the path in this method.

        The ``exists`` argument is provided in all cases. In the case
        of EventType.NONE events, it indicates whether the cache has
        seen the node in ZK immediately before calling this method.
        Otherwise, it indicates whether or not the EventType would
        cause the node to exist in ZK.

        :param EventType event: The event.
        :param bool exists: Whether the object exists in ZK.

        """
        return None

    def postCacheHook(self, event, data, stat):
        """Called after the cache has been updated"""
        return None

    @abc.abstractmethod
    def parsePath(self, path):
        """Parse the path and return a cache key

        The cache key is an opaque object ignored by the cache, but
        must be hashable.

        A convention is to use a tuple of relevant path components as
        the key.

        Return None to indicate the path is not relevant to the cache.

        """
        return None

    @abc.abstractmethod
    def objectFromDict(self, d, key):
        """Construct an object from ZooKeeper data

        Given a dictionary of data from ZK and cache key, construct
        and return an object to insert into the cache.

        :param dict d: The dictionary.
        :param object key: The key as returned by parsePath.
        """
        pass


class ImageCache(NodepoolTreeCache):
    def parsePath(self, path):
        r = self.zk._parseImageUploadPath(path)
        if not r:
            r = self.zk._parseImageBuildPath(path)
        if not r:
            r = self.zk._parseImagePath(path)
        return r

    def preCacheHook(self, event, exists):
        key = self.zk._parseImagePausePath(event.path)
        if key is None:
            return
        # A pause flag is being added or removed
        # The image key is identical to the image pause path key.
        image = self._cached_objects.get(key)
        if not image:
            return
        if exists:
            image.paused = True
        else:
            image.paused = False
        return

    def objectFromDict(self, d, key):
        if len(key) == 4:
            image, build_id, provider, upload_number = key
            return ImageUpload.fromDict(d,
                                        build_id,
                                        provider,
                                        image,
                                        upload_number)
        elif len(key) == 2:
            image, build_id = key
            return ImageBuild.fromDict(d,
                                       image,
                                       build_id)
        elif len(key) == 1:
            image = key[0]
            return Image(image)

    def getImages(self):
        self.ensureReady()
        items = self._cached_objects.copy().items()
        items = sorted(items, key=lambda x: x[0])
        return [x[1] for x in items
                if isinstance(x[1], Image)]

    def getBuilds(self):
        self.ensureReady()
        items = self._cached_objects.copy().items()
        items = sorted(items, key=lambda x: x[0])
        return [x[1] for x in items
                if isinstance(x[1], ImageBuild)]

    def getUploads(self):
        self.ensureReady()
        items = self._cached_objects.copy().items()
        items = sorted(items, key=lambda x: x[0])
        return [x[1] for x in items
                if isinstance(x[1], ImageUpload)]


class NodeCache(NodepoolTreeCache):
    def parsePath(self, path):
        return self.zk._parseNodePath(path)

    def preCacheHook(self, event, exists):
        key = self.zk._parseNodeLockPath(event.path)
        if key is None:
            return
        # A lock contender is being added or removed
        node_id, contender = key
        # Construct a key for the node object
        obj_key = (node_id,)
        node = self._cached_objects.get(obj_key)
        if not node:
            return
        if exists:
            node.lock_contenders.add(contender)
        else:
            node.lock_contenders.discard(contender)
        return

    def postCacheHook(self, event, data, stat):
        # set the stats event so the stats reporting thread can act upon it
        if self.zk.node_stats_event is not None:
            self.zk.node_stats_event.set()

    def objectFromDict(self, d, key):
        node_id = key[0]
        return Node.fromDict(d, node_id)

    def getNode(self, node_id):
        self.ensureReady()
        return self._cached_objects.get((node_id,))

    def getNodeIds(self):
        # get a copy of the values view to avoid runtime errors in the event
        # the _cached_nodes dict gets updated while iterating
        self.ensureReady()
        return [x.id for x in list(self._cached_objects.values())]


class RequestCache(NodepoolTreeCache):
    def parsePath(self, path):
        return self.zk._parseRequestPath(path)

    def objectFromDict(self, d, key):
        request_id = key[0]
        return NodeRequest.fromDict(d, request_id)

    def getNodeRequest(self, request_id):
        self.ensureReady()
        return self._cached_objects.get((request_id,))

    def getNodeRequestIds(self):
        # get a copy of the values view to avoid runtime errors in the event
        # the _cached_nodes dict gets updated while iterating
        self.ensureReady()
        return [x.id for x in list(self._cached_objects.values())]


class ZooKeeper(ZooKeeperBase):
    '''
    Class implementing the ZooKeeper interface.

    This class uses the facade design pattern to keep common interaction
    with the ZooKeeper API simple and consistent for the caller, and
    limits coupling between objects. It allows for more complex interactions
    by providing direct access to the client connection when needed (though
    that is discouraged). It also provides for a convenient entry point for
    testing only ZooKeeper interactions.

    Most API calls reference an image name only, as the path for the znode
    for that image is calculated automatically. And image names are assumed
    to be unique.
    '''

    log = logging.getLogger("nodepool.zk.ZooKeeper")
    event_log = logging.getLogger("nodepool.zk.cache.event")

    IMAGE_ROOT = "/nodepool/images"
    LAUNCHER_ROOT = "/nodepool/launchers"
    NODE_ROOT = "/nodepool/nodes"
    REQUEST_ROOT = "/nodepool/requests"
    REQUEST_LOCK_ROOT = "/nodepool/requests-lock"
    ELECTION_ROOT = "/nodepool/elections"

    # Log zookeeper retry every 10 seconds
    retry_log_rate = 10

    def __init__(self, client, enable_cache=True):
        '''
        Initialize the ZooKeeper object.
        '''
        super().__init__(client)
        self._last_retry_log = 0
        self._node_cache = None
        self._request_cache = None
        self._image_cache = None
        self.enable_cache = enable_cache
        self.node_stats_event = None

        if self.client.connected:
            self._onConnect()

        COMPONENT_REGISTRY.create(self.client)

    # =======================================================================
    # Private Methods
    # =======================================================================
    def _onConnect(self):
        if self.enable_cache and self._node_cache is None:
            self._node_cache = NodeCache(self, self.NODE_ROOT)
            self._request_cache = RequestCache(self, self.REQUEST_ROOT)
            self._image_cache = ImageCache(self, self.IMAGE_ROOT)

    def _electionPath(self, election):
        return "%s/%s" % (self.ELECTION_ROOT, election)

    def _imagePath(self, image):
        return "%s/%s" % (self.IMAGE_ROOT, image)

    def _parseImagePath(self, path):
        if not path.startswith(self.IMAGE_ROOT):
            return None
        path = path[len(self.IMAGE_ROOT):]
        parts = path.split('/')
        if len(parts) != 2:
            return None
        if parts[-1] == 'lock':
            return
        image = parts[1]
        return (image,)

    def _imageBuildRequestPath(self, image):
        return "%s/request-build" % self._imagePath(image)

    def _imageBuildsPath(self, image):
        return "%s/builds" % self._imagePath(image)

    def _imagePausePath(self, image):
        return "%s/pause" % self._imagePath(image)

    def _parseImagePausePath(self, path):
        if not path.startswith(self.IMAGE_ROOT):
            return None
        path = path[len(self.IMAGE_ROOT):]
        parts = path.split('/')
        if len(parts) != 3:
            return None
        if parts[2] != 'pause':
            return None
        image = parts[1]
        return (image,)

    def _imageBuildIdPath(self, image, build_id):
        return "%s/%s" % (self._imageBuildsPath(image), build_id)

    def _parseImageBuildPath(self, path):
        if not path.startswith(self.IMAGE_ROOT):
            return None
        path = path[len(self.IMAGE_ROOT):]
        parts = path.split('/')
        if len(parts) != 4:
            return None
        if parts[-1] == 'lock':
            return
        image = parts[1]
        build = parts[3]
        return image, build

    def _imageBuildLockPath(self, image):
        return "%s/lock" % self._imageBuildsPath(image)

    def _imageBuildIdLockPath(self, image, build_id):
        return "%s/lock" % self._imageBuildIdPath(image, build_id)

    def _imageProviderPath(self, image, build_id):
        return "%s/%s/providers" % (self._imageBuildsPath(image),
                                    build_id)

    def _imageUploadPath(self, image, build_id, provider):
        return "%s/%s/providers/%s/images" % (self._imageBuildsPath(image),
                                              build_id,
                                              provider)

    def _parseImageUploadPath(self, path):
        if not path.startswith(self.IMAGE_ROOT):
            return None
        path = path[len(self.IMAGE_ROOT):]
        parts = path.split('/')
        if len(parts) != 8:
            return None
        if parts[-1] == 'lock':
            return
        image = parts[1]
        build = parts[3]
        provider = parts[5]
        upload = parts[7]
        return image, build, provider, upload

    def _imageUploadLockPath(self, image, build_id, provider):
        return "%s/lock" % self._imageUploadPath(image, build_id,
                                                 provider)

    def _imageUploadNumberLockPath(self, image, build_id, provider,
                                   upload_number):
        return "%s/%s/lock" % (
            self._imageUploadPath(image, build_id, provider),
            upload_number)

    def _launcherPath(self, launcher):
        return "%s/%s" % (self.LAUNCHER_ROOT, launcher)

    def _nodePath(self, node):
        return "%s/%s" % (self.NODE_ROOT, node)

    def _parseNodePath(self, path):
        if not path.startswith(self.NODE_ROOT):
            return None
        path = path[len(self.NODE_ROOT):]
        parts = path.split('/')
        if len(parts) != 2:
            return None
        if parts[-1] == 'lock':
            return
        node = parts[1]
        return (node,)

    def _nodeLockPath(self, node):
        return "%s/%s/lock" % (self.NODE_ROOT, node)

    def _parseNodeLockPath(self, path):
        if not path.startswith(self.NODE_ROOT):
            return None
        path = path[len(self.NODE_ROOT):]
        parts = path.split('/')
        if len(parts) != 4:
            return None
        if parts[2] != 'lock':
            return None
        node = parts[1]
        contender = parts[3]
        return (node, contender)

    def _requestPath(self, request):
        return "%s/%s" % (self.REQUEST_ROOT, request)

    def _parseRequestPath(self, path):
        if not path.startswith(self.REQUEST_ROOT):
            return None
        path = path[len(self.REQUEST_ROOT):]
        parts = path.split('/')
        if len(parts) != 2:
            return None
        if parts[-1] == 'lock':
            return
        request = parts[1]
        return (request,)

    def _requestLockPath(self, request):
        return "%s/%s" % (self.REQUEST_LOCK_ROOT, request)

    def _bytesToDict(self, data):
        return json.loads(data.decode('utf8'))

    def _getImageBuildLock(self, image, blocking=True, timeout=None):
        lock_path = self._imageBuildLockPath(image)
        try:
            lock = Lock(self.kazoo_client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image build not found for locking: %s", image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _getImageBuildIdLock(self, image, build_id,
                             blocking=True, timeout=None):
        lock_path = self._imageBuildIdLockPath(image, build_id)
        try:
            lock = Lock(self.kazoo_client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image build id not found for locking: %s, %s",
                           build_id, image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _getImageUploadNumberLock(self, image, build_id, provider,
                                  upload_number, blocking=True, timeout=None):
        lock_path = self._imageUploadNumberLockPath(image, build_id,
                                                    provider, upload_number)
        try:
            lock = Lock(self.kazoo_client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image upload number %s not found for locking: "
                           "%s, %s, %s",
                           upload_number, build_id, provider, image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def _getImageUploadLock(self, image, build_id, provider,
                            blocking=True, timeout=None):
        lock_path = self._imageUploadLockPath(image, build_id, provider)
        try:
            lock = Lock(self.kazoo_client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % lock_path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Image upload not found for locking: %s, %s, %s",
                           build_id, provider, image)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % lock_path)

        return lock

    def logConnectionRetryEvent(self):
        '''
        Kazoo retry callback
        '''
        now = time.monotonic()
        if now - self._last_retry_log >= self.retry_log_rate:
            self.log.warning("Retrying zookeeper connection")
            self._last_retry_log = now

    # =======================================================================
    # Public Methods and Properties
    # =======================================================================

    @property
    def connected(self):
        return self.client.connected

    @property
    def suspended(self):
        return self.client.suspended

    @property
    def lost(self):
        return self.client.lost

    def disconnect(self):
        '''
        Close the ZooKeeper cluster connection.

        You should call this method if you used connect() to establish a
        cluster connection.
        '''

        self.client.disconnect()

    def resetHosts(self, hosts):
        '''
        Reset the ZooKeeper cluster connection host list.

        :param str host_list: A ZK host list
        '''
        self.client.resetHosts(hosts)

    def reportStats(self, statsd, root_key):
        '''
        Report stats using the supplied statsd object.

        :param statsd statsd: A statsd instance
        :param str root_key: The root key (eg 'nodepool.launcher.hostname')
        '''

        pipeline = statsd.pipeline()

        key = f'{root_key}.zk.client.connection_queue'
        pipeline.gauge(key, len(self.client.client._queue))

        key = f'{root_key}.zk.node_cache.event_queue'
        pipeline.gauge(key, self._node_cache._event_queue.qsize())
        key = f'{root_key}.zk.node_cache.playback_queue'
        pipeline.gauge(key, self._node_cache._playback_queue.qsize())

        key = f'{root_key}.zk.request_cache.event_queue'
        pipeline.gauge(key, self._request_cache._event_queue.qsize())
        key = f'{root_key}.zk.request_cache.playback_queue'
        pipeline.gauge(key, self._request_cache._playback_queue.qsize())

        key = f'{root_key}.zk.image_cache.event_queue'
        pipeline.gauge(key, self._image_cache._event_queue.qsize())
        key = f'{root_key}.zk.image_cache.playback_queue'
        pipeline.gauge(key, self._image_cache._playback_queue.qsize())

        pipeline.send()

    @contextmanager
    def imageBuildLock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking image builds.

        Obtains a write lock for the specified image.

        :param str image: Name of the image to lock
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageBuildLock(image, blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    @contextmanager
    def imageBuildIdLock(self, image, build_id,
                         blocking=True, timeout=None):
        '''
        Context manager to use for locking _specific_ image builds.

        Obtains a write lock for the specified image build id. This is
        used for locking a build id during the cleanup phase of the
        builder.

        :param str image: Name of the image
        :param str build_id: The image build id to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageBuildIdLock(image, build_id,
                                             blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    @contextmanager
    def imageUploadLock(self, image, build_id, provider,
                        blocking=True, timeout=None):
        '''
        Context manager to use for locking image uploads.

        Obtains a write lock for the specified image upload.

        :param str image: Name of the image.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageUploadLock(image, build_id, provider,
                                            blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    @contextmanager
    def imageUploadNumberLock(self, image_upload, blocking=True, timeout=None):
        '''
        Context manager to use for locking _specific_ image uploads.

        Obtains a write lock for the specified image upload number.

        :param ImageUpload image_upload: The object describing the upload
            to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        lock = None
        try:
            lock = self._getImageUploadNumberLock(image_upload.image_name,
                                                  image_upload.build_id,
                                                  image_upload.provider_name,
                                                  image_upload.id,
                                                  blocking, timeout)
            yield
        finally:
            if lock:
                lock.release()

    def getImageNames(self):
        '''
        Retrieve the image names in Zookeeper.

        :returns: A list of image names or the empty list.
        '''
        path = self.IMAGE_ROOT

        try:
            images = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []
        return sorted(images)

    def getCachedImages(self):
        '''
        Retrieve all cached images

        :returns: A list of Image objects.
        '''
        if not self.enable_cache:
            raise RuntimeError("Caching not enabled")
        return self._image_cache.getImages()

    def getImagePaused(self, image):
        '''
        Return the pause flag for an image.

        :returns: A boolean indicating if the image is paused.
        '''
        path = self._imagePausePath(image)

        try:
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return False
        return True

    def setImagePaused(self, image, paused):
        '''
        Set or clear the pause flag for an image.
        '''
        path = self._imagePausePath(image)

        if paused:
            try:
                self.kazoo_client.create(path)
            except kze.NodeExistsError:
                pass
        else:
            try:
                self.kazoo_client.delete(path)
            except kze.NoNodeError:
                pass

    def getBuildIds(self, image):
        '''
        Retrieve the builds available for an image.

        :param str image: The image name.

        :returns: A list of image build ids or the empty list.
        '''
        path = self._imageBuildsPath(image)

        try:
            builds = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []
        builds = [x for x in builds if x != 'lock']
        return builds

    def getBuildProviders(self, image, build_id):
        '''
        Retrieve the providers which have uploads for an image build.

        :param str image: The image name.
        :param str build_id: The image build id.

        :returns: A list of provider names or the empty list.

        '''
        path = self._imageProviderPath(image, build_id)

        try:
            providers = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []

        return sorted(providers)

    def getImageUploadNumbers(self, image, build_id, provider):
        '''
        Retrieve upload numbers for a provider and image build.

        :param str image: The image name.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.

        :returns: A list of upload numbers or the empty list.

        '''
        path = self._imageUploadPath(image, build_id, provider)

        try:
            uploads = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []

        uploads = [x for x in uploads if x != 'lock']
        return uploads

    def getBuild(self, image, build_id):
        '''
        Retrieve the image build data.

        :param str image: The image name.
        :param str build_id: The image build id.

        :returns: An ImageBuild object, or None if not found.
        '''
        path = self._imageBuildsPath(image) + "/%s" % build_id

        try:
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return None

        try:
            d = ImageBuild.fromDict(self._bytesToDict(data),
                                    image, build_id)
        except json.decoder.JSONDecodeError:
            self.log.exception('Error loading json data from image build %s',
                               path)
            return None
        d.stat = stat
        return d

    def getBuilds(self, image, states=None):
        '''
        Retrieve all image build data matching any given states.

        :param str image: The image name.
        :param list states: A list of build state values to match against.
            A value of None will disable state matching and just return
            all builds.

        :returns: A list of ImageBuild objects.
        '''
        path = self._imageBuildsPath(image)

        try:
            builds = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []

        matches = []
        for build in builds:
            if build == 'lock':   # skip the build lock node
                continue
            data = self.getBuild(image, build)
            if states is None:
                matches.append(data)
            elif data and data.state in states:
                matches.append(data)

        return matches

    def getCachedBuilds(self):
        '''
        Retrieve all image build data from the cache

        :returns: A list of ImageBuild objects.
        '''
        if not self.enable_cache:
            raise RuntimeError("Caching not enabled")
        return self._image_cache.getBuilds()

    def getMostRecentBuilds(self, count, image, state=None):
        '''
        Retrieve the most recent image build data with the given state.

        :param int count: A count of the most recent builds to return.
            Use None for all builds.
        :param str image: The image name.
        :param str state: The build state to match on. Use None to
            ignore state

        :returns: A list of the most recent ImageBuild objects matching the
            given state, or an empty list if there were no builds matching
            the state. You may get less than 'count' entries if there were
            not enough matching builds.
        '''
        states = None
        if state:
            states = [state]

        builds = self.getBuilds(image, states)
        if not builds:
            return []

        builds.sort(key=lambda x: x.state_time, reverse=True)
        return builds[:count]

    def storeBuild(self, image, build_data, build_id=None):
        '''
        Store the image build data.

        The build data is either created if it does not exist, or it is
        updated in its entirety if it does not. There is no partial updating.
        The build data is expected to be represented as a dict. This dict may
        contain any data, as appropriate.

        If a build id is not supplied, then a new build node/id is
        created. The new build id is available in the return value.

        .. important: You should have the image locked before calling this
            method.

        :param str image: The image name for which we have data.
        :param ImageBuild build_data: The build data.
        :param str build_id: The image build id.

        :returns: A string for the build id that was updated.
        '''
        # Append trailing / so the sequence node is created as a child node.
        build_path = self._imageBuildsPath(image) + "/"

        if build_id is None:
            build_id = uuid.uuid4().hex
            path = build_path + build_id
            self.kazoo_client.create(
                path,
                value=build_data.serialize(),
                makepath=True)
        else:
            path = build_path + build_id
            self.kazoo_client.set(path, build_data.serialize())

        return build_id

    def getImageUpload(self, image, build_id, provider, upload_number):
        '''
        Retrieve the image upload data.

        :param str image: The image name.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param str upload_number: The image upload number.

        :returns: An ImageUpload object, or None if not found.

        :raises: ZKException if the image upload path is not found.
        '''
        path = self._imageUploadPath(image, build_id, provider)
        path = path + "/%s" % upload_number

        try:
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return None

        try:
            d = ImageUpload.fromDict(self._bytesToDict(data),
                                     build_id,
                                     provider,
                                     image,
                                     upload_number)
        except json.decoder.JSONDecodeError:
            self.log.exception('Error loading json data from image upload %s',
                               path)
            return None
        d.stat = stat
        return d

    def getUploads(self, image, build_id, provider, states=None):
        '''
        Retrieve all image upload data matching any given states.

        :param str image: The image name.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param list states: A list of upload state values to match against.
            A value of None will disable state matching and just return
            all uploads.

        :returns: A list of ImageUpload objects.
        '''
        path = self._imageUploadPath(image, build_id, provider)

        try:
            uploads = self.kazoo_client.get_children(path)
        except kze.NoNodeError:
            return []

        matches = []
        for upload in uploads:
            if upload == 'lock':
                continue
            data = self.getImageUpload(image, build_id, provider, upload)
            if not data:
                continue
            if states is None:
                matches.append(data)
            elif data.state in states:
                matches.append(data)

        return matches

    def getMostRecentBuildImageUploads(self, count, image, build_id,
                                       provider, state=None):
        '''
        Retrieve the most recent image upload data with the given state.

        :param int count: A count of the most recent uploads to return.
            Use None for all uploads.
        :param str image: The image name.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param str state: The image upload state to match on. Use None to
            ignore state.

        :returns: A tuple with the most recent upload number and dictionary of
            upload data matching the given state, or None if there was no
            upload matching the state.
        '''
        states = None
        if state:
            states = [state]

        uploads = self.getUploads(image, build_id, provider, states)
        if not uploads:
            return []

        uploads.sort(key=lambda x: x.state_time, reverse=True)
        return uploads[:count]

    def getMostRecentImageUpload(self, image, provider,
                                 state=READY, cached=False):
        '''
        Retrieve the most recent image upload data with the given state.

        :param str image: The image name.
        :param str provider: The provider name owning the image.
        :param str state: The image upload state to match on.
        :param bool cached: Whether to use cached data.

        :returns: An ImageUpload object matching the given state, or
            None if there is no recent upload.
        '''
        uploads = []

        if cached:
            uploads = self.getCachedImageUploads()
        else:
            for build_id in self.getBuildIds(image):
                path = self._imageUploadPath(image, build_id, provider)
                try:
                    upload_numbers = self.kazoo_client.get_children(path)
                except kze.NoNodeError:
                    upload_numbers = []

                for upload_number in upload_numbers:
                    if upload_number == 'lock':   # skip the upload lock node
                        continue
                    data = self.getImageUpload(
                        image, build_id, provider, upload_number)
                    if not data or data.state != state:
                        continue
                    uploads.append(data)

        recent_upload = None
        for upload in uploads:
            if upload.image_name != image:
                continue
            if upload.provider_name != provider:
                continue
            if upload.state != state:
                continue
            if (recent_upload is None or
                recent_upload.state_time < upload.state_time):
                recent_upload = upload

        return recent_upload

    def getCachedImageUploads(self):
        '''
        Retrieve all image upload data from the cache

        :returns: A list of ImageUpload objects.
        '''
        if not self.enable_cache:
            raise RuntimeError("Caching not enabled")
        return self._image_cache.getUploads()

    def storeImageUpload(self, image, build_id, provider, image_data,
                         upload_number=None):
        '''
        Store the built image's upload data for the given provider.

        The image upload data is either created if it does not exist, or it
        is updated in its entirety if it does not. There is no partial
        updating. The image data is expected to be represented as a dict.
        This dict may contain any data, as appropriate.

        If an image upload number is not supplied, then a new image upload
        node/number is created. The new upload number is available in the
        return value.

        :param str image: The image name for which we have data.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param ImageUpload image_data: The image data we want to store.
        :param str upload_number: The image upload number to update.

        :returns: A string for the upload number that was updated.

        :raises: ZKException for an invalid image build.
        '''
        # We expect the image builds path to already exist.
        build_path = self._imageBuildsPath(image)
        if not self.kazoo_client.exists(build_path):
            raise npe.ZKException(
                "Cannot find build %s of image %s" % (build_id, image)
            )

        # Generate a path for the upload. This doesn't have to exist yet
        # since we'll create new provider/upload ID znodes automatically.
        # Append trailing / so the sequence node is created as a child node.
        upload_path = self._imageUploadPath(
            image, build_id, provider) + "/"

        if upload_number is None:
            path = self.kazoo_client.create(
                upload_path,
                value=image_data.serialize(),
                sequence=True,
                makepath=True)
            upload_number = path.split("/")[-1]
        else:
            path = upload_path + upload_number
            self.kazoo_client.set(path, image_data.serialize())

        return upload_number

    def hasBuildRequest(self, image):
        '''
        Check if an image has a pending build request.

        :param str image: The image name to check.

        :returns: True if request is pending, False otherwise
        '''
        path = self._imageBuildRequestPath(image)
        if self.kazoo_client.exists(path) is not None:
            return True
        return False

    def _latestImageBuildStat(self, image):
        builds = self.getBuildIds(image)
        if not builds:
            return

        latest_build, *_ = builds
        builds_path = self._imageBuildIdPath(image, latest_build)
        return self.kazoo_client.exists(builds_path)

    def getBuildRequest(self, image):
        """Get a build request for the given image.

        :param str image: The image name to check.

        :returns: An ImagebuildRequest object, or None if not found
        """
        path = self._imageBuildRequestPath(image)
        try:
            _, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return

        pending = True
        lock_path = self._imageBuildLockPath(image)
        lock_stat = self.kazoo_client.exists(lock_path)
        if lock_stat and lock_stat.children_count:
            build_stat = self._latestImageBuildStat(image)
            # If there is a lock, but no build we assume that the build
            # will was not yet created.
            pending = (
                build_stat is None or
                build_stat.created < lock_stat.created
            )

        return ImageBuildRequest(image, pending, stat.created)

    def submitBuildRequest(self, image):
        '''
        Submit a request for a new image build.

        :param str image: The image name.
        '''
        path = self._imageBuildRequestPath(image)
        self.kazoo_client.ensure_path(path)

    def removeBuildRequest(self, image):
        '''
        Remove an image build request.

        :param str image: The image name to check.
        '''
        path = self._imageBuildRequestPath(image)
        try:
            self.kazoo_client.delete(path)
        except kze.NoNodeError:
            pass

    def deleteBuild(self, image, build_id):
        '''
        Delete an image build from ZooKeeper.

        Any provider uploads for this build must be deleted before the build
        node can be deleted.

        :param str image: The image name.
        :param str build_id: The image build id to delete.

        :returns: True if the build is successfully deleted or did not exist,
           False if the provider uploads still exist.
        '''
        path = self._imageBuildsPath(image)
        path = path + "/%s" % build_id

        # Verify that no upload znodes exist.
        for prov in self.getBuildProviders(image, build_id):
            if self.getImageUploadNumbers(image, build_id, prov):
                return False

        try:
            # NOTE: Need to do recursively to remove lock znodes
            self.kazoo_client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

        return True

    def deleteUpload(self, image, build_id, provider, upload_number):
        '''
        Delete an image upload from ZooKeeper.

        :param str image: The image name.
        :param str build_id: The image build id.
        :param str provider: The provider name owning the image.
        :param str upload_number: The image upload number to delete.
        '''
        path = self._imageUploadPath(image, build_id, provider)
        path = path + "/%s" % upload_number
        try:
            # NOTE: Need to do recursively to remove lock znodes
            self.kazoo_client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def getRegisteredPools(self):
        '''
        Get a list of all launcher pools that have registered with ZooKeeper.

        :returns: A list of PoolComponent objects, or empty list if none
        are found.
        '''
        return list(COMPONENT_REGISTRY.registry.all(kind='pool'))

    def getNodeRequests(self):
        '''
        Get the current list of all node requests in priority sorted order.

        :returns: A list of request nodes.
        '''
        try:
            requests = self.kazoo_client.get_children(self.REQUEST_ROOT)
        except kze.NoNodeError:
            return []

        return sorted(requests)

    def getNodeRequestLockIDs(self):
        '''
        Get the current list of all node request lock ids.
        '''
        try:
            lock_ids = self.kazoo_client.get_children(self.REQUEST_LOCK_ROOT)
        except kze.NoNodeError:
            return []
        return lock_ids

    def getNodeRequestLockStats(self, lock_id):
        '''
        Get the data for a specific node request lock.

        Note that there is no user data set on a node request lock znode. The
        main purpose for this method is to get the ZK stat data for the lock
        so we can inspect it and use it for lock deletion.

        :param str lock_id: The node request lock ID.

        :returns: A NodeRequestLockStats object.
        '''
        path = self._requestLockPath(lock_id)
        try:
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return None
        d = NodeRequestLockStats(lock_id)
        d.stat = stat
        return d

    def deleteNodeRequestLock(self, lock_id):
        '''
        Delete the znode for a node request lock id.

        :param str lock_id: The lock ID.
        '''
        path = self._requestLockPath(lock_id)
        try:
            self.kazoo_client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def getNodeRequest(self, request, cached=False):
        '''
        Get the data for a specific node request.

        :param str request: The request ID.
        :param cached: True if cached node requests should be returned.

        :returns: The request data, or None if the request was not found.
        '''
        if cached and self._request_cache:
            d = self._request_cache.getNodeRequest(request)
            if d:
                return d

        # If we got here we either didn't use the cache or the cache didn't
        # have the request (yet). Note that even if we use caching we need to
        # do a real query if the cached data is empty because the request data
        # might not be in the cache yet when it's listed by the get_children
        # call.
        try:
            path = self._requestPath(request)
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return None

        d = NodeRequest.fromDict(self._bytesToDict(data), request)
        d.stat = stat
        return d

    def updateNodeRequest(self, request):
        '''
        Update the data of a node request object in-place

        :param request: the node request object to update
        '''

        path = self._requestPath(request.id)
        data, stat = self.kazoo_client.get(path)

        if data:
            d = self._bytesToDict(data)
        else:
            d = {}

        request.updateFromDict(d)
        request.stat = stat

    def storeNodeRequest(self, request, priority="100"):
        '''
        Store a new or existing node request.

        :param NodeRequest request: The node request to update.
        :param str priority: Priority of a new request. Ignored on updates.
        '''
        if not request.id:
            if not request.event_id:
                request.event_id = uuid.uuid4().hex
            path = "%s/%s-" % (self.REQUEST_ROOT, priority)
            path = self.kazoo_client.create(
                path,
                value=request.serialize(),
                ephemeral=True,
                sequence=True,
                makepath=True)
            request.id = path.split("/")[-1]

        # Validate it still exists before updating
        else:
            if not self.getNodeRequest(request.id):
                raise Exception(
                    "Attempt to update non-existing request %s" % request)

            path = self._requestPath(request.id)
            self.kazoo_client.set(path, request.serialize())

    def deleteNodeRequest(self, request):
        '''
        Delete a node request.

        :param NodeRequest request: The request to delete.
        '''
        if not request.id:
            return

        path = self._requestPath(request.id)
        try:
            self.kazoo_client.delete(path)
        except kze.NoNodeError:
            pass

    def lockNodeRequest(self, request, blocking=True, timeout=None):
        '''
        Lock a node request.

        This will set the `lock` attribute of the request object when the
        lock is successfully acquired. Also this will update the node request
        with the latest data after acquiring the lock in order to guarantee
        that it has the latest state if locking was successful.

        :param NodeRequest request: The request to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        log = get_annotated_logger(self.log, event_id=request.event_id,
                                   node_request_id=request.id)
        path = self._requestLockPath(request.id)
        thread_timeout = -1 if timeout is None else timeout
        have_thread_lock = request._thread_lock.acquire(
            blocking=blocking,
            timeout=thread_timeout)
        if not have_thread_lock:
            if blocking:
                raise npe.TimeoutException(
                    "Timeout trying to acquire thread lock for %s" % path)
            raise npe.ZKLockException("Did not get thread lock on %s" % path)
        try:
            try:
                lock = Lock(self.kazoo_client, path)
                have_lock = lock.acquire(blocking, timeout)
            except kze.LockTimeout:
                raise npe.TimeoutException(
                    "Timeout trying to acquire lock %s" % path)
            except kze.NoNodeError:
                have_lock = False
                log.error("Request not found for locking: %s", request)

            # If we aren't blocking, it's possible we didn't get the lock
            # because someone else has it.
            if not have_lock:
                raise npe.ZKLockException("Did not get lock on %s" % path)

            request.lock = lock
        except Exception:
            try:
                request._thread_lock.release()
            except Exception:
                log.exception("Unable to release thread lock for: %s", request)
            raise

        # Do an in-place update of the node request so we have the latest data
        self.updateNodeRequest(request)

    def unlockNodeRequest(self, request):
        '''
        Unlock a node request.

        The request must already have been locked.

        :param NodeRequest request: The request to unlock.

        :raises: ZKLockException if the request is not currently locked.
        '''
        if request.lock is None:
            raise npe.ZKLockException(
                "Request %s does not hold a lock" % request)
        request.lock.release()
        request.lock = None
        request._thread_lock.release()

    def lockNode(self, node, blocking=True, timeout=None,
                 ephemeral=True, identifier=None):
        '''
        Lock a node.

        This will set the `lock` attribute of the Node object when the
        lock is successfully acquired. Also this will update the node with the
        latest data after acquiring the lock in order to guarantee that it has
        the latest state if locking was successful.

        :param Node node: The node to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.
        :param bool ephemeral: Whether to use an ephemeral lock.  Unless
            you have a really good reason, use the default of True.
        :param bool identifier: Identifies the lock holder.  The default
            of None is usually fine.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''
        path = self._nodeLockPath(node.id)
        thread_timeout = -1 if timeout is None else timeout
        have_thread_lock = node._thread_lock.acquire(
            blocking=blocking,
            timeout=thread_timeout)
        if not have_thread_lock:
            if blocking:
                raise npe.TimeoutException(
                    "Timeout trying to acquire thread lock for %s" % path)
            raise npe.ZKLockException("Did not get thread lock on %s" % path)
        try:
            try:
                lock = Lock(self.kazoo_client, path, identifier)
                have_lock = lock.acquire(blocking, timeout, ephemeral)
            except kze.LockTimeout:
                raise npe.TimeoutException(
                    "Timeout trying to acquire lock %s" % path)
            except kze.NoNodeError:
                have_lock = False
                self.log.error("Node not found for locking: %s", node)

            # If we aren't blocking, it's possible we didn't get the lock
            # because someone else has it.
            if not have_lock:
                raise npe.ZKLockException("Did not get lock on %s" % path)

            node.lock = lock

            if not ephemeral:
                try:
                    node._thread_lock.release()
                except Exception:
                    self.log.exception("Unable to release non-emphemeral "
                                       "thread lock for: %s", node)
        except Exception:
            try:
                node._thread_lock.release()
            except Exception:
                self.log.exception("Unable to release thread lock for: %s",
                                   node)
            raise

        # Do an in-place update of the node so we have the latest data.
        self.updateNode(node)

    def unlockNode(self, node):
        '''
        Unlock a node.

        The node must already have been locked.

        :param Node node: The node to unlock.

        :raises: ZKLockException if the node is not currently locked.
        '''
        if node.lock is None:
            raise npe.ZKLockException("Node %s does not hold a lock" % node)
        node.lock.release()
        node.lock = None
        node._thread_lock.release()

    def forceUnlockNode(self, node):
        '''
        Forcibly unlock a node.

        :param Node node: The node to unlock.
        '''
        path = self._nodeLockPath(node.id)
        try:
            self.kazoo_client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def getNodeLockContenders(self, node):
        '''
        Return the contenders for a node lock.
        '''
        path = self._nodeLockPath(node.id)
        lock = Lock(self.kazoo_client, path)
        return lock.contenders()

    def getNodes(self):
        '''
        Get the current list of all nodes.

        :returns: A list of nodes.
        '''
        try:
            return self.kazoo_client.get_children(self.NODE_ROOT)
        except kze.NoNodeError:
            return []

    def getNode(self, node, cached=False, only_cached=False):
        '''
        Get the data for a specific node.

        :param str node: The node ID.
        :param bool cached: True if the data should be taken from the cache.
        :param bool only_cached: True if we should ignore nodes not
                                 fully in the cache.
        :returns: The node data, or None if the node was not found.
        '''
        if cached and self._node_cache:
            d = self._node_cache.getNode(node)
            if d:
                return d
            if only_cached:
                self.event_log.debug("Cache miss for node %s", node)
                return None

        # We got here we either didn't use the cache or the cache didn't
        # have the node (yet). Note that even if we use caching we need to
        # do a real query if the cached data is empty because the node data
        # might not be in the cache yet when it's listed by the get_children
        # call.
        try:
            path = self._nodePath(node)
            data, stat = self.kazoo_client.get(path)
        except kze.NoNodeError:
            return None

        if not data:
            return None

        d = Node.fromDict(self._bytesToDict(data), node)
        d.id = node
        d.stat = stat
        return d

    def updateNode(self, node):
        '''
        Update the data of a node object in-place

        :param node: The node object
        '''

        path = self._nodePath(node.id)
        data, stat = self.kazoo_client.get(path)

        if data:
            d = self._bytesToDict(data)
        else:
            # The node exists but has no data so use empty dict.
            d = {}

        node.updateFromDict(d)
        node.stat = stat

    def storeNode(self, node):
        '''
        Store an new or existing node.

        If this is a new node, then node.id will be set with the newly created
        node identifier. Otherwise, node.id is used to identify the node to
        update.

        :param Node node: The Node object to store.
        '''
        if not node.id:
            node_path = "%s/" % self.NODE_ROOT

            # We expect a new node to always have a state already set, so
            # use that state_time for created_time for consistency. But have
            # this check, just in case.
            if node.state_time:
                node.created_time = node.state_time
            else:
                node.created_time = time.time()

            path = self.kazoo_client.create(
                node_path,
                value=node.serialize(),
                sequence=True,
                makepath=True)
            node.id = path.split("/")[-1]
        else:
            path = self._nodePath(node.id)
            self.kazoo_client.set(path, node.serialize())

    def watchNode(self, node, callback):
        '''Watch an existing node for changes.

        :param Node node: The node object to watch.
        :param callable callback: A callable object that will be invoked each
            time the node is updated. It is called with two arguments (node,
            deleted) where 'node' is the same argument passed to this method,
            and 'deleted' is a boolean which is True if the node no longer
            exists. The callback should return False when further updates are
            no longer necessary.
        '''
        def _callback_wrapper(data, stat):
            if data is not None:
                node.updateFromDict(self._bytesToDict(data))

            deleted = data is None
            return callback(node, deleted)

        path = self._nodePath(node.id)
        self.kazoo_client.DataWatch(path, _callback_wrapper)

    def deleteRawNode(self, node_id):
        '''
        Delete a znode for a Node.

        This is used to forcefully delete a Node znode that has somehow
        ended up without any actual data. In most cases, you should be using
        deleteNode() instead.
        '''
        path = self._nodePath(node_id)
        try:
            self.kazoo_client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def deleteNode(self, node):
        '''
        Delete a node.

        :param Node node: The Node object representing the ZK node to delete.
        '''
        if not node.id:
            return

        path = self._nodePath(node.id)

        # Set the node state to deleted before we start deleting
        # anything so that we can detect a race condition where the
        # lock is removed before the node deletion occurs.
        node.state = DELETED
        self.kazoo_client.set(path, node.serialize())
        self.deleteRawNode(node.id)
        if node._thread_lock.locked():
            node._thread_lock.release()

    def getReadyNodesOfTypes(self, labels, cached=True):
        '''
        Query ZooKeeper for unused/ready nodes.

        :param list labels: The node types we want.

        :returns: A dictionary, keyed by node type, with lists of Node objects
            that are ready, or an empty dict if none are found. A node may
            appear more than once under different labels if it is tagged with
            those labels.
        '''
        ret = {}
        for node in self.nodeIterator(cached=cached, cached_ids=cached):
            if node.state != READY or node.allocated_to:
                continue
            for label in labels:
                if label in node.type:
                    if label not in ret:
                        ret[label] = []
                    ret[label].append(node)
        return ret

    def deleteOldestUnusedNode(self, provider_name, pool_name):
        '''
        Deletes the oldest unused (READY+unlocked) node for a provider's pool.

        If we discover that this provider pool already has a node that is
        being deleted, and whose state is less than 5 minutes old, do nothing.

        :param str provider_name: Name of the provider.
        :param str pool_name: Pool name for the given provider.

        :returns: True if a delete was requested, False otherwise.
        '''
        def age(timestamp):
            now = time.time()
            dt = now - timestamp
            m, s = divmod(dt, 60)
            h, m = divmod(m, 60)
            d, h = divmod(h, 24)
            return '%02d:%02d:%02d:%02d' % (d, h, m, s)

        MAX_DELETE_AGE = 5 * 60

        candidates = []
        for node in self.nodeIterator():
            if node.provider == provider_name and node.pool == pool_name:
                # A READY node that has been allocated will not be considered
                # a candidate at this point. If allocated_to gets reset during
                # the cleanup phase b/c the request disappears, then it can
                # become a candidate.
                if node.state == READY and not node.allocated_to:
                    candidates.append(node)
                elif (node.state == DELETING and
                      (time.time() - node.state_time / 1000) < MAX_DELETE_AGE
                ):
                    return False

        candidates.sort(key=lambda n: n.state_time)
        for node in candidates:
            try:
                self.lockNode(node, blocking=False)
            except npe.ZKLockException:
                continue

            # Make sure the state didn't change on us
            if node.state != READY or node.allocated_to:
                self.unlockNode(node)
                continue

            try:
                self.log.debug("Deleting unused node %s (age: %s)",
                               node.id, age(node.state_time))
                node.state = DELETING
                self.storeNode(node)
            except Exception:
                self.log.exception(
                    "Error attempting to update unused node %s:", node.id)
                continue
            finally:
                self.unlockNode(node)

            # If we got here, we found and requested a delete for the
            # oldest unused node.
            return True

        return False

    def nodeIterator(self, cached=True, cached_ids=False):
        '''Utility generator method for iterating through all nodes.

        :param bool cached: True if the data should be taken from the cache.
        :param bool cached_ids: True if the node IDs should be taken from the
                                cache; this will also avoid hitting ZK
                                if the node data is missing from the cache.
        '''

        if cached_ids and self._node_cache:
            node_ids = self._node_cache.getNodeIds()
        else:
            node_ids = self.getNodes()

        for node_id in node_ids:
            node = self.getNode(node_id, cached=cached, only_cached=cached_ids)
            if node:
                yield node

    def nodeRequestLockStatsIterator(self):
        '''
        Utility generator method for iterating through all nodes request locks.
        '''
        for lock_id in self.getNodeRequestLockIDs():
            lock_stats = self.getNodeRequestLockStats(lock_id)
            if lock_stats:
                yield lock_stats

    def nodeRequestIterator(self, cached=True, cached_ids=False):
        '''
        Utility generator method for iterating through all nodes requests.

        :param bool cached: True if the data should be taken from the cache.
        :param bool cached_ids: True if the request IDs should be taken from
                                the cache.
        '''
        if cached_ids:
            req_ids = self._request_cache.getNodeRequestIds()
        else:
            req_ids = self.getNodeRequests()

        for req_id in req_ids:
            req = self.getNodeRequest(req_id, cached=cached)
            if req:
                yield req

    def countPoolNodes(self, provider_name, pool_name):
        '''
        Count the number of nodes that exist for the given provider pool.

        :param str provider_name: The provider name.
        :param str pool_name: The pool name.
        '''
        count = 0
        for node in self.nodeIterator():
            if node.provider == provider_name and node.pool == pool_name:
                count = count + 1
        return count

    def getProviderBuilds(self, provider_name):
        '''
        Get all builds for a provider for each image.

        :param str provider_name: The provider name.
        :returns: A dict of lists of build IDs, keyed by image name.
        '''
        provider_builds = {}
        image_names = self.getImageNames()
        for image in image_names:
            build_ids = self.getBuildIds(image)
            for build in build_ids:
                providers = self.getBuildProviders(image, build)
                for p in providers:
                    if p == provider_name:
                        if image not in provider_builds:
                            provider_builds[image] = []
                        provider_builds[image].append(build)
        return provider_builds

    def getProviderUploads(self, provider_name):
        '''
        Get all uploads for a provider for each image.

        :param str provider_name: The provider name.
        :returns: A dict, keyed by image name and build ID, of a list of
            ImageUpload objects.
        '''
        provider_uploads = {}
        image_names = self.getImageNames()
        for image in image_names:
            build_ids = self.getBuildIds(image)
            for build in build_ids:
                # If this build is not valid for this provider, move along.
                if provider_name not in self.getBuildProviders(image, build):
                    continue

                # We've determined that we at least have a build for this
                # provider so init with an empty upload list.
                if image not in provider_uploads:
                    provider_uploads[image] = {}
                provider_uploads[image][build] = []

                # Add any uploads we might have for this provider.
                uploads = self.getUploads(image, build, provider_name)
                for upload in uploads:
                    provider_uploads[image][build].append(upload)
        return provider_uploads

    def getProviderNodes(self, provider_name):
        '''
        Get all nodes for a provider.

        :param str provider_name: The provider name.
        :returns: A list of Node objects.
        '''
        provider_nodes = []
        for node in self.nodeIterator():
            if node.provider == provider_name:
                provider_nodes.append(node)
        return provider_nodes

    def removeProviderBuilds(self, provider_name, provider_builds):
        '''
        Remove ZooKeeper build data for a provider.

        :param str provider_name: The provider name.
        :param dict provider_builds: Data as returned by getProviderBuilds().
        '''
        for image, builds in provider_builds.items():
            for build in builds:
                path = self._imageProviderPath(image, build)
                path = "%s/%s" % (path, provider_name)
                try:
                    self.kazoo_client.delete(path, recursive=True)
                except kze.NoNodeError:
                    pass

    def removeProviderNodes(self, provider_name, provider_nodes):
        '''
        Remove ZooKeeper node data for a provider.
        :param str provider_name: The provider name.
        :param dict provider_nodes: Data as returned by getProviderNodes().
        '''
        for node in provider_nodes:
            self.deleteNode(node)

    def setNodeStatsEvent(self, event):
        self.node_stats_event = event

    def getStatsElection(self, identifier):
        path = self._electionPath('stats')
        return Election(self.kazoo_client, path, identifier)

    def exportImageData(self):
        '''
        Export the DIB image and upload data from ZK for backup purposes.
        '''
        ret = {}
        for image_name in self.getImageNames():
            paused = self.getImagePaused(image_name)
            if paused:
                paused_path = self._imagePausePath(image_name)
                ret[paused_path] = ''
            for build_no in self.getBuildIds(image_name):
                build_path = self._imageBuildsPath(image_name) + "/" + build_no
                try:
                    build_data, stat = self.kazoo_client.get(build_path)
                except kze.NoNodeError:
                    continue
                ret[build_path] = build_data.decode('utf8')
                for provider_name in self.getBuildProviders(image_name,
                                                            build_no):
                    for upload_no in self.getImageUploadNumbers(
                            image_name, build_no, provider_name):
                        upload_path = self._imageUploadPath(
                            image_name, build_no, provider_name) + "/"
                        upload_path += upload_no
                        try:
                            upload_data, stat = self.kazoo_client.get(
                                upload_path)
                        except kze.NoNodeError:
                            continue
                        ret[upload_path] = upload_data.decode('utf8')
        return ret

    def importImageData(self, import_data):
        '''Import the DIB image and upload data to ZK.

        This makes no guarantees about locking; it is expected to be
        run on a quiescent system with no daemons running.

        '''
        # We do some extra work to ensure that the sequence numbers
        # don't collide.  ZK sequence numbers are stored in the parent
        # node and ZK isn't smart enough to avoid collisions if there
        # are missing entries.  So if we restore upload 1, and then the
        # builder later wants to create a new upload, it will attempt
        # to create upload 1, and fail since the node already exists.
        #
        # NB: The behavior is slightly different for sequence number 1
        # vs others; if 2 is the lowest number, then ZK will create
        # node 0 and 1 before colliding with 2.  This is further
        # complicated in the nodepool context since we create lock
        # entries under the upload znodes which also seem to
        # have an effect on the counter.
        #
        # Regardless, if we pre-create sequence nodes up to our
        # highest node numbers for uploads, we are guaranteed that the
        # next sequence node created will be greater.  So we look at
        # all the sequence nodes in our import data set and pre-create
        # sequence nodes up to that number.
        #
        # Build ids are not affected since they are not sequence nodes
        # (though they used to be).

        highest_num = {}
        # 0     1      2        3         4     5
        #  /nodepool/images/fake-image/builds/UUID/
        #      6         7            8      9
        # providers/fake-provider/images/0000000001
        for path, data in import_data.items():
            parts = path.split('/')
            if len(parts) == 10:
                key = '/'.join(parts[:9])
                num = int(parts[9])
                highest_num[key] = max(highest_num.get(key, num), num)
        for path, num in highest_num.items():
            for x in range(num):
                node = self.kazoo_client.create(
                    path + '/', makepath=True, sequence=True)
                # If this node isn't in our import data, go ahead and
                # delete it.
                if node not in import_data:
                    self.kazoo_client.delete(node)
        for path, data in import_data.items():
            # We may have already created a node above; in that
            # case, just set the data on it.
            if self.kazoo_client.exists(path):
                self.kazoo_client.set(path,
                                      value=data.encode('utf8'))
            else:
                self.kazoo_client.create(path,
                                         value=data.encode('utf8'),
                                         makepath=True)
