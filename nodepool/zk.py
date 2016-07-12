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


from kazoo.client import KazooClient


class ZooKeeper(object):
    '''
    Class implementing the ZooKeeper interface.

    This class uses the facade design pattern to keep common interaction
    with the ZooKeeper API simple and consistent for the caller, and
    limits coupling between objects. It allows for more complex interactions
    by providing direct access to the client connection when needed (though
    that is discouraged). It also provides for a convenient entry point for
    testing only ZooKeeper interactions.
    '''

    def __init__(self):
        self._zkclient = None
        self.connected = False


    @property
    def client(self):
        return self._zkclient


    def _build_hosts(self, host_list):
        if not isinstance(host_list, list):
            raise Exception("'host_list' must be a list")
        hosts = []
        for host_def in host_list:
            host = host_def['host']
            port = host_def['port']
            chroot = host_def['chroot']
            hosts.append("%s:%s%s" % (host, port, chroot))
        return ",".join(hosts)


    def connect(self, host_list, readonly=False):
        '''
        Establish a connection with ZooKeeper cluster.

        :param list host_list: A list of dicts (one per server) defining
            the ZooKeeper cluster servers. Keys for 'host', 'port',
            and 'chroot' are expected.

        :param bool readonly: If True, establishes a read-only connection.
        '''
        if not self.connected:
            hosts = self._build_hosts(host_list)
            self._zkclient = KazooClient(hosts=hosts, readonly=readonly)
            self._zkclient.start()
            self.connected = True


    def disconnect(self):
        '''
        Closes an existing connection to ZooKeeper cluster.
        '''
        if self.connected:
            self._zkclient.stop()


    def build_image_root(self, image):
        '''
        Get the root znode for an image.

        :param str image: Name of the image

        :returns: The full path to the root image znode.
        '''
        return "/nodepool/image/%s" % image


    def get_max_build_id(self, image_root):
        '''
        Find the highest build number for a given image.

        :param str image_root: Root path for the image, as returned from
            the build_image_root() API call.

        :returns: The max existing image build number, or zero if none exist.
        '''
        pass
