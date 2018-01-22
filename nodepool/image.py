#!/usr/bin/env python
#
# Copyright 2018 OpenStack Foundation
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


from nodepool import zk as _zk


def image_build(zk, pool, diskimage):
    if diskimage not in pool.config.diskimages:
        # only can build disk images, not snapshots
        raise Exception("Trying to build a non disk-image-builder "
                        "image: %s" % diskimage)

    if pool.config.diskimages[diskimage].pause:
        raise Exception(
            "Skipping build request for image %s; paused" % diskimage)

    zk.submitBuildRequest(diskimage)


def dib_image_delete(zk, image, build_num):
    build = zk.getBuild(image, build_num)
    if not build:
        raise Exception("Build %s-%s not found" % (image, build_num))

    if build.state == _zk.BUILDING:
        raise Exception("Cannot delete a build in progress")

    build.state = _zk.DELETING
    zk.storeBuild(image, build, build.id)


def image_delete(zk, provider_name, image_name, build_id, upload_id):
    image = zk.getImageUpload(image_name, build_id, provider_name,
                              upload_id)
    if not image:
        raise Exception("Image upload not found")

    if image.state == _zk.UPLOADING:
        raise Exception("Cannot delete because image upload in progress")

    image.state = _zk.DELETING
    zk.storeImageUpload(image.image_name, image.build_id,
                        image.provider_name, image, image.id)
