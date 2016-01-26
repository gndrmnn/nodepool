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

import json
import logging
import uuid
import threading

import gear

import nodedb


class WatchableJob(gear.Job):
    def __init__(self, *args, **kwargs):
        super(WatchableJob, self).__init__(*args, **kwargs)
        self._completion_handlers = []
        self._event = threading.Event()

    def _handleCompletion(self, mode=None):
        self._event.set()
        for handler in self._completion_handlers:
            handler(self)

    def addCompletionHandler(self, handler):
        self._completion_handlers.append(handler)

    def onCompleted(self):
        self._handleCompletion()

    def onFailed(self):
        self._handleCompletion()

    def onDisconnect(self):
        self._handleCompletion()

    def onWorkStatus(self):
        pass

    def waitForCompletion(self, timeout=None):
        return self._event.wait(timeout)


class NodepoolJob(WatchableJob):
    def __init__(self, job_name, job_data_obj, nodepool):
        job_uuid = str(uuid.uuid4().hex)
        job_data = json.dumps(job_data_obj)
        super(NodepoolJob, self).__init__(job_name, job_data, job_uuid)
        self.nodepool = nodepool

    def getDbSession(self):
        return self.nodepool.getDB().getSession()


class ImageBuildJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageBuildJob")

    def __init__(self, image_name, image_id, nodepool):
        self.image_id = image_id
        job_data = {'image-id': str(image_id)}
        job_name = 'image-build:%s' % image_name
        super(ImageBuildJob, self).__init__(job_name, job_data, nodepool)

    def _deleteImage(self, record_only=False):
        with self.getDbSession() as session:
            self.log.debug('DIB Image %s (id %d) failed to build. Deleting.',
                           self.name.split(':', 1)[0], self.image_id)
            dib_image = session.getDibImage(self.image_id)
            if not record_only:
                self.nodepool.deleteDibImage(dib_image)
            else:
                dib_image.delete()

    def onCompleted(self):
        with self.getDbSession() as session:
            dib_image = session.getDibImage(self.image_id)
            if dib_image is None:
                self.log.error(
                    'Unable to find matching dib_image for image_id %s',
                    self.image_id)
                return
            dib_image.state = nodedb.READY
            session.commit()
            self.log.debug('DIB Image %s (id %d) is ready',
                           self.name.split(':', 1)[0], self.image_id)
        super(ImageBuildJob, self).onCompleted()

    def onFailed(self):
        self.log.error('DIB Image %s (id %d) failed to build. Deleting.',
                       self.name.split(':', 1)[0], self.image_id)
        self._deleteImage(True)
        super(ImageBuildJob, self).onFailed()

    def onDisconnect(self):
        self.log.error('DIB Image %s (id %d) failed due to gear disconnect.',
                       self.name.split(':', 1)[0], self.image_id)
        self._deleteImage()
        super(ImageBuildJob, self).onDisconnect()


class ImageUploadJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageUploadJob")

    def __init__(self, image_id, provider_name, external_name, snap_image_id,
                 nodepool):
        self.image_id = image_id
        self.snap_image_id = snap_image_id
        job_data = {
            'image-name': external_name,
            'provider': provider_name
        }
        job_name = 'image-upload:%s' % image_id
        super(ImageUploadJob, self).__init__(job_name, job_data, nodepool)

    def onCompleted(self):
        job_data = json.loads(self.data[0])
        external_id = job_data['external-id']

        with self.getDbSession() as session:
            snap_image = session.getSnapshotImage(self.snap_image_id)
            if snap_image is None:
                self.log.error(
                    'Unable to find matching snap_image for job_id %s',
                    self.unique)
                return

            snap_image.external_id = external_id
            snap_image.state = nodedb.READY
            session.commit()
            self.log.debug('Image %s is ready with external_id %s',
                           self.snap_image_id, external_id)
        super(ImageUploadJob, self).onCompleted()

    def onDisconnect(self):
        self.log.error('Image %s failed to upload due to gear disconnect.',
                       self.snap_image_id)
        self.nodepool.deleteImage(self.snap_image_id)
        super(ImageUploadJob, self).onDisconnect()


class ImageDeleteJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageDeleteJob")

    def __init__(self, image_id, nodepool):
        self.image_id = image_id
        job_name = 'image-delete:%s' % image_id
        super(ImageDeleteJob, self).__init__(job_name, '', nodepool)
