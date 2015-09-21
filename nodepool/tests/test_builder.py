# Copyright (C) 2015 Hewlett-Packard Development Company, L.P.
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

import os

import fixtures

from nodepool import builder, tests

class TestNodepoolBuilderDibImage(tests.BaseTestCase):
    def test_from_path(self):
        image = builder.DibImageFile.from_path('/foo/bar/myid1234:myname.qcow2')
        self.assertEqual(image.name, 'myname')
        self.assertEqual(image.image_id, 'myid1234')
        self.assertEqual(image.extension, 'qcow2')

    def test_from_image_id(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path = os.path.join(tempdir.path, 'myid1234:myname.qcow2')
        open(image_path, 'w')

        images = builder.DibImageFile.from_image_id(tempdir.path, 'myid1234')
        self.assertEqual(len(images), 1)

        image = images[0]
        self.assertEqual(image.name, 'myname')
        self.assertEqual(image.image_id, 'myid1234')
        self.assertEqual(image.extension, 'qcow2')

    def test_from_id_multiple(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path_1 = os.path.join(tempdir.path, 'myid1234:myname.qcow2')
        image_path_2 = os.path.join(tempdir.path, 'myid1234:myname.raw')
        open(image_path_1, 'w')
        open(image_path_2, 'w')

        images = builder.DibImageFile.from_image_id(tempdir.path, 'myid1234')
        images = sorted(images, key=lambda x: x.extension)
        self.assertEqual(len(images), 2)

        self.assertEqual(images[0].extension, 'qcow2')
        self.assertEqual(images[1].extension, 'raw')

    def test_from_images_dir(self):
        tempdir = fixtures.TempDir()
        self.useFixture(tempdir)
        image_path_1 = os.path.join(tempdir.path, 'myid1234:myname.qcow2')
        image_path_2 = os.path.join(tempdir.path, 'myid1234:myname.raw')
        open(image_path_1, 'w')
        open(image_path_2, 'w')

        images = builder.DibImageFile.from_images_dir(tempdir.path)
        images = sorted(images, key=lambda x: x.extension)
        self.assertEqual(len(images), 2)

        self.assertEqual(images[0].name, 'myname')
        self.assertEqual(images[0].image_id, 'myid1234')
        self.assertEqual(images[0].extension, 'qcow2')
        self.assertEqual(images[1].name, 'myname')
        self.assertEqual(images[1].image_id, 'myid1234')
        self.assertEqual(images[1].extension, 'raw')

    def test_to_path(self):
        image = builder.DibImageFile('myid1234', 'myname', 'qcow2')
        self.assertEqual(image.to_path('/imagedir'),
                         '/imagedir/myid1234:myname.qcow2')
        self.assertEqual(image.to_path('/imagedir/'),
                         '/imagedir/myid1234:myname.qcow2')
        self.assertEqual(image.to_path('/imagedir/', False),
                         '/imagedir/myid1234:myname')

        image = builder.DibImageFile('myid1234', 'myname')
        self.assertRaises(ValueError, image.to_path, '/imagedir/')


class TestNodepoolBuilder(tests.DBTestCase):
    def test_parse_config(self):
        configfile = self.setup_config('node_dib.yaml')
        nb = builder.NodePoolBuilder(None, None)
        images_dir, diskimages = nb.parse_config(configfile)

        di = builder.DiskImageConfig(
            name='fake-dib-image',
            elements=u'fedora vm',
            release='21',
            env_vars={
                'BASE_IMAGE_FILE': 'Fedora-Cloud-Base-20141029-21_Beta.x86_64.'
                                   'qcow2',
                'DIB_IMAGE_CACHE': '/opt/dib_cache',
                'DIB_CLOUD_IMAGES': 'http://download.fedoraproject.org/pub/fed'
                                    'ora/linux/releases/test/21-Beta/Cloud/Ima'
                                    'ges/x86_64/',
                'TMPDIR': '/opt/dib_tmp'
            },
            image_types=set(['qcow2'])
        )
        self.assertEqual([di], diskimages)
