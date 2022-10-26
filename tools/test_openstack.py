# Copyright 2022 Acme Gating, LLC
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import time

from nodepool import config as nodepool_config
from nodepool.driver.openstack import OpenStackDriver


logging.basicConfig(level=logging.INFO)
log = logging.getLogger('nodepool.test')
config = nodepool_config.loadConfig('/etc/nodepool/nodepool.yaml')
POOL_NAME = 'main'
LABEL_NAME = 'ubuntu-jammy'


def test_create(provider_config):
    pool = provider_config.pools[POOL_NAME]
    label = pool.labels[LABEL_NAME]
    image_external_id = None
    metadata = {'nptest': 'test'}
    retries = 0
    request = None
    az = None
    sm = adapter.getCreateStateMachine('nptest', label, image_external_id,
                                       metadata, retries, request, az, log)
    print(f'State machine at {sm.state}')
    while True:
        sm.advance()
        print(f'State machine at {sm.state} id {sm.external_id}')
        time.sleep(1)
        if sm.complete:
            break
    return sm.external_id


def test_delete(external_id):
    sm = adapter.getDeleteStateMachine(external_id, log)
    print(f'State machine at {sm.state}')
    while True:
        sm.advance()
        print(f'State machine at {sm.state} id {sm.external_id}')
        time.sleep(1)
        if sm.complete:
            break


providers = list(config.providers.keys())
for provider in providers:
    print("Provider", provider)
    provider_config = config.providers.get(provider)
    driver = OpenStackDriver()
    adapter = driver.getAdapter(provider_config)

    extid = test_create(provider_config)
    test_delete(extid)
