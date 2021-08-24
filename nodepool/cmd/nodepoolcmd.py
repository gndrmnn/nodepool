# Copyright 2013 OpenStack Foundation
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

import json
import logging.config
import os

from prettytable import PrettyTable

from nodepool import launcher
from nodepool import provider_manager
from nodepool import status
from nodepool import zk
from nodepool.cmd import NodepoolApp
from nodepool.cmd.config_validator import ConfigValidator

log = logging.getLogger(__name__)


class NodePoolCmd(NodepoolApp):

    def create_parser(self):
        parser = super(NodePoolCmd, self).create_parser()

        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            help='path to secure file')
        parser.add_argument('--debug', dest='debug', action='store_true',
                            help='show DEBUG level logging')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           dest='command',
                                           help='additional help')

        cmd_list = subparsers.add_parser('list', help='list nodes')
        cmd_list.set_defaults(func=self.list)
        cmd_list.add_argument('--detail', action='store_true',
                              help='Output detailed node info')

        cmd_image_list = subparsers.add_parser(
            'image-list', help='list images from providers')
        cmd_image_list.set_defaults(func=self.image_list)

        cmd_dib_image_list = subparsers.add_parser(
            'dib-image-list',
            help='list images built with diskimage-builder')
        cmd_dib_image_list.set_defaults(func=self.dib_image_list)

        cmd_image_build = subparsers.add_parser(
            'image-build',
            help='build image using diskimage-builder')
        cmd_image_build.add_argument('image', help='image name')
        cmd_image_build.set_defaults(func=self.image_build)

        cmd_alien_image_list = subparsers.add_parser(
            'alien-image-list',
            help='list images not accounted for by nodepool')
        cmd_alien_image_list.set_defaults(func=self.alien_image_list)
        cmd_alien_image_list.add_argument('provider', help='provider name',
                                          nargs='?')

        cmd_delete = subparsers.add_parser(
            'delete',
            help='place a node in the DELETE state')
        cmd_delete.set_defaults(func=self.delete)
        cmd_delete.add_argument('id', help='node id')
        cmd_delete.add_argument('--now',
                                action='store_true',
                                help='delete the node in the foreground')

        cmd_image_delete = subparsers.add_parser(
            'image-delete',
            help='delete an image')
        cmd_image_delete.set_defaults(func=self.image_delete)
        cmd_image_delete.add_argument('--provider', help='provider name',
                                      required=True)
        cmd_image_delete.add_argument('--image', help='image name',
                                      required=True)
        cmd_image_delete.add_argument('--upload-id', help='image upload id',
                                      required=True)
        cmd_image_delete.add_argument('--build-id', help='image build id',
                                      required=True)

        cmd_dib_image_delete = subparsers.add_parser(
            'dib-image-delete',
            help='Delete a dib built image from disk along with all cloud '
                 'uploads of this image')
        cmd_dib_image_delete.set_defaults(func=self.dib_image_delete)
        cmd_dib_image_delete.add_argument('id', help='dib image id')

        cmd_config_validate = subparsers.add_parser(
            'config-validate',
            help='Validate configuration file')
        cmd_config_validate.set_defaults(func=self.config_validate)

        cmd_request_list = subparsers.add_parser(
            'request-list',
            help='list the current node requests')
        cmd_request_list.set_defaults(func=self.request_list)

        cmd_info = subparsers.add_parser(
            'info',
            help='Show provider data from zookeeper')
        cmd_info.add_argument(
            'provider',
            help='Provider name',
            metavar='PROVIDER')
        cmd_info.set_defaults(func=self.info)

        cmd_erase = subparsers.add_parser(
            'erase',
            help='Erase provider data from zookeeper')
        cmd_erase.add_argument(
            'provider',
            help='Provider name',
            metavar='PROVIDER')
        cmd_erase.add_argument(
            '--force',
            help='Bypass the warning prompt',
            action='store_true')
        cmd_erase.set_defaults(func=self.erase)

        cmd_image_pause = subparsers.add_parser(
            'image-pause',
            help='pause an image')
        cmd_image_pause.set_defaults(func=self.image_pause)
        cmd_image_pause.add_argument('image', help='image name')

        cmd_image_unpause = subparsers.add_parser(
            'image-unpause',
            help='unpause an image')
        cmd_image_unpause.set_defaults(func=self.image_unpause)
        cmd_image_unpause.add_argument('image', help='image name')

        cmd_export_image_data = subparsers.add_parser(
            'export-image-data',
            help='Export image data from ZooKeeper')
        cmd_export_image_data.add_argument(
            'path',
            type=str,
            help='Export file path')
        cmd_export_image_data.set_defaults(func=self.export_image_data)

        cmd_import_image_data = subparsers.add_parser(
            'import-image-data',
            help='Import image data to ZooKeeper')
        cmd_import_image_data.add_argument(
            'path',
            type=str,
            help='Import file path')
        cmd_import_image_data.set_defaults(func=self.import_image_data)

        return parser

    def setup_logging(self):
        # NOTE(jamielennox): This should just be the same as other apps
        if self.args.debug:
            m = '%(asctime)s %(levelname)s %(name)s: %(message)s'
            logging.basicConfig(level=logging.DEBUG, format=m)

        elif self.args.logconfig:
            super(NodePoolCmd, self).setup_logging()

        else:
            m = '%(asctime)s %(levelname)s %(name)s: %(message)s'
            logging.basicConfig(level=logging.INFO, format=m)

            l = logging.getLogger('kazoo')
            l.setLevel(logging.WARNING)

    def list(self, node_id=None, detail=False):
        if hasattr(self.args, 'detail'):
            detail = self.args.detail

        fields = ['id', 'provider', 'label', 'server_id',
                  'public_ipv4', 'ipv6', 'state', 'age', 'locked']
        if detail:
            fields.extend(['pool', 'hostname', 'private_ipv4', 'AZ',
                           'connection_port', 'launcher',
                           'allocated_to', 'hold_job',
                           'comment'])
        results = status.node_list(self.zk, node_id)
        print(status.output(results, 'pretty', fields))

    def dib_image_list(self):
        results = status.dib_image_list(self.zk)
        print(status.output(results, 'pretty'))

    def image_list(self):
        results = status.image_list(self.zk)
        print(status.output(results, 'pretty'))

    def image_build(self, diskimage=None):
        diskimage = diskimage or self.args.image
        if diskimage not in self.pool.config.diskimages:
            # only can build disk images, not snapshots
            raise Exception("Trying to build a non disk-image-builder "
                            "image: %s" % diskimage)

        if self.pool.config.diskimages[diskimage].pause:
            raise Exception(
                "Skipping build request for image %s; paused" % diskimage)

        self.zk.submitBuildRequest(diskimage)

    def alien_image_list(self):
        self.pool.updateConfig()

        t = PrettyTable(["Provider", "Name", "Image ID"])
        t.align = 'l'

        for provider in self.pool.config.providers.values():
            if (self.args.provider and
                    provider.name != self.args.provider):
                continue
            manager = self.pool.getProviderManager(provider.name)

            # Build list of provider images as known by the provider
            provider_images = []
            try:
                # Only consider images marked as managed by nodepool.
                # Prevent cloud-provider images from showing
                # up in alien list since we can't do anything about them
                # anyway.
                provider_images = [
                    image for image in manager.listImages()
                    if 'nodepool_build_id' in image['properties']]
            except Exception as e:
                log.warning("Exception listing alien images for %s: %s"
                            % (provider.name, str(e)))

            alien_ids = []
            uploads = []
            for image in provider.diskimages:
                # Build list of provider images as recorded in ZK
                for bnum in self.zk.getBuildNumbers(image):
                    uploads.extend(
                        self.zk.getUploads(image, bnum,
                                           provider.name,
                                           states=[zk.READY])
                    )

            # Calculate image IDs present in the provider, but not in ZK
            provider_image_ids = set([img['id'] for img in provider_images])
            zk_image_ids = set([img.external_id for img in uploads])
            alien_ids = provider_image_ids - zk_image_ids

            for image in provider_images:
                if image['id'] in alien_ids:
                    t.add_row([provider.name, image['name'], image['id']])

        print(t)

    def delete(self):
        node = self.zk.getNode(self.args.id)
        if not node:
            print("Node id %s not found" % self.args.id)
            return

        self.zk.lockNode(node, blocking=True, timeout=5)

        if self.args.now:
            if node.provider not in self.pool.config.providers:
                print("Provider %s for node %s not defined on this launcher" %
                      (node.provider, node.id))
                return
            provider = self.pool.config.providers[node.provider]
            manager = provider_manager.get_provider(provider)
            manager.start(self.zk)
            node_deleter = manager.startNodeCleanup(node)
            node_deleter.join()
            manager.stop()
        else:
            node.state = zk.DELETING
            self.zk.storeNode(node)
            self.zk.unlockNode(node)

        self.list(node_id=node.id)

    def dib_image_delete(self):
        (image, build_num) = self.args.id.rsplit('-', 1)
        build = self.zk.getBuild(image, build_num)
        if not build:
            print("Build %s not found" % self.args.id)
            return

        if build.state == zk.BUILDING:
            print("Cannot delete a build in progress")
            return

        build.state = zk.DELETING
        self.zk.storeBuild(image, build, build.id)

    def image_delete(self):
        provider_name = self.args.provider
        image_name = self.args.image
        build_id = self.args.build_id
        upload_id = self.args.upload_id

        image = self.zk.getImageUpload(image_name, build_id, provider_name,
                                       upload_id)
        if not image:
            print("Image upload not found")
            return

        if image.state == zk.UPLOADING:
            print("Cannot delete because image upload in progress")
            return

        image.state = zk.DELETING
        self.zk.storeImageUpload(image.image_name, image.build_id,
                                 image.provider_name, image, image.id)

    def erase(self):
        def do_erase(provider_name, provider_builds, provider_nodes):
            print("Erasing build data for %s..." % provider_name)
            self.zk.removeProviderBuilds(provider_name, provider_builds)
            print("Erasing node data for %s..." % provider_name)
            self.zk.removeProviderNodes(provider_name, provider_nodes)

        provider_name = self.args.provider
        provider_builds = self.zk.getProviderBuilds(provider_name)
        provider_nodes = self.zk.getProviderNodes(provider_name)

        if self.args.force:
            do_erase(provider_name, provider_builds, provider_nodes)
        else:
            print("\nWARNING! This action is not reversible!")
            answer = input("Erase ZooKeeper data for provider %s? [N/y] " %
                           provider_name)
            if answer.lower() != 'y':
                print("Aborting. No data erased.")
            else:
                do_erase(provider_name, provider_builds, provider_nodes)

    def info(self):
        provider_name = self.args.provider
        provider_uploads = self.zk.getProviderUploads(provider_name)
        provider_nodes = self.zk.getProviderNodes(provider_name)

        print("ZooKeeper data for provider %s\n" % provider_name)

        print("Image data:")
        t = PrettyTable(['Image Name', 'Build ID', 'Upload IDs'])
        t.align = 'l'
        for image in sorted(provider_uploads):
            for build in sorted(provider_uploads[image]):
                uploads = provider_uploads[image][build]
                upload_ids = sorted([u.id for u in uploads])
                t.add_row([image, build, ','.join(upload_ids)])
        print(t)

        print("\nNodes:")
        t = PrettyTable(['ID', 'Server ID'])
        t.align = 'l'
        for node in provider_nodes:
            t.add_row([node.id, node.external_id])
        print(t)

    def config_validate(self):
        validator = ConfigValidator(self.args.config)
        return validator.validate()
        # TODO(asselin,yolanda): add validation of secure.conf

    def request_list(self):
        results = status.request_list(self.zk)
        print(status.output(results, 'pretty'))

    def image_pause(self):
        image_name = self.args.image
        self.zk.setImagePaused(image_name, True)

    def image_unpause(self):
        image_name = self.args.image
        self.zk.setImagePaused(image_name, False)

    def export_image_data(self):
        data = self.zk.exportImageData()
        with open(os.open(self.args.path,
                          os.O_CREAT | os.O_WRONLY, 0o600), 'w') as f:
            json.dump(data, f)

    def import_image_data(self):
        with open(self.args.path, 'r') as f:
            self.zk.importImageData(json.load(f))

    def _wait_for_threads(self, threads):
        for t in threads:
            if t:
                t.join()

    def run(self):
        self.zk = None

        # no arguments, print help messaging, then exit with error(1)
        if not self.args.command:
            self.parser.print_help()
            return 1
        # commands which do not need to start-up or parse config
        if self.args.command in ('config-validate'):
            return self.args.func()

        self.pool = launcher.NodePool(self.args.secure, self.args.config)
        config = self.pool.loadConfig()

        # commands needing ZooKeeper
        if self.args.command in ('image-build', 'dib-image-list',
                                 'image-list', 'dib-image-delete',
                                 'image-delete', 'alien-image-list',
                                 'list', 'delete',
                                 'request-list', 'info', 'erase',
                                 'image-pause', 'image-unpause',
                                 'export-image-data', 'import-image-data'):
            self.zk = zk.ZooKeeper(enable_cache=False)
            self.zk.connect(
                list(config.zookeeper_servers.values()),
                tls_cert=config.zookeeper_tls_cert,
                tls_key=config.zookeeper_tls_key,
                tls_ca=config.zookeeper_tls_ca)

        self.pool.setConfig(config)
        self.args.func()

        if self.zk:
            self.zk.disconnect()


def main():
    return NodePoolCmd.main()
