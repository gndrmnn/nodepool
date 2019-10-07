# Copyright (C) 2018 Red Hat
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
from nodepool.driver.fake import db


class TestFakeDatabase(tests.DBTestCase):

    def setUp(self):
        super().setUp()
        self.db = db.Database()
        self.db.setZK(self.zk.client)

    def test_flavor(self):
        flavor1 = dict(id='f1', ram=8192, name='Fake Flavor 1', vcpus=2)
        flavor2 = dict(id='f2', ram=1024, name='Fake Flavor 2', vcpus=4)
        self.db.createFlavor(flavor1)
        self.db.createFlavor(flavor2)

        all_flavors = self.db.listFlavors()
        self.assertEqual(2, len(all_flavors))
        self.assertIn(flavor1, all_flavors)
        self.assertIn(flavor2, all_flavors)
