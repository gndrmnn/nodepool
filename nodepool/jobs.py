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
        self._completed = False
        self._event = threading.Event()

    def _handleCompletion(self, mode=None):
        self._completed = True
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

    def waitForCompletion(self, timeout=None):
        if not self._completed:
            return self._event.wait(timeout)


class NodepoolJob(WatchableJob):
    def __init__(self, job_name, job_data_obj):
        job_uuid = str(uuid.uuid4().hex)
        job_data = json.dumps(job_data_obj)
        super(NodepoolJob, self).__init__(job_name, job_data, job_uuid)


class ImageBuildJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageBuildJob")

    def __init__(self, image_name, image_id, db):
        self.db = db
        self.image_id = image_id
        job_data = {'image-id': str(image_id)}
        job_name = 'image-build:%s' % image_name
        super(ImageBuildJob, self).__init__(job_name, job_data)

    def onCompleted(self):
        with self.db.getSession() as session:
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
        with self.db.getSession() as session:
            self.log.debug('DIB Image %s (id %d) failed to build. Deleting.',
                           self.name.split(':', 1)[0], self.image_id)
            dib_image = session.getDibImage(self.image_id)
            dib_image.delete()
        super(ImageBuildJob, self).onFailed()


class ImageUploadJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageUploadJob")

    def __init__(self, image_id, provider_name, external_name, snap_image_id,
                 db):
        self.db = db
        self.image_id = image_id
        self.snap_image_id = snap_image_id
        job_data = {
            'image-name': external_name,
            'provider': provider_name
        }
        job_name = 'image-upload:%s' % image_id
        super(ImageUploadJob, self).__init__(job_name, job_data)

    def onCompleted(self):
        job_data = json.loads(self.data[0])
        external_id = job_data['external-id']

        with self.db.getSession() as session:
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


class ImageDeleteJob(NodepoolJob):
    log = logging.getLogger("jobs.ImageDeleteJob")

    def __init__(self, image_id, db):
        self.db = db
        self.image_id = image_id
        job_name = 'image-delete:%s' % image_id
        super(ImageDeleteJob, self).__init__(job_name, '')

    def onCompleted(self):
        with self.db.getSession() as session:
            dib_image = session.getDibImage(self.image_id)

            # Remove image from the nodedb
            dib_image.state = nodedb.DELETE
            dib_image.delete()
        super(ImageDeleteJob, self).onCompleted()
