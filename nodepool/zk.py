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


import threading

from contextlib import contextmanager
from kazoo.client import KazooClient


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
    '''

    IMAGE_ROOT = "/nodepool/image"

    def __init__(self, client=None):
        '''
        Initialize the ZooKeeper object.

        :param client: A pre-connected client. Optionally, you may choose
            to use the connect() call.
        '''
        self.client = client
        self._build_locks = []
        self._build_locks_lock = threading.Lock()

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

    def get_max_build_id(self, image_name):
        '''
        Find the highest build number for a given image.

        Image builds are integer znodes rooted at::

            /nodepool/image/{IMAGE_NAME}/builds/{ID}

        :param str image_name: The image name.

        :returns: An int value for the max existing image build number, or
            zero if none exist.
        '''
        max_found = 0
        builds_path = "%s/%s/builds" % (self.IMAGE_ROOT, image_name)
        children = self.client.get_children(builds_path)
        if children:
            children = [int(child) for child in children]
            max_found = max(children)
        return max_found

    @contextmanager
    def image_build_lock(self, image_name):
        '''
        Context manager to use for locking new image builds.

        Separate API methods are available for acquiring and releasing
        build locks, but that's prone to programmer error of not releasing
        the lock properly. Use this instead.

        :param str image_name: Name of the image to lock

        :returns: A integer to use for the new build id.
        '''
        try:
            yield self.get_image_build_lock(image_name)
        finally:
            self.release_image_build_lock(image_name)

    def get_image_build_lock(self, image_name):
        '''
        Gets a ZooKeeper lock for a new image build.

        Acquire a new build image lock. It is suggested you use the
        image_build_lock() context manager instead.

        :param str image_name: Name of the image to lock

        :returns: A integer to use for the new build id.
        '''
        # get zk lock here
        build_number = self.get_max_build_id(image_name) + 1
        return build_number

    def release_image_build_lock(self, image_name):
        '''
        Release an existing ZooKeeper lock for a new image build.

        Releases the build image lock. It is suggested you use the
        image_build_lock() context manager instead.
        '''
        pass

    def store_build(self, build_number, image_name, build_data):
        pass
