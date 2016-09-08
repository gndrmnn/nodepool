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

import mock
import testtools
import time

from nodepool import exceptions as npe
from nodepool import tests
from nodepool import zk


class TestZooKeeper(tests.ZKTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()
        self.zk = zk.ZooKeeper(self.zkclient)

    def test_buildZooKeeperHosts_single(self):
        hosts = [
            zk.ZooKeeperConnectionConfig('127.0.0.1', port=2181,
                                         chroot='/test1')
        ]
        self.assertEqual('127.0.0.1:2181/test1',
                         zk.buildZooKeeperHosts(hosts))

    def test_buildZooKeeperHosts_multiple(self):
        hosts = [
            zk.ZooKeeperConnectionConfig('127.0.0.1', port=2181,
                                         chroot='/test1'),
            zk.ZooKeeperConnectionConfig('127.0.0.2', port=2182,
                                         chroot='/test2')
        ]
        self.assertEqual('127.0.0.1:2181/test1,127.0.0.2:2182/test2',
                         zk.buildZooKeeperHosts(hosts))

    def test_getMaxBuildId(self):
        test_root = self.zk._imageBuildsPath("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")
        self.zk.client.create(test_root + "/10")
        self.zk.client.create(test_root + "/3")
        self.zk.client.create(test_root + "/22")
        self.zk.client.create(test_root + "/lock")

        self.assertEqual(22, self.zk.getMaxBuildId("ubuntu-trusty"))

    def test_getMaxBuildId_not_found(self):
        with testtools.ExpectedException(
            npe.ZKException, "Image build path not found for .*"
        ):
            self.zk.getMaxBuildId("aaa")

    def test_getMaxImageUploadId(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"

        test_root = self.zk._imageUploadPath(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")
        self.zk.client.create(test_root + "/10")
        self.zk.client.create(test_root + "/3")
        self.zk.client.create(test_root + "/22")

        self.assertEqual(22, self.zk.getMaxImageUploadId(image,
                                                         build_number,
                                                         provider))

    def test_getMaxImageUploadId_not_found(self):
        with testtools.ExpectedException(
            npe.ZKException, "Image upload path not found for .*"
        ):
            self.zk.getMaxImageUploadId("aaa", 1, "xyz")

    def test_imageBuildLock(self):
        test_root = self.zk._imageBuildsPath("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")

        with self.zk.imageBuildLock("ubuntu-trusty", blocking=False):
            self.assertIsNotNone(self.zk._current_lock)
        self.assertIsNone(self.zk._current_lock)

    def test_imageBuildLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.imageBuildLock("ubuntu-trusty", blocking=False):
                    pass
        zk2.disconnect()

    def test_imageBuildLock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageBuildLock("ubuntu-trusty",
                                            blocking=True,
                                            timeout=1):
                    pass
        zk2.disconnect()

    def test_storeBuild_new(self):
        image = "ubuntu-trusty"
        test_root = self.zk._imageBuildsPath(image)
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")
        self.assertEqual(11, self.zk.storeBuild(image, {}))

    def test_store_and_get_build(self):
        image = "ubuntu-trusty"
        orig_data = dict(builder="host", filename="file", state="state")
        with self.zk.imageBuildLock(image, blocking=True, timeout=1):
            build_num = self.zk.storeBuild(image, orig_data)

        data = self.zk.getBuild(image, build_num)
        self.assertEqual(orig_data, data)

    def test_getBuild_not_found(self):
        self.assertIsNone(self.zk.getBuild("ubuntu-trusty", 0))

    def test_getImageUpload_not_found(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        test_root = self.zk._imageUploadPath(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find upload data .*"
        ):
            self.zk.getImageUpload(image, build_number, provider, 2)

    def test_storeImageUpload_invalid_build(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find build .*"
        ):
            self.zk.storeImageUpload(image, build_number, provider, orig_data)

    def test_store_and_get_image_upload(self):
        image = "ubuntu-trusty"
        build_number = 1
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")
        test_root = self.zk._imageUploadPath(image, build_number, provider)
        self.zk.client.create(test_root, makepath=True)

        upload_id = self.zk.storeImageUpload(image, build_number, provider,
                                             orig_data)

        # Should be the first upload
        self.assertEqual(1, upload_id)

        data = self.zk.getImageUpload(image, build_number, provider, upload_id)
        self.assertEqual(orig_data, data)

    def test_registerBuildRequestWatch(self):
        func = mock.MagicMock()
        image = "ubuntu-trusty"
        watch_path = self.zk._imageBuildRequestPath(image)

        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  self.zookeeper_port,
                                                  self.chroot_path)])

        # First client registers the watch
        self.zk.registerBuildRequestWatch(image, func)
        self.assertIn(watch_path, self.zk._data_watches)

        # Second client triggers the watch. Give ZK time to dispatch the event
        # to the other client.
        zk2.client.create(watch_path, makepath=True)
        time.sleep(1)
        zk2.disconnect()

        # Make sure the registered function was called.
        func.assert_called_once_with(mock.ANY)

        # The watch should be unregistered now.
        self.assertNotIn(watch_path, self.zk._data_watches)

    def test_sendHeartbeat(self):
        self.zk.sendHeartbeat()
        # Not sure what to test here other than it's actually connected
        self.assertTrue(self.zkclient.connected)

    def test_hasBuildRequest(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildRequestPath(image)
        self.zk.client.create(path, makepath=True)
        self.assertTrue(self.zk.hasBuildRequest(image))

    def test_removeBuildRequest(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildRequestPath(image)
        self.zk.client.create(path, makepath=True)
        self.assertTrue(self.zk.hasBuildRequest(image))
        self.zk.removeBuildRequest(image)
        self.assertFalse(self.zk.hasBuildRequest(image))

    def test_getMostRecentBuild(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildsPath(image)
        v1 = {'state': 'ready', 'state_time': int(time.time())}
        v2 = {'state': 'ready', 'state_time': v1['state_time'] + 10}
        v3 = {'state': 'delete', 'state_time': v2['state_time'] + 10}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)

        # v2 should be the most recent 'ready' build
        data = self.zk.getMostRecentBuild(image, 'ready')
        self.assertEqual(data, v2)

    def test_getBuildsWithStates(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildsPath(image)
        v1 = {'state': ''}
        v2 = {'state': 'ready'}
        v3 = {'state': 'unused'}
        v4 = {'state': 'failed'}
        v5 = {'state': 'deleted'}
        v6 = {}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)
        self.zk.client.create(path + "/4", value=self.zk._dictToStr(v4),
                              makepath=True)
        self.zk.client.create(path + "/5", value=self.zk._dictToStr(v5),
                              makepath=True)
        self.zk.client.create(path + "/6", value=self.zk._dictToStr(v6),
                              makepath=True)

        matches = self.zk.getBuildsWithStates(image, ['', 'deleted', 'failed'])

        expected = {'1': v1, '4': v4, '5': v5, '6': v6}
        self.assertEqual(expected, matches)

    def test_deleteBuild(self):
        test_root = self.zk._imageBuildsPath("ubuntu-trusty")
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")

        path = test_root + "/10"
        self.assertTrue(self.zk.client.exists(path))
        self.zk.deleteBuild("ubuntu-trusty", 10)
        self.assertFalse(self.zk.client.exists(path))

    def test_getImages(self):
        root = self.zk.IMAGE_ROOT
        self.zk.client.create(root + "/image1", makepath=True)
        self.zk.client.create(root + "/image2", makepath=True)
        self.assertItemsEqual(["image1", "image2"], self.zk.getImages())
