# Copyright (C) 2020 Red Hat
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

import logging
import os
import tempfile
import time
from unittest.mock import patch
from functools import wraps

import yaml

import googleapiclient.discovery
import googleapiclient.errors

from nodepool import tests
from nodepool import zk
from nodepool.nodeutils import iterate_timeout


class GCloudRequest:
    def __init__(self, method, args, kw):
        self.method = method
        self.args = args
        self.kw = kw

    def execute(self):
        return self.method(*self.args, **self.kw)


class GCloudCollection:
    def __init__(self):
        self.items = []

    def list(self, *args, **kw):
        return GCloudRequest(self._list, args, kw)

    def _list(self, *args, **kw):
        return dict(
            items=self.items,
        )

    def insert(self, *args, **kw):
        return GCloudRequest(self._insert, args, kw)

    def delete(self, *args, **kw):
        return GCloudRequest(self._delete, args, kw)


class GCloudInstances(GCloudCollection):
    def _insert(self, *args, **kw):
        item = kw['body'].copy()
        item['status'] = 'RUNNING'
        item['zone'] = ('https://www.googleapis.com/compute/v1/projects/'
                        + kw['project'] + '/' + kw['zone'])
        item['networkInterfaces'][0]['networkIP'] = '10.0.0.1'
        item['networkInterfaces'][0]['accessConfigs'][0]['natIP'] = '8.8.8.8'
        item['selfLink'] = ("https://www.googleapis.com/compute/v1/projects/"
                            + kw['project'] + '/instances/'
                            + kw['body']['name'])
        self.items.append(item)

    def _delete(self, *args, **kw):
        for item in self.items[:]:
            if (kw['zone'] in item['zone'] and
                kw['instance'] == item['name'] and
                kw['project'] in item['selfLink']):
                self.items.remove(item)


class GCloudImages(GCloudCollection):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.items.append({
            "family": "debian-9",
            "selfLink": "https://www.googleapis.com/compute/beta/projects/"
            "debian-cloud/global/images/debian-9-stretch-v20200309",
        })

    def getFromFamily(self, *args, **kw):
        return GCloudRequest(self._getFromFamily, args, kw)

    def _getFromFamily(self, *args, **kw):
        for item in self.items:
            if (kw['family'] == item['family'] and
                kw['project'] in item['selfLink']):
                return item
        # Note this isn't quite right, but at least it's the correct class
        raise googleapiclient.errors.HttpError(404, b'')


class GCloudMachineTypes(GCloudCollection):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.items.append({
            "id": "3002",
            "creationTimestamp": "1969-12-31T16:00:00.000-08:00",
            "name": "n1-standard-2",
            "description": "2 vCPUs, 7.5 GB RAM",
            "guestCpus": 2,
            "memoryMb": 7680,
            "imageSpaceGb": 10,
            "maximumPersistentDisks": 128,
            "maximumPersistentDisksSizeGb": "263168",
            "zone": "us-central1-a",
            "selfLink": "https://www.googleapis.com/compute/v1/projects/"
            "gcloud-project/zones/us-central1-a/machineTypes/n1-standard-2",
            "isSharedCpu": False,
            "kind": "compute#machineType"
        })
        self.items.append({
            "id": "1000",
            "creationTimestamp": "1969-12-31T16:00:00.000-08:00",
            "name": "f1-micro",
            "description": "1 vCPU (shared physical core) and 0.6 GB RAM",
            "guestCpus": 1,
            "memoryMb": 614,
            "imageSpaceGb": 0,
            "maximumPersistentDisks": 16,
            "maximumPersistentDisksSizeGb": "3072",
            "zone": "us-central1-a",
            "selfLink": "https://www.googleapis.com/compute/v1/projects/"
            "gcloud-project/zones/us-central1-a/machineTypes/f1-micro",
            "isSharedCpu": True,
            "kind": "compute#machineType"
        })

    def get(self, *args, **kw):
        return GCloudRequest(self._get, args, kw)

    def _get(self, *args, **kw):
        for item in self.items:
            if (kw['machineType'] == item['name']):
                return item
        # Note this isn't quite right, but at least it's the correct class
        raise googleapiclient.errors.HttpError(404, b'')


class GCloudRegions(GCloudCollection):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.items.append({
            "id": "1000",
            "creationTimestamp": "1969-12-31T16:00:00.000-08:00",
            "name": "us-central1",
            "description": "us-central1",
            "status": "UP",
            "zones": [
                "https://www.googleapis.com/compute/v1/projects/gcloud-project"
                "/zones/us-central1-a",
                "https://www.googleapis.com/compute/v1/projects/gcloud-project"
                "/zones/us-central1-b",
                "https://www.googleapis.com/compute/v1/projects/gcloud-project"
                "/zones/us-central1-c",
                "https://www.googleapis.com/compute/v1/projects/gcloud-project"
                "/zones/us-central1-f"
            ],
            "quotas": [
                {"metric": "CPUS",               "limit": 24,   "usage": 0}, # noqa
                {"metric": "DISKS_TOTAL_GB",     "limit": 4096, "usage": 0}, # noqa
                {"metric": "STATIC_ADDRESSES",   "limit": 8,    "usage": 0}, # noqa
                {"metric": "IN_USE_ADDRESSES",   "limit": 8,    "usage": 0}, # noqa
                {"metric": "SSD_TOTAL_GB",       "limit": 500,  "usage": 0}, # noqa
                {"metric": "LOCAL_SSD_TOTAL_GB", "limit": 6000, "usage": 0}, # noqa
                {"metric": "INSTANCES",          "limit": 24,   "usage": 0}, # noqa
                {"metric": "PREEMPTIBLE_CPUS",   "limit": 0,    "usage": 0}, # noqa
                {"metric": "COMMITTED_CPUS",     "limit": 0,    "usage": 0}, # noqa
                {"metric": "INTERNAL_ADDRESSES", "limit": 200,  "usage": 0}, # noqa
                # A bunch of other quotas elided for space
            ],
            "selfLink": "https://www.googleapis.com/compute/v1/projects/"
            "gcloud-project/regions/us-central1",
            "kind": "compute#region"
        })

    def get(self, *args, **kw):
        return GCloudRequest(self._get, args, kw)

    def _get(self, *args, **kw):
        for item in self.items:
            if (kw['region'] == item['name']):
                return item
        # Note this isn't quite right, but at least it's the correct class
        raise googleapiclient.errors.HttpError(404, b'')


