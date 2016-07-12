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

    def test_get_max_build_id_not_found(self):
        with testtools.ExpectedException(
            npe.ZKException, "Image build path not found for .*"
        ):
            self.zk.get_max_build_id("aaa")

    def test_get_max_image_upload_id(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"

        test_root = self.zk._image_upload_path(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")
        self.zk.client.create(test_root + "/10")
        self.zk.client.create(test_root + "/3")
        self.zk.client.create(test_root + "/22")

        self.assertEqual(22, self.zk.get_max_image_upload_id(image,
                                                             build_number,
                                                             provider))

    def test_get_max_image_upload_id_not_found(self):
        with testtools.ExpectedException(
            npe.ZKException, "Image upload path not found for .*"
        ):
            self.zk.get_max_image_upload_id("aaa", 1, "xyz")

    def test_image_build_lock(self):
        test_root = self.zk._image_builds_path("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")

        with self.zk.image_build_lock("ubuntu-trusty", blocking=False) as e:
            # Make sure the volume goes to 11
            self.assertEqual(11, e)

    def test_image_build_lock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([{'host': self.zookeeper_host,
                      'port': self.zookeeper_port,
                      'chroot': self.chroot_path}])
        with zk2.image_build_lock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.image_build_lock("ubuntu-trusty", blocking=False):
                    pass
        zk2.disconnect()

    def test_image_build_lock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([{'host': self.zookeeper_host,
                      'port': self.zookeeper_port,
                      'chroot': self.chroot_path}])
        with zk2.image_build_lock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.image_build_lock("ubuntu-trusty",
                                              blocking=True,
                                              timeout=1):
                    pass
        zk2.disconnect()

    def test_store_build_not_locked(self):
        with testtools.ExpectedException(npe.ZKException):
            self.zk.store_build("ubuntu-trusty", 123, "")

    def test_store_and_get_build(self):
        orig_data = dict(builder="host", filename="file", state="state")
        with self.zk.image_build_lock("ubuntu-trusty",
                                      blocking=True,
                                      timeout=1) as build_num:
            self.zk.store_build("ubuntu-trusty", build_num, orig_data)

        data = self.zk.get_build("ubuntu-trusty", build_num)
        self.assertEqual(orig_data, data)

    def test_get_build_not_found(self):
        with testtools.ExpectedException(
            npe.ZKException, "Cannot find build data .*"
        ):
            self.zk.get_build("ubuntu-trusty", 0)

    def test_get_image_upload_not_found(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        test_root = self.zk._image_upload_path(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find upload data .*"
        ):
            self.zk.get_image_upload(image, build_number, provider, 2)

    def test_store_image_upload_invalid_build(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find build .*"
        ):
            self.zk.store_image_upload(image, build_number, provider,
                                       orig_data)

    def test_store_and_get_image_upload(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")
        test_root = self.zk._image_upload_path(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)

        upload_id = self.zk.store_image_upload(image, build_number, provider,
                                               orig_data)

        # Should be the first upload
        self.assertEqual(1, upload_id)

        data = self.zk.get_image_upload(image, build_number, provider,
                                        upload_id)

        self.assertEqual(orig_data, data)
