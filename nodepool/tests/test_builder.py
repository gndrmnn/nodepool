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

from nodepool import builder, tests


class TestNodepoolBuilder(tests.DBTestCase):

    def test_parse_config(self):
        configfile = self.setup_config('node_dib.yaml')
        nb = builder.NodePoolBuilder(None, None)
        images_dir, diskimages = nb.parse_config(configfile)

        di = builder.DiskImage(
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
