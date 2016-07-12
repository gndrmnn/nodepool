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

from nodepool import tests
from nodepool import zk


class TestZooKeeper(tests.ZKTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()
        self.zk = zk.ZooKeeper(self.zkclient)

    def _do_cleanup(self):
        if self.zk.client.exists(self.test_root):
            self.zk.client.delete(self.test_root, recursive=True)
        self.zk.disconnect()

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
        image_name = self.getUniqueString()
        test_root = "/nodepool/image/%s/builds" % image_name
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/1")
        self.zk.client.create(test_root + "/10")
        self.zk.client.create(test_root + "/3")
        self.zk.client.create(test_root + "/22")

        self.assertEqual(22, self.zk.get_max_build_id(image_name))

    def test_image_build_lock(self):
        image_name = self.getUniqueString()
        test_root = "/nodepool/image/%s/builds" % image_name
        self.zk.client.create(test_root, makepath=True)
        self.zk.client.create(test_root + "/10")
        with self.zk.image_build_lock(image_name) as e:
            self.assertEqual(11, e)
