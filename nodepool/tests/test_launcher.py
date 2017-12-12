# Copyright (C) 2014 OpenStack Foundation
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
import math
import time
import fixtures
import unittest.mock as mock

from nodepool import tests
from nodepool import zk
import nodepool.launcher


class TestLauncher(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestLauncher")

    def test_node_assignment(self):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request.
        '''
        configfile = self.setup_config('node_no_min_ready.yaml')
        self._useBuilder(configfile)
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(image.username, 'zuul')

        nodepool.launcher.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        self.assertNotEqual(req.nodes, [])
        for node_id in req.nodes:
            node = self.zk.getNode(node_id)
            self.assertEqual(node.allocated_to, req.id)
            self.assertEqual(node.state, zk.READY)
            self.assertIsNotNone(node.launcher)
            self.assertEqual(node.cloud, 'fake')
            self.assertEqual(node.region, 'fake-region')
            self.assertEqual(node.az, "az1")
            self.assertEqual(node.username, "zuul")
            p = "{path}/{id}".format(
                path=self.zk._imageUploadPath(image.image_name,
                                              image.build_id,
                                              image.provider_name),
                id=image.id)
            self.assertEqual(node.image_id, p)
            self.zk.lockNode(node, blocking=False)
            self.zk.unlockNode(node)

        # Verify the cleanup thread removed the lock
        self.assertIsNotNone(
            self.zk.client.exists(self.zk._requestLockPath(req.id))
        )
        self.zk.deleteNodeRequest(req)
        self.waitForNodeRequestLockDeletion(req.id)
        self.assertReportedStat('nodepool.nodes.ready', '1|g')
        self.assertReportedStat('nodepool.nodes.building', '0|g')

    def test_node_assignment_order(self):
        """Test that nodes are assigned in the order requested"""
        configfile = self.setup_config('node_many_labels.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        self.waitForNodes('fake-label1')
        self.waitForNodes('fake-label2')
        self.waitForNodes('fake-label3')
        self.waitForNodes('fake-label4')

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label3')
        req.node_types.append('fake-label1')
        req.node_types.append('fake-label4')
        req.node_types.append('fake-label2')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(4, len(req.nodes))
        nodes = []
        for node_id in req.nodes:
            nodes.append(self.zk.getNode(node_id))
        self.assertEqual(nodes[0].type, 'fake-label3')
        self.assertEqual(nodes[1].type, 'fake-label1')
        self.assertEqual(nodes[2].type, 'fake-label4')
        self.assertEqual(nodes[3].type, 'fake-label2')

    @mock.patch('nodepool.driver.fake.provider.get_fake_quota')
    def _test_node_assignment_at_quota(self, mock_quota,
                                       config='node_quota.yaml',
                                       max_cores=100,
                                       max_instances=20,
                                       max_ram=1000000):
        '''
        Successful node launch should have unlocked nodes in READY state
        and assigned to the request. This should be run with a quota that
        fits for two nodes.
        '''

        # patch the cloud with requested quota
        mock_quota.return_value = (max_cores, max_instances, max_ram)

        configfile = self.setup_config(config)
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        nodepool.launcher.LOCK_CLEANUP = 1
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)

        client = pool.getProviderManager('fake-provider')._getClient()

        # One of the things we want to test is that if we spawn many
        # node launches at once, we do not deadlock while the request
        # handler pauses for quota.  To ensure we test that case,
        # pause server creation until we have accepted all of the node
        # requests we submit.  This will ensure that we hold locks on
        # all of the nodes before pausing so that we can validate they
        # are released.
        client.pause_creates = True

        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        req1.node_types.append('fake-label')
        self.zk.storeNodeRequest(req1)
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)

        req1 = self.waitForNodeRequest(req1, (zk.PENDING,))
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # At this point, we should be about to create or have already
        # created two servers for the first request, and the request
        # handler has accepted the second node request but paused
        # waiting for the server count to go below quota.

        # Wait until both of the servers exist.
        while len(client._server_list) < 2:
            time.sleep(0.1)

        # Wait until there is a paused request handler and check if there
        # are exactly two servers
        pool_worker = pool.getPoolWorkers('fake-provider')
        while not pool_worker[0].paused_handler:
            time.sleep(0.1)
        self.assertEqual(len(client._server_list), 2)

        # Allow the servers to finish being created.
        for server in client._server_list:
            server.event.set()

        self.log.debug("Waiting for 1st request %s", req1.id)
        req1 = self.waitForNodeRequest(req1)
        self.assertEqual(req1.state, zk.FULFILLED)
        self.assertEqual(len(req1.nodes), 2)

        # Mark the first request's nodes as USED, which will get them deleted
        # and allow the second to proceed.
        self.log.debug("Marking first node as used %s", req1.id)
        node = self.zk.getNode(req1.nodes[0])
        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

        # To force the sequential nature of what we're testing, wait for
        # the 2nd request to get a node allocated to it now that we've
        # freed up a node.
        self.log.debug("Waiting for node allocation for 2nd request")
        done = False
        while not done:
            for n in self.zk.nodeIterator():
                if n.allocated_to == req2.id:
                    done = True
                    break

        self.log.debug("Marking second node as used %s", req1.id)
        node = self.zk.getNode(req1.nodes[1])
        node.state = zk.USED
        self.zk.storeNode(node)
        self.waitForNodeDeletion(node)

        self.log.debug("Deleting 1st request %s", req1.id)
        self.zk.deleteNodeRequest(req1)
        self.waitForNodeRequestLockDeletion(req1.id)

        # Wait until both of the servers exist.
        while len(client._server_list) < 2:
            time.sleep(0.1)

        # Allow the servers to finish being created.
        for server in client._server_list:
            server.event.set()

        req2 = self.waitForNodeRequest(req2)
        self.assertEqual(req2.state, zk.FULFILLED)
        self.assertEqual(len(req2.nodes), 2)

    def test_node_assignment_at_pool_quota_instances(self):
        self._test_node_assignment_at_quota(
            config='node_quota_pool_instances.yaml')


    def test_node_assignment_at_cloud_cores_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=8,
                                            max_instances=math.inf,
                                            max_ram=math.inf)

    def test_node_assignment_at_cloud_instances_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=math.inf,
                                            max_instances=2,
                                            max_ram=math.inf)

    def test_node_assignment_at_cloud_ram_quota(self):
        self._test_node_assignment_at_quota(config='node_quota_cloud.yaml',
                                            max_cores=math.inf,
                                            max_instances=math.inf,
                                            max_ram=2*8192)

    def test_fail_request_on_launch_failure(self):
        '''
        Test that provider launch error fails the request.
        '''
        configfile = self.setup_config('node_launch_retry.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager.createServer_fails = 2

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(0, manager.createServer_fails)
        self.assertEqual(req.state, zk.FAILED)
        self.assertNotEqual(req.declined_by, [])

    def test_fail_minready_request_at_capacity(self):
        '''
        A min-ready request to a provider that is already at capacity should
        be declined.
        '''
        configfile = self.setup_config('node_min_ready_capacity.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Get an initial node ready
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append("fake-label")
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)

        # Now simulate a min-ready request
        min_ready_req = zk.NodeRequest()
        min_ready_req.state = zk.REQUESTED
        min_ready_req.node_types.append("fake-label")
        min_ready_req.requestor = "NodePool:min-ready"
        self.zk.storeNodeRequest(min_ready_req)
        min_ready_req = self.waitForNodeRequest(min_ready_req)
        self.assertEqual(min_ready_req.state, zk.FAILED)
        self.assertNotEqual(min_ready_req.declined_by, [])

    def test_invalid_image_fails(self):
        '''
        Test that an invalid image declines and fails the request.
        '''
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append("zorky-zumba")
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FAILED)
        self.assertNotEqual(req.declined_by, [])

    def test_node(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(image.username, 'zuul')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertNotEqual(nodes[0].host_keys, [])

    def test_node_boot_from_volume(self):
        """Test that an image and node are created from a volume"""
        configfile = self.setup_config('node_boot_from_volume.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_disabled_label(self):
        """Test that a node is not created with min-ready=0"""
        configfile = self.setup_config('node_disabled_label.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual([], self.zk.getNodeRequests())
        self.assertEqual([], self.zk.getNodes())

    def test_node_net_name(self):
        """Test that a node is created with a net name"""
        configfile = self.setup_config('node_net_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')

    def test_node_flavor_name(self):
        """Test that a node is created with a flavor name"""
        configfile = self.setup_config('node_flavor_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_node_vhd_image(self):
        """Test that a image and node are created vhd image"""
        configfile = self.setup_config('node_vhd.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].type, 'fake-label')

    def test_node_vhd_and_qcow2(self):
        """Test label provided by vhd and qcow2 images builds"""
        configfile = self.setup_config('node_vhd_and_qcow2.yaml')
        self._useBuilder(configfile)
        p1_image = self.waitForImage('fake-provider1', 'fake-image')
        p2_image = self.waitForImage('fake-provider2', 'fake-image')

        # We can't guarantee which provider would build the requested
        # nodes, but that doesn't matter so much as guaranteeing that the
        # correct image type is uploaded to the correct provider.
        self.assertEqual(p1_image.format, "vhd")
        self.assertEqual(p2_image.format, "qcow2")

    def test_dib_upload_fail(self):
        """Test that an image upload failure is contained."""
        configfile = self.setup_config('node_upload_fail.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider2', 'fake-image')
        nodes = self.waitForNodes('fake-label', 2)
        self.assertEqual(len(nodes), 2)
        total_nodes = sum(1 for _ in self.zk.nodeIterator())
        self.assertEqual(total_nodes, 2)
        self.assertEqual(nodes[0].provider, 'fake-provider2')
        self.assertEqual(nodes[0].type, 'fake-label')
        self.assertEqual(nodes[0].username, 'zuul')
        self.assertEqual(nodes[1].provider, 'fake-provider2')
        self.assertEqual(nodes[1].type, 'fake-label')
        self.assertEqual(nodes[1].username, 'zuul')

    def test_node_az(self):
        """Test that an image and node are created with az specified"""
        configfile = self.setup_config('node_az.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')
        self.assertEqual(nodes[0].az, 'az1')

    def test_node_ipv6(self):
        """Test that ipv6 existence either way works fine."""
        configfile = self.setup_config('node_ipv6.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider1', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        label1_nodes = self.waitForNodes('fake-label1')
        label2_nodes = self.waitForNodes('fake-label2')

        self.assertEqual(len(label1_nodes), 1)
        self.assertEqual(len(label2_nodes), 1)

        # ipv6 address available
        self.assertEqual(label1_nodes[0].provider, 'fake-provider1')
        self.assertEqual(label1_nodes[0].public_ipv4, 'fake')
        self.assertEqual(label1_nodes[0].public_ipv6, 'fake_v6')
        self.assertEqual(label1_nodes[0].interface_ip, 'fake_v6')

        # ipv6 address unavailable
        self.assertEqual(label2_nodes[0].provider, 'fake-provider2')
        self.assertEqual(label2_nodes[0].public_ipv4, 'fake')
        self.assertEqual(label2_nodes[0].public_ipv6, '')
        self.assertEqual(label2_nodes[0].interface_ip, 'fake')

    def test_node_delete_success(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(zk.READY, nodes[0].state)
        self.assertEqual('fake-provider', nodes[0].provider)
        nodes[0].state = zk.DELETING
        self.zk.storeNode(nodes[0])

        # Wait for this one to be deleted
        self.waitForNodeDeletion(nodes[0])

        # Wait for a new one to take it's place
        new_nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(new_nodes), 1)
        self.assertEqual(zk.READY, new_nodes[0].state)
        self.assertEqual('fake-provider', new_nodes[0].provider)
        self.assertNotEqual(nodes[0], new_nodes[0])

    def test_node_launch_retries(self):
        configfile = self.setup_config('node_launch_retry.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager.createServer_fails = 2
        self.waitForImage('fake-provider', 'fake-image')

        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FAILED)

        # retries in config is set to 2, so 2 attempts to create a server
        self.assertEqual(0, manager.createServer_fails)

    def test_node_delete_failure(self):
        def fail_delete(self, name):
            raise RuntimeError('Fake Error')

        fake_delete = 'nodepool.driver.fake.provider.FakeProvider.deleteServer'
        self.useFixture(fixtures.MonkeyPatch(fake_delete, fail_delete))

        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        self.zk.lockNode(nodes[0], blocking=False)
        nodepool.launcher.NodeDeleter.delete(
            self.zk, pool.getProviderManager('fake-provider'), nodes[0])

        # Make sure our old node is in delete state, even though delete failed
        deleted_node = self.zk.getNode(nodes[0].id)
        self.assertIsNotNone(deleted_node)
        self.assertEqual(deleted_node.state, zk.DELETING)

        # Make sure we have a new, READY node
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider')

    def test_leaked_node(self):
        """Test that a leaked node is deleted"""
        configfile = self.setup_config('leaked_node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")

        # Make sure we have a node built and ready
        self.assertEqual(len(nodes), 1)
        manager = pool.getProviderManager('fake-provider')
        servers = manager.listNodes()
        self.assertEqual(len(servers), 1)

        # Delete the node from ZooKeeper, but leave the instance
        # so it is leaked.
        self.log.debug("Delete node db record so instance is leaked...")
        self.zk.deleteNode(nodes[0])
        self.log.debug("...deleted node db so instance is leaked.")

        # Wait for nodepool to replace it
        self.log.debug("Waiting for replacement pool...")
        new_nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for replacement pool.")
        self.assertEqual(len(new_nodes), 1)

        # Wait for the instance to be cleaned up
        self.waitForInstanceDeletion(manager, nodes[0].external_id)

        # Make sure we end up with only one server (the replacement)
        servers = manager.listNodes()
        self.assertEqual(len(servers), 1)

    def test_max_ready_age(self):
        """Test a node with exceeded max-ready-age is deleted"""
        configfile = self.setup_config('node_max_ready_age.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.log.debug("Waiting for initial pool...")
        nodes = self.waitForNodes('fake-label')
        self.log.debug("...done waiting for initial pool.")

        # Wait for the instance to be cleaned up
        manager = pool.getProviderManager('fake-provider')
        self.waitForInstanceDeletion(manager, nodes[0].external_id)

    def test_label_provider(self):
        """Test that only providers listed in the label satisfy the request"""
        configfile = self.setup_config('node_label_provider.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].provider, 'fake-provider2')

    def _create_pending_request(self):
        req = zk.NodeRequest()
        req.state = zk.PENDING
        req.requestor = 'test_nodepool'
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        # Create a node that is allocated to the request, but not yet assigned
        # within the NodeRequest object
        node = zk.Node()
        node.state = zk.READY
        node.type = 'fake-label'
        node.public_ipv4 = 'fake'
        node.provider = 'fake-provider'
        node.pool = 'main'
        node.allocated_to = req.id
        self.zk.storeNode(node)

        return (req, node)

    def test_lost_requests(self):
        """Test a request left pending is reset and satisfied on restart"""
        (req, node) = self._create_pending_request()

        configfile = self.setup_config('node_lost_requests.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        pool.start()
        req = self.waitForNodeRequest(req, (zk.FULFILLED,))
        # Since our config file has min-ready=0, we should be able to re-use
        # the previously assigned node, thus making sure that the cleanup
        # code reset the 'allocated_to' field.
        self.assertIn(node.id, req.nodes)

    def test_node_deallocation(self):
        """Test an allocated node with a missing request is deallocated"""
        node = zk.Node()
        node.state = zk.READY
        node.type = 'fake-label'
        node.public_ipv4 = 'fake'
        node.provider = 'fake-provider'
        node.allocated_to = "MISSING"
        self.zk.storeNode(node)

        configfile = self.setup_config('node_lost_requests.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()

        while True:
            node = self.zk.getNode(node.id)
            if not node.allocated_to:
                break

    def test_multiple_pools(self):
        """Test that an image and node are created"""
        configfile = self.setup_config('multiple_pools.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        lab1 = self.waitForNodes('fake-label1')
        lab2 = self.waitForNodes('fake-label2')

        self.assertEqual(len(lab1), 1)
        self.assertEqual(lab1[0].provider, 'fake-provider')
        self.assertEqual(lab1[0].type, 'fake-label1')
        self.assertEqual(lab1[0].az, 'az1')
        self.assertEqual(lab1[0].pool, 'pool1')

        self.assertEqual(len(lab2), 1)
        self.assertEqual(lab2[0].provider, 'fake-provider')
        self.assertEqual(lab2[0].type, 'fake-label2')
        self.assertEqual(lab2[0].az, 'az2')
        self.assertEqual(lab2[0].pool, 'pool2')

    def test_unmanaged_image(self):
        """Test node launching using an unmanaged image"""
        configfile = self.setup_config('node_unmanaged_image.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)

        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="fake-image")

        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

    def test_unmanaged_image_provider_name(self):
        """
        Test node launching using an unmanaged image referencing the
        image name as known by the provider.
        """
        configfile = self.setup_config('unmanaged_image_provider_name.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)

        pool.start()
        self.wait_for_config(pool)
        manager = pool.getProviderManager('fake-provider')
        manager._client.create_image(name="provider-named-image")

        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

    def test_paused_gets_declined(self):
        """Test that a paused request, that later gets declined, unpauses."""

        # First config has max-servers set to 2
        configfile = self.setup_config('pause_declined_1.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Create a request that uses all capacity (2 servers)
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)
        req = self.waitForNodeRequest(req)
        self.assertEqual(req.state, zk.FULFILLED)
        self.assertEqual(len(req.nodes), 2)

        # Now that we have 2 nodes in use, create another request that
        # requests two nodes, which should cause the request to pause.
        req2 = zk.NodeRequest()
        req2.state = zk.REQUESTED
        req2.node_types.append('fake-label')
        req2.node_types.append('fake-label')
        self.zk.storeNodeRequest(req2)
        req2 = self.waitForNodeRequest(req2, (zk.PENDING,))

        # Second config decreases max-servers to 1
        self.replace_config(configfile, 'pause_declined_2.yaml')

        # Because the second request asked for 2 nodes, but that now exceeds
        # max-servers, req2 should get declined now, and transition to FAILED
        req2 = self.waitForNodeRequest(req2, (zk.FAILED,))
        self.assertNotEqual(req2.declined_by, [])

    def test_node_auto_floating_ip(self):
        """Test that auto-floating-ip option works fine."""
        configfile = self.setup_config('node_auto_floating_ip.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider1', 'fake-image')
        self.waitForImage('fake-provider2', 'fake-image')
        self.waitForImage('fake-provider3', 'fake-image')
        label1_nodes = self.waitForNodes('fake-label1')
        label2_nodes = self.waitForNodes('fake-label2')
        label3_nodes = self.waitForNodes('fake-label3')

        self.assertEqual(1, len(label1_nodes))
        self.assertEqual(1, len(label2_nodes))
        self.assertEqual(1, len(label3_nodes))

        # auto-floating-ip: False
        self.assertEqual('fake-provider1', label1_nodes[0].provider)
        self.assertEqual('', label1_nodes[0].public_ipv4)
        self.assertEqual('', label1_nodes[0].public_ipv6)
        self.assertEqual('fake', label1_nodes[0].interface_ip)

        # auto-floating-ip: True
        self.assertEqual('fake-provider2', label2_nodes[0].provider)
        self.assertEqual('fake', label2_nodes[0].public_ipv4)
        self.assertEqual('', label2_nodes[0].public_ipv6)
        self.assertEqual('fake', label2_nodes[0].interface_ip)

        # auto-floating-ip: default value
        self.assertEqual('fake-provider3', label3_nodes[0].provider)
        self.assertEqual('fake', label3_nodes[0].public_ipv4)
        self.assertEqual('', label3_nodes[0].public_ipv6)
        self.assertEqual('fake', label3_nodes[0].interface_ip)
