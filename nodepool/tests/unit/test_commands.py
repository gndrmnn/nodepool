# Copyright (C) 2015 OpenStack Foundation
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
import os.path
import shutil
import sys  # noqa making sure its available for monkey patching
import tempfile

import fixtures
import mock
import testtools

from nodepool import exceptions as npe
from nodepool.cmd import nodepoolcmd
from nodepool import tests
from nodepool.zk import zookeeper as zk
from nodepool.nodeutils import iterate_timeout


class TestNodepoolCMD(tests.DBTestCase):
    def setUp(self):
        super(TestNodepoolCMD, self).setUp()

    def patch_argv(self, *args):
        argv = ["nodepool"]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_listed(self, configfile, cmd, col, val, count, col_count=0):
        log = logging.getLogger("tests.PrettyTableMock")
        self.patch_argv("-c", configfile, *cmd)
        for _ in iterate_timeout(10, AssertionError, 'assert listed'):
            try:
                with mock.patch('prettytable.PrettyTable.add_row') as \
                        m_add_row:
                    nodepoolcmd.main()
                    rows_with_val = 0
                    # Find add_rows with the status were looking for
                    for args, kwargs in m_add_row.call_args_list:
                        row = args[0]
                        if col_count:
                            self.assertEqual(len(row), col_count)
                        log.debug(row)
                        if col < len(row) and row[col] == val:
                            rows_with_val += 1
                    self.assertEqual(rows_with_val, count)
                break
            except AssertionError:
                # retry
                pass

    def assert_alien_images_listed(self, configfile, image_cnt, image_id):
        self.assert_listed(configfile, ['alien-image-list'], 2, image_id,
                           image_cnt)

    def assert_alien_images_empty(self, configfile):
        self.assert_alien_images_listed(configfile, 0, 0)

    def assert_images_listed(self, configfile, image_cnt, status="ready"):
        self.assert_listed(configfile, ['image-list'], 6, status, image_cnt)

    def assert_nodes_listed(self, configfile, node_cnt, status="ready",
                            detail=False, validate_col_count=False):
        cmd = ['list']
        col_count = 9
        if detail:
            cmd += ['--detail']
            col_count = 21
        if not validate_col_count:
            col_count = 0
        self.assert_listed(configfile, cmd, 6, status, node_cnt, col_count)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)

    def test_image_delete_invalid(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-delete",
                        "--provider", "invalid-provider",
                        "--image", "invalid-image",
                        "--build-id", "invalid-build-id",
                        "--upload-id", "invalid-upload-id")
        nodepoolcmd.main()

    def test_image_delete(self):
        configfile = self.setup_config("node.yaml")
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        image = self.zk.getMostRecentImageUpload('fake-image', 'fake-provider')
        self.patch_argv("-c", configfile, "image-delete",
                        "--provider", "fake-provider",
                        "--image", "fake-image",
                        "--build-id", image.build_id,
                        "--upload-id", image.id)
        nodepoolcmd.main()
        self.waitForUploadRecordDeletion('fake-provider', 'fake-image',
                                         image.build_id, image.id)

    def test_alien_image_list_empty(self):
        configfile = self.setup_config("node.yaml")
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()
        self.assert_alien_images_empty(configfile)

    def test_alien_image_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.driver.fake.provider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()

    def test_list_nodes(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        for _ in iterate_timeout(10, Exception, "assert nodes are listed"):
            try:
                self.assert_nodes_listed(configfile, 1, detail=False,
                                         validate_col_count=True)
                break
            except AssertionError:
                # node is not listed yet, retry later
                pass

    def test_list_nodes_detail(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')
        for _ in iterate_timeout(10, Exception, "assert nodes are listed"):
            try:
                self.assert_nodes_listed(configfile, 1, detail=True,
                                         validate_col_count=True)
                break
            except AssertionError:
                # node is not listed yet, retry later
                pass

    def test_config_validate(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'good.yaml')
        self.patch_argv('-c', config, 'config-validate')
        nodepoolcmd.main()

    def test_dib_image_list(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)

    def test_dib_image_cmd_pause(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        # Pause and verify
        self.patch_argv("-c", configfile, "image-pause", "fake-image")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 4, 'paused', 1)
        # Repeat to make sure it's a noop
        self.patch_argv("-c", configfile, "image-pause", "fake-image")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 4, 'paused', 1)
        # Unpause and verify
        self.patch_argv("-c", configfile, "image-unpause", "fake-image")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        # Repeat to make sure it's a noop
        self.patch_argv("-c", configfile, "image-unpause", "fake-image")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)

    def test_image_status(self):
        configfile = self.setup_config('node.yaml')
        builder = self.useBuilder(configfile)
        # Make sure we have enough time to test for the build request
        # before it's processed by the build worker.
        for worker in builder._build_workers:
            worker._interval = 60
        self.waitForImage('fake-provider', 'fake-image')
        self.zk.submitBuildRequest("fake-image")
        self.assert_listed(configfile, ['image-status'],
                           0, 'fake-image', 1)

    def test_dib_image_build_pause(self):
        configfile = self.setup_config('node_diskimage_pause.yaml')
        self.useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-build", "fake-image")
        with testtools.ExpectedException(Exception):
            nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 0)

    def test_dib_image_pause(self):
        configfile = self.setup_config('node_diskimage_pause.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label2')
        self.assertEqual(len(nodes), 1)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 0)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image2', 1)

    def test_dib_image_upload_pause(self):
        configfile = self.setup_config('node_image_upload_pause.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label2')
        self.assertEqual(len(nodes), 1)
        # Make sure diskimages were built.
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 1)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image2', 1)
        # fake-image will be missing, since it is paused.
        self.assert_listed(configfile, ['image-list'], 3, 'fake-image', 0)
        self.assert_listed(configfile, ['image-list'], 3, 'fake-image2', 1)

    def test_dib_image_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        # Check the image exists
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        builds = self.zk.getMostRecentBuilds(1, 'fake-image', zk.READY)
        # Delete the image
        self.patch_argv('-c', configfile, 'dib-image-delete',
                        'fake-image-%s' % (builds[0].id))
        nodepoolcmd.main()
        self.waitForBuildDeletion('fake-image', '0000000001')
        # Check that fake-image-0000000001 doesn't exist
        self.assert_listed(
            configfile, ['dib-image-list'], 0, 'fake-image-0000000001', 0)

    def test_dib_image_delete_custom_image_creation(self):
        # Test deletion of images without a .d manifest folder
        # e.g. When using a custom image creation
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        builder = self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
        # Check the image exists
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        builds = self.zk.getMostRecentBuilds(1, 'fake-image', zk.READY)
        # Delete manifest folder to simulate custom image creation
        shutil.rmtree(os.path.join(builder._config.images_dir,
                                   'fake-image-0000000001.d'))
        # But ensure image is still present
        self.assertTrue(
            os.path.exists(os.path.join(builder._config.images_dir,
                                        'fake-image-0000000001.qcow2')))
        # Delete the image
        self.patch_argv('-c', configfile, 'dib-image-delete',
                        'fake-image-%s' % (builds[0].id))
        nodepoolcmd.main()
        self.waitForBuildDeletion('fake-image', '0000000001')
        # Check that fake-image-0000000001 doesn't exist
        self.assert_listed(
            configfile, ['dib-image-list'], 0, 'fake-image-0000000001', 0)
        self.assertFalse(
            os.path.exists(os.path.join(builder._config.images_dir,
                                        'fake-image-0000000001.qcow2')))

    def test_dib_image_delete_two_builders(self):
        # This tests deleting an image when its builder is offline
        # 1. Build the image with builder1
        configfile1 = self.setup_config('node.yaml')
        builder1 = self.useBuilder(configfile1)
        self.waitForImage('fake-provider', 'fake-image')
        # Check the image exists
        builds = self.zk.getMostRecentBuilds(1, 'fake-image', zk.READY)

        # 2. Stop builder1; start builder2
        for worker in builder1._upload_workers:
            worker.shutdown()
            worker.join()
        builder1.stop()
        # setup_config() makes a new images_dir each time, so this
        # acts as a different builder.
        configfile2 = self.setup_config('node.yaml')
        builder2 = self.useBuilder(configfile2)

        # 3. Set image to 'deleted' in ZK
        self.patch_argv('-c', configfile1, 'dib-image-delete',
                        'fake-image-%s' % (builds[0].id))
        nodepoolcmd.main()

        # 4. Verify builder2 deleted the ZK records, but image is still on disk
        self.waitForBuildDeletion('fake-image', '0000000001')
        self.assertTrue(
            os.path.exists(os.path.join(builder1._config.images_dir,
                                        'fake-image-0000000001.d')))
        self.assertFalse(
            os.path.exists(os.path.join(builder2._config.images_dir,
                                        'fake-image-0000000001.d')))

        # 5. Start builder1 and verify it deletes image on disk
        builder1 = self.useBuilder(configfile1)
        for _ in iterate_timeout(30, AssertionError, 'image file delete'):
            if not os.path.exists(os.path.join(builder1._config.images_dir,
                                               'fake-image-0000000001.d')):
                break

    def test_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Delete node
        self.patch_argv('-c', configfile, 'delete', nodes[0].id)
        nodepoolcmd.main()
        self.waitForNodeDeletion(nodes[0])

        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 0)

    def test_delete_now(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)

        # (Shrews): This is a hack to avoid a race with the DeletedNodeWorker
        # thread where it may see that our direct call to NodeDeleter.delete()
        # has changed the node state to DELETING and lock the node during the
        # act of deletion, but *after* the lock znode child has been deleted
        # and *before* kazoo has fully removed the node znode itself. This race
        # causes the rare kazoo.exceptions.NotEmptyError in this test because
        # a new lock znode gets created (that the original delete does not see)
        # preventing the node znode from being deleted.
        pool.delete_interval = 5

        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Delete node
        self.patch_argv('-c', configfile, 'delete', '--now', nodes[0].id)
        nodepoolcmd.main()
        self.waitForNodeDeletion(nodes[0])

        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 0)

    def test_hold(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Hold node
        self.patch_argv('-c', configfile, 'hold', nodes[0].id)
        nodepoolcmd.main()

        # Assert the node is on hold
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.HOLD)

        # Re-enable node by deleting
        old_node_id = nodes[0].id
        self.patch_argv('-c', configfile, 'delete', nodes[0].id)
        nodepoolcmd.main()

        # Assert that the node is ready
        self.waitForNodeDeletion(nodes[0])
        new_nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(new_nodes), 1)
        self.assert_listed(configfile, ['list'], 0, new_nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)
        self.assertNotEqual(old_node_id, new_nodes[0].id)

        # Request a node
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.assertEqual(len(req.nodes), 0)
        self.zk.storeNodeRequest(req)

        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req, (zk.FULFILLED,))
        self.assertEqual(len(req.nodes), 1)

    def test_attempt_hold_busy_node(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Request a node
        req1 = zk.NodeRequest()
        req1.state = zk.REQUESTED
        req1.node_types.append('fake-label')
        self.zk.storeNodeRequest(req1)

        # Wait for node request
        self.log.debug("Waiting for 1st request %s", req1.id)
        req1 = self.waitForNodeRequest(req1, (zk.FULFILLED,))
        self.assertEqual(len(req1.nodes), 1)

        # Lock node and set it as in-use
        node = self.zk.getNode(req1.nodes[0])
        self.zk.lockNode(node, blocking=False)
        node.state = zk.IN_USE
        self.zk.storeNode(node)
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.IN_USE)

        # Attempt to hold the node, this should fail
        # since another process holds the lock
        with testtools.ExpectedException(npe.TimeoutException):
            self.patch_argv('-c', configfile, 'hold', nodes[0].id)
            nodepoolcmd.main()

    def test_attempt_request_held_static_node(self):
        configfile = self.setup_config('static-basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Hold node
        self.patch_argv('-c', configfile, 'hold', nodes[0].id)
        nodepoolcmd.main()

        # Assert the node is on HOLD
        self.assertEqual(len(nodes), 1)
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.HOLD)

        # Prepare node request
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        # Make a node request
        # Expect to timeout since the node is not ready
        self.log.debug("Waiting for request %s", req.id)
        req = self.zk.getNodeRequest(req.id)

        with testtools.ExpectedException(Exception):
            req = self.waitForNodeRequest(req, (zk.FULFILLED,), max_time=30)

        self.assertEqual(len(req.nodes), 0)

    def test_attempt_request_held_node(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Hold node
        self.patch_argv('-c', configfile, 'hold', nodes[0].id)
        nodepoolcmd.main()

        # Assert the node is on HOLD
        self.assertEqual(len(nodes), 1)
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.HOLD)

        # Prepare node request
        req = zk.NodeRequest()
        req.state = zk.REQUESTED
        req.node_types.append('fake-label')
        self.zk.storeNodeRequest(req)

        # Make a node request
        self.log.debug("Waiting for request %s", req.id)
        req = self.waitForNodeRequest(req, (zk.FULFILLED,))

        # Make sure we did not assign the held node
        # but another node as long as the quota is not reached
        self.assertNotEqual(nodes[0].id, req.nodes[0])

    def test_image_build(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)

        # wait for the scheduled build to arrive
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        image = self.zk.getMostRecentImageUpload('fake-image', 'fake-provider')

        # now do the manual build request
        self.patch_argv("-c", configfile, "image-build", "fake-image")
        nodepoolcmd.main()

        self.waitForImage('fake-provider', 'fake-image', [image])
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 2)

    def test_request_list(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        req = zk.NodeRequest()
        req.state = zk.PENDING   # so it will be ignored
        req.node_types = ['fake-label']
        req.requestor = 'test_request_list'
        self.zk.storeNodeRequest(req)

        self.assert_listed(configfile, ['request-list'], 0, req.id, 1)

    def test_without_argument(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile)
        result = nodepoolcmd.main()
        self.assertEqual(1, result)

    def test_info_and_erase(self):
        configfile = self.setup_config('info_cmd_two_provider.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        p1_image = self.waitForImage('fake-provider', 'fake-image')
        p2_image = self.waitForImage('fake-provider2', 'fake-image')
        p1_nodes = self.waitForNodes('fake-label')
        p2_nodes = self.waitForNodes('fake-label2')

        # Get rid of the second provider so that when we remove its
        # data from ZooKeeper, the builder and launcher don't attempt to
        # recreate the data.
        self.replace_config(configfile, 'info_cmd_two_provider_remove.yaml')

        IMAGE_NAME_COL = 0
        BUILD_ID_COL = 1
        UPLOAD_ID_COL = 2
        NODE_ID_COL = 0

        # Verify that the second provider image is listed
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            IMAGE_NAME_COL, 'fake-image', 1)
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            BUILD_ID_COL, p2_image.build_id, 1)
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            UPLOAD_ID_COL, p2_image.id, 1)

        # Verify that the second provider node is listed in the second table.
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            NODE_ID_COL, p2_nodes[0].id, 1)

        # Erase the data for the second provider
        self.patch_argv(
            "-c", configfile, 'erase', 'fake-provider2', '--force')
        nodepoolcmd.main()

        # Verify that no image or node for the second provider is listed
        # after the previous erase. With no build data, the image name should
        # not even show up.
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            IMAGE_NAME_COL, 'fake-image', 0)
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            NODE_ID_COL, p2_nodes[0].id, 0)

        # Verify that we did not affect the first provider
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(p1_image, image)
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(1, len(nodes))
        self.assertEqual(p1_nodes[0], nodes[0])

    def test_export_image_data(self):
        configfile = self.setup_config('node.yaml')
        builder = self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        build = self.waitForBuild('fake-image', '0000000001')
        # Delete the first build so that we have a hole in our
        # numbering.  This lets us validate that we reconstruct the
        # sequence state correctly.
        build.state = zk.DELETING
        with self.zk.imageBuildLock('fake-image', blocking=True, timeout=1):
            self.zk.storeBuild('fake-image', build, '0000000001')
        self.waitForBuildDeletion('fake-image', '0000000001')
        self.waitForBuild('fake-image', '0000000002')

        pool.stop()
        for worker in builder._upload_workers:
            worker.shutdown()
            worker.join()
        builder.stop()
        # Save a copy of the data in ZK
        old_data = self.getZKTree('/nodepool/images')
        # We aren't backing up the lock data
        old_data.pop('/nodepool/images/fake-image/builds/0000000002'
                     '/providers/fake-provider/images/lock')
        old_data.pop('/nodepool/images/fake-image/builds/lock')

        with tempfile.NamedTemporaryFile() as tf:
            self.patch_argv(
                "-c", configfile, 'export-image-data', tf.name)
            nodepoolcmd.main()
            # Delete data from ZK
            self.zk.kazoo_client.delete('/nodepool', recursive=True)

            self.patch_argv(
                "-c", configfile, 'import-image-data', tf.name)
            nodepoolcmd.main()

        new_data = self.getZKTree('/nodepool/images')
        self.assertEqual(new_data, old_data)

        # Now restart the builder to make sure new builds/uploads work
        builder = self.useBuilder(configfile)

        # First test a new upload of the existing image and make sure
        # it uses the correct sequence number.
        upload = self.waitForUpload('fake-provider', 'fake-image',
                                    '0000000002', '0000000001')
        upload.state = zk.DELETING
        with self.zk.imageUploadLock(upload.image_name, upload.build_id,
                                     upload.provider_name, blocking=True,
                                     timeout=1):
            self.zk.storeImageUpload(upload.image_name, upload.build_id,
                                     upload.provider_name, upload, upload.id)
        # We skip at least one number because upload lock is a sequence
        # node too (this is why builds and uploads start at 1 instead of 0).
        upload = self.waitForUpload('fake-provider', 'fake-image',
                                    '0000000002', '0000000003')

        # Now build a new image and make sure it uses the correct
        # sequence number.
        build = self.waitForBuild('fake-image', '0000000002')
        # Expire rebuild-age (default: 1day) to force a new build.
        build.state_time -= 86400
        with self.zk.imageBuildLock('fake-image', blocking=True, timeout=1):
            self.zk.storeBuild('fake-image', build, '0000000002')
        # We skip at least one number because build lock is a sequence
        # node too (this is why builds and uploads start at 1 instead of 0).
        self.waitForBuild('fake-image', '0000000004')