class GCloudComputeEmulator:
    def __init__(self):
        self._instances = GCloudInstances()
        self._images = GCloudImages()
        self._machine_types = GCloudMachineTypes()
        self._regions = GCloudRegions()

    def instances(self):
        return self._instances

    def images(self):
        return self._images

    def machineTypes(self):
        return self._machine_types

    def regions(self):
        return self._regions


class GCloudEmulator:
    def __init__(self):
        self.compute = GCloudComputeEmulator()

    def build(self, *args, **kw):
        return self.compute


class TestDriverGce(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestDriverGce")

    def _wait_for_provider(self, nodepool, provider):
        for _ in iterate_timeout(30, Exception, 'wait for provider'):
            try:
                nodepool.getProviderManager(provider)
                break
            except Exception:
                pass

    def _test_with_pool(the_test):
        @wraps(the_test)
        def wrapper(self, *args, **kwargs):
            self.patch(googleapiclient, 'discovery', GCloudEmulator())

            conf_template = os.path.join(
                os.path.dirname(__file__), '..', 'fixtures', 'gce.yaml')
            with open(conf_template) as f:
                raw_config = yaml.safe_load(f)
            raw_config['zookeeper-servers'][0] = {
                'host': self.zookeeper_host,
                'port': self.zookeeper_port,
                'chroot': self.zookeeper_chroot,
            }
            raw_config['zookeeper-tls'] = {
                'ca': self.zookeeper_ca,
                'cert': self.zookeeper_cert,
                'key': self.zookeeper_key,
            }
            with tempfile.NamedTemporaryFile() as tf:
                tf.write(yaml.safe_dump(
                    raw_config, default_flow_style=False).encode('utf-8'))
                tf.flush()
                configfile = self.setup_config(tf.name)
                pool = self.useNodepool(configfile, watermark_sleep=1)
                the_test(self, pool, *args, **kwargs)
        return wrapper

    @_test_with_pool
    def test_gce_reconfigure(self, pool):
        pool.updateConfig()
        pool.updateConfig()

    @_test_with_pool
    def _test_gce_machine(self, pool, label,
                          is_valid_config=True,
                          host_key_checking=True):
        pool.start()

        self._wait_for_provider(pool, 'gcloud-provider')

        with patch('nodepool.driver.simple.nodescan') as nodescan:
            nodescan.return_value = 'MOCK KEY'
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.tenant_name = 'tenant-1'
            req.node_types.append(label)
            self.zk.storeNodeRequest(req)

            self.log.debug("Waiting for request %s", req.id)
            req = self.waitForNodeRequest(req)
            self.log.debug("Finished request %s", req.id)

            if is_valid_config is False:
                self.assertEqual(req.state, zk.FAILED)
                self.assertEqual(req.nodes, [])
                return

            self.assertEqual(req.state, zk.FULFILLED)
            self.assertNotEqual(req.nodes, [])

            node = self.zk.getNode(req.nodes[0])
            self.assertEqual(node.allocated_to, req.id)
            self.assertEqual(node.state, zk.READY)
            self.assertIsNotNone(node.launcher)
            self.assertEqual(node.connection_type, 'ssh')
            self.assertEqual(node.attributes,
                             {'key1': 'value1', 'key2': 'value2'})
            if host_key_checking:
                nodescan.assert_called_with(
                    node.interface_ip,
                    port=22,
                    timeout=180,
                    gather_hostkeys=True)

            # A new request will be paused and for lack of quota
            # until this one is deleted
            req2 = zk.NodeRequest()
            req2.state = zk.REQUESTED
            req2.node_types.append(label)
            self.zk.storeNodeRequest(req2)
            req2 = self.waitForNodeRequest(
                req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
            self.assertEqual(req2.state, zk.PENDING)
            # It could flip from PENDING to one of the others,
            # so sleep a bit and be sure
            time.sleep(1)
            req2 = self.waitForNodeRequest(
                req2, (zk.PENDING, zk.FAILED, zk.FULFILLED))
            self.assertEqual(req2.state, zk.PENDING)

            node.state = zk.DELETING
            self.zk.storeNode(node)

            self.waitForNodeDeletion(node)

            req2 = self.waitForNodeRequest(req2,
                                           (zk.FAILED, zk.FULFILLED))
            self.assertEqual(req2.state, zk.FULFILLED)
            node = self.zk.getNode(req2.nodes[0])
            node.state = zk.DELETING
            self.zk.storeNode(node)
            self.waitForNodeDeletion(node)

    def test_gce_machine(self):
        self._test_gce_machine('debian-stretch-f1-micro')
