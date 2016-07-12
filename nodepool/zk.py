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

from contextlib import contextmanager
import json
from kazoo.client import KazooClient
from kazoo import exceptions as kze
from kazoo.recipe.lock import Lock

from nodepool import exceptions as npe


def build_zookeeper_hosts(host_list):
    '''
    Build the ZK cluster host list for client connections.

    :param list host_list: A list of dicts (one per server) defining
        the ZooKeeper cluster servers. Keys for 'host', 'port', and
        'chroot' are expected. Only 'host' is required.'. E.g.::

            [
              dict(host='192.168.0.2'),
              dict(host='192.168.0.3', port=2181, chroot='/junk')
            ]
    '''
    if not isinstance(host_list, list):
        raise Exception("'host_list' must be a list")
    hosts = []
    for host_def in host_list:
        host = host_def['host']
        if 'port' in host_def:
            host = host + ":%s" % host_def['port']
        else:
            host = host + ":2181"
        if 'chroot' in host_def:
            host = host + host_def['chroot']
        hosts.append(host)
    return ",".join(hosts)


class ZooKeeper(object):
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

    If you will have multiple threads needing this API, each thread should
    instantiate their own ZooKeeper object. It should not be shared.
    '''

    IMAGE_ROOT = "/nodepool/image"

    def __init__(self, client=None):
        '''
        Initialize the ZooKeeper object.

        :param client: A pre-connected client. Optionally, you may choose
            to use the connect() call.
        '''
        self.client = client
        self._current_lock = None

    #========================================================================
    # Private Methods
    #========================================================================

    def _image_path(self, image):
        return "%s/%s" % (self.IMAGE_ROOT, image)

    def _image_builds_path(self, image):
        return "%s/builds" % self._image_path(image)

    def _image_lock_path(self, image):
        return "%s/lock" % self._image_builds_path(image)

    def _dict_to_str(self, data):
        return json.dumps(data)

    def _str_to_dict(self, data):
        return json.loads(data)

    def _get_image_write_lock(self, image, blocking=True, timeout=None):
        # If we don't already have a znode for this image, create it.
        image_lock = self._image_lock_path(image)
        try:
            self.client.ensure_path(self._image_path(image))
            self._current_lock = Lock(self.client, image_lock)
            have_lock = self._current_lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise npe.TimeoutException(
                "Timeout trying to acquire lock %s" % image_lock)
        except kze.KazooException as e:
            raise npe.ZKLockException(
                "Failed locking znode for image %s:" % (image, e))

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise npe.ZKLockException("Did not get lock on %s" % image_lock)

        # Create new znode with new build_number
        build_number = self.get_max_build_id(image) + 1
        self.client.create(
            self._image_builds_path(image) + "/%s" % build_number
        )

        return build_number

    #========================================================================
    # Public Methods
    #========================================================================

    def connect(self, host_list, readonly=False):
        '''
        Establish a connection with ZooKeeper cluster.

        Convenience method if a pre-existing ZooKeeper connection is not
        supplied to the ZooKeeper object at instantiation time.

        :param list host_list: A list of dicts (one per server) defining
            the ZooKeeper cluster servers.

        :param bool readonly: If True, establishes a read-only connection.
        '''
        if not self.client:
            hosts = build_zookeeper_hosts(host_list)
            self.client = KazooClient(hosts=hosts, readonly=readonly)
            self.client.start()

    def get_max_build_id(self, image):
        '''
        Find the highest build number for a given image.

        Image builds are integer znodes, which are children of the 'builds'
        parent znode.

        :param str image: The image name.

        :returns: An int value for the max existing image build number, or
            zero if none exist.
        '''
        max_found = 0
        children = self.client.get_children(self._image_builds_path(image))
        if children:
            for child in children:
                # There can be a lock znode that we should ignore
                if child != 'lock':
                    max_found = max(max_found, int(child))
        return max_found

    @contextmanager
    def image_build_lock(self, image, blocking=True, timeout=None):
        '''
        Context manager to use for locking new image builds.

        Obtains a write lock for the specified image. A thread of control
        using this API may have only one image locked at a time. A new
        znode is created with the next highest build number. This build
        number is returned to the caller.

        :param str image: Name of the image to lock
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :returns: A integer to use for the new build id.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock.
        '''
        try:
            yield self._get_image_write_lock(image)
        finally:
            if self._current_lock:
                self._current_lock.release()
                self._current_lock = None

    def store_build(self, build_number, image, build_data):
        '''
        Store the image build data.

        :param int build_number: The image build number.
        :param str image: The image name for which we have data.
        :param build_data: The build data.

        :raises: ZKException if the build znode does not exist (it is created
            with the image_build_lock() context manager), or there was an
            error with setting the build data.
        '''
        build_path = self._image_builds_path(image) + "/%s" % build_number
        if not self.client.exists(build_path):
            raise npe.ZKException(
                "%s does not exist. Did you lock it?" % build_path)
        try:
            self.client.set(build_path, self._dict_to_str(build_data))
        except kze.KazooException as e:
            raise npe.ZKException("Storing build data failed: %s" % e)
