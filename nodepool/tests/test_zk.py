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


class TestZooKeeper(tests.BaseTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()
        self.zk = zk.ZooKeeper()

    def test__build_hosts_single(self):
        hosts = [
            dict(host='127.0.0.1', port=2181, chroot='/test1')
        ]
        self.assertEqual('127.0.0.1:2181/test1',
                         self.zk._build_hosts(hosts))

    def test__build_hosts_multiple(self):
        hosts = [
            dict(host='127.0.0.1', port=2181, chroot='/test1'),
            dict(host='127.0.0.2', port=2182, chroot='/test2')
        ]
        self.assertEqual('127.0.0.1:2181/test1,127.0.0.2:2182/test2',
                         self.zk._build_hosts(hosts))

    def test_build_image_root(self):
        image = "ubuntu-trusty"
        self.assertEqual(
            "/nodepool/image/%s" % image,
            self.zk.build_image_root(image)
        )
