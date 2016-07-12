# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import kazoo
import mock
import testtools

from nodepool import exceptions as npe
from nodepool import tests
from nodepool import zk


class TestZooKeeper(tests.ZKTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()
        self.zk = zk.ZooKeeper(self.zkclient)

    def test_build_zookeeper_hosts_single(self):
        hosts = [
            dict(host='127.0.0.1', port=2181, chroot='/test1')
        ]
        self.assertEqual('127.0.0.1:2181/test1',
                         zk.build_zookeeper_hosts(hosts))

    def test_build_zookeeper_hosts_multiple(self):
        hosts = [
            dict(host='127.0.0.1', port=2181, chroot='/test1'),
            dict(host='127.0.0.2', port=2182, chroot='/test2')
        ]
        self.assertEqual('127.0.0.1:2181/test1,127.0.0.2:2182/test2',
                         zk.build_zookeeper_hosts(hosts))

    def test_get_max_build_id(self):
        test_root = self.zk._image_builds_path("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")
        self.zk.client.create(test_root + "/10")
        self.zk.client.create(test_root + "/3")
        self.zk.client.create(test_root + "/22")
        self.zk.client.create(test_root + "/lock")

        self.assertEqual(22, self.zk.get_max_build_id("ubuntu-trusty"))

    def test_image_build_lock(self):
        test_root = self.zk._image_builds_path("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")

        with self.zk.image_build_lock("ubuntu-trusty", blocking=False) as e:
            # Make sure the volume goes to 11
            self.assertEqual(11, e)

    @mock.patch.object(kazoo.recipe.lock.Lock, 'acquire')
    def test_image_build_lock_exception_nonblocking(self, mock_acquire):
        mock_acquire.return_value = False
        with testtools.ExpectedException(npe.ZKLockException):
            with self.zk.image_build_lock("ubuntu-trusty", blocking=False):
                pass

    @mock.patch.object(kazoo.recipe.lock.Lock, 'acquire')
    def test_image_build_lock_exception_blocking(self, mock_acquire):
        mock_acquire.side_effect = kazoo.exceptions.LockTimeout()
        with testtools.ExpectedException(npe.TimeoutException):
            with self.zk.image_build_lock("ubuntu-trusty",
                                          blocking=True,
                                          timeout=1):
                pass

    def test_store_build_not_locked(self):
        with testtools.ExpectedException(npe.ZKException):
            self.zk.store_build(123, "ubuntu-trusty", "")

    def test_store_build(self):
        orig_data = dict(builder="host", filename="file", state="state")
        with self.zk.image_build_lock("ubuntu-trusty",
                                      blocking=True,
                                      timeout=1) as build_num:
            self.zk.store_build(build_num, "ubuntu-trusty", orig_data)

        p = "%s/%s" % (self.zk._image_builds_path("ubuntu-trusty"), build_num)
        data, stat = self.zk.client.get(p)
        self.assertEqual(orig_data, self.zk._str_to_dict(data))
