#
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
from nodepool.config import Config
from nodepool.config import DiskImage
from nodepool.config import Label
from nodepool.driver import ConfigPool
from nodepool.driver import Driver
from nodepool.driver.openstack.config import ProviderDiskImage
from nodepool.driver.openstack.config import ProviderCloudImage
from nodepool.driver.openstack.config import ProviderLabel
from nodepool.driver.openstack.config import ProviderPool


class TestConfigComparisons(tests.BaseTestCase):

    def test_ConfigPool(self):
        a = ConfigPool()
        b = ConfigPool()
        self.assertEqual(a, b)
        a.labels.append(1)
        self.assertNotEqual(a, b)

    def test_Driver(self):
        a = Driver()
        b = Driver()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_Config(self):
        a = Config()
        b = Config()
        self.assertEqual(a, b)
        a.imagesdir = "foo"
        self.assertNotEqual(a, b)

    def test_Label(self):
        a = Label()
        b = Label()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_DiskImage(self):
        a = DiskImage()
        b = DiskImage()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_ProviderDiskImage(self):
        a = ProviderDiskImage()
        b = ProviderDiskImage()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_ProviderCloudImage(self):
        a = ProviderCloudImage()
        b = ProviderCloudImage()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_ProviderLabel(self):
        a = ProviderLabel()
        b = ProviderLabel()
        self.assertEqual(a, b)
        a.name = "foo"
        self.assertNotEqual(a, b)

    def test_ProviderPool(self):
        a = ProviderPool()
        b = ProviderPool()
        self.assertEqual(a, b)
        # intentionally change an attribute of the base class
        a.max_servers = 5
        self.assertNotEqual(a, b)

        c = ConfigPool()
        d = ProviderPool()
        self.assertNotEqual(c, d)
