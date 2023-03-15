# Copyright 2022 Acme Gating, LLC
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

from concurrent.futures import ThreadPoolExecutor
import copy
import math
import time

from nodepool import tests
from nodepool.driver.utils import QuotaInformation, LazyExecutorTTLCache
from nodepool.nodeutils import iterate_timeout


class TestQutoInformation(tests.BaseTestCase):
    def test_subtract(self):
        provider = QuotaInformation(cores=8, ram=8192, default=math.inf)
        needed = QuotaInformation(cores=2, instances=1)
        expected = QuotaInformation(cores=6, instances=math.inf, ram=8192)

        remain = copy.deepcopy(provider)
        remain.subtract(needed)

        self.assertEqual(expected.quota, remain.quota)

    def test_add(self):
        label1 = QuotaInformation(cores=8, ram=8192)
        label2 = QuotaInformation(cores=2, instances=1)

        needed = copy.deepcopy(label1)
        needed.add(label2)
        expected = QuotaInformation(cores=10, instances=1, ram=8192)
        self.assertEqual(expected.quota, needed.quota)

    def test_extra(self):
        # Test extra quota fields

        # We call them red_, blue_, green_
        # cores here.  They are arbitrary names other than the
        # standard cores, ram, instances.
        label1 = QuotaInformation(cores=8, ram=8192,
                                  red_cores=8, green_cores=8)
        label2 = QuotaInformation(cores=2, instances=1, blue_cores=2)

        needed = copy.deepcopy(label1)
        needed.add(label2)
        expected = QuotaInformation(cores=10, instances=1, ram=8192,
                                    red_cores=8, blue_cores=2,
                                    green_cores=8)
        self.assertEqual(expected.quota, needed.quota)

        provider = QuotaInformation(cores=8, ram=8192, default=math.inf,
                                    green_cores=16)
        expected = QuotaInformation(cores=-2, instances=math.inf, ram=0,
                                    red_cores=math.inf, blue_cores=math.inf,
                                    green_cores=8)

        remain = copy.deepcopy(provider)
        remain.subtract(needed)

        self.assertEqual(expected.quota, remain.quota)


class FakeAdapter:
    CACHE_TTL = 0.5

    def __init__(self):
        self.api_executor = ThreadPoolExecutor(max_workers=4)
        self.get_time = LazyExecutorTTLCache(
            self.CACHE_TTL, self.api_executor)(
                self.get_time)

    def get_time(self):
        return time.monotonic()


class TestLazyExecutorTTLCache(tests.BaseTestCase):
    def test_lazy_cache(self):
        adapter = FakeAdapter()
        t0 = time.monotonic()
        ret1 = adapter.get_time()
        t1 = time.monotonic()
        self.assertTrue(t0 < ret1 < t1)
        # Assuming the computer isn't completely overloaded, this
        # should happen instantly and be a cache hit.
        ret2 = adapter.get_time()
        self.assertEqual(ret1, ret2)
        # Sleep longer than the ttl
        time.sleep(adapter.CACHE_TTL + 0.1)
        # This should be a cache miss that triggers an update and
        # returns the old value.
        ret3 = adapter.get_time()
        self.assertEqual(ret1, ret3)
        # Eventually the async update should return and we should get
        # a newer value.
        for _ in iterate_timeout(30, Exception, 'cache update'):
            ret4 = adapter.get_time()
            if ret4 > ret3:
                break
