#!/usr/bin/env python
# Copyright 2015 Hewlett-Packard Development Company, L.P.
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
import logging
import os
import subprocess
import threading
import time
import traceback
import uuid

import gear
import shlex
from stats import statsd

import config as nodepool_config
import provider_manager

MINS = 60
HOURS = 60 * MINS
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save

# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"


class DibImageFile(object):
    def __init__(self, image_id, name, extension=None):
        self.image_id = image_id
        self.name = name
        self.extension = extension

    @staticmethod
    def from_path(path):
        image_file = os.path.basename(path)
        image_file, extension = image_file.rsplit('.', 1)
        image_id, name = image_file.split(':', 1)
        return DibImageFile(image_id, name, extension)

    @staticmethod
    def from_image_id(images_dir, image_id):
        images = []
        for image_filename in os.listdir(images_dir):
            image = DibImageFile.from_path(image_filename)
            if image.image_id == image_id:
                images.append(image)
        return images

    @staticmethod
    def from_images_dir(images_dir):
        return [DibImageFile.from_path(x) for x in os.listdir(images_dir)]

    def to_path(self, images_dir, with_extension=True):
        my_path = os.path.join(images_dir, self.image_id + ':' + self.name)
        if with_extension:
            if self.extension is None:
                raise ValueError('Cannot specify image extension of None')
            my_path += '.' + self.extension
        return my_path


class NodePoolBuilder(object):
    log = logging.getLogger("Nodepool Builder")

    def __init__(self, config_path):
        self._config_path = config_path
        self._running = False
        self._built_image_ids = set()
        self._start_lock = threading.Lock()
        self._gearman_worker_lock = threading.Lock()
        self._config = None

    @property
    def running(self):
        return self._running

    def start(self):
        self.load_config(self._config_path)

        with self._start_lock:
            if self._running:
                raise RuntimeError('Cannot start, already running.')
            self._running = True

        self.gearman_worker = self._initialize_gearman_worker(
            self._config.gearman_servers.values())
        self._register_gearman_functions(self.gearman_worker,
                                         self._config.diskimages.values())
        self._register_existing_image_uploads(self.gearman_worker)
        self.thread = threading.Thread(target=self._run,
                                       name='NodePool Builder')
        self.thread.start()

    def stop(self):
        with self._start_lock:
            self.log.debug('Stopping')
            if not self._running:
                self.log.warning("Stop called when we are already stopped.")
            self._running = False

            try:
                self.gearman_worker.stopWaitingForJobs()
            except OSError as e:
                if e.errno == 9:
                    # The connection has been lost already
                    self.log.debug("Gearman connection lost when attempting to"
                                   " shutdown. Ignoring.")
                else:
                    raise

            self.log.debug('Waiting for builder thread to complete.')
            self.thread.join()
            self.log.debug('Builder thread completed.')

            try:
                self.gearman_worker.shutdown()
            except OSError as e:
                if e.errno == 9:
                    # The connection has been lost already
                    self.log.debug("Gearman connection lost when "
                                   "attempting to shutdown. Ignoring.")
                else:
                    raise

    def load_config(self, config_path):
        config = nodepool_config.loadConfig(None, config_path)
        provider_manager.ProviderManager.reconfigure(
            self._config, config)
        self._config = config

    def _run(self):
        self.log.debug('Starting listener for build jobs')
        while self._running:
            try:
                job = self.gearman_worker.getJob()
                with self._gearman_worker_lock:
                    self._handle_job(job, self.gearman_worker)
            except gear.InterruptedError:
                pass
            except Exception:
                self.log.exception('Exception while getting job')

    def _initialize_gearman_worker(self, servers):
        worker = gear.Worker('Nodepool Builder')
        for server in servers:
            worker.addServer(server.host, server.port)

        self.log.debug('Waiting for gearman server')
        worker.waitForServer()
        return worker

    def _register_gearman_functions(self, worker, images):
        self.log.debug('Registering gearman functions')
        for image in images:
            worker.registerFunction('image-build:%s' % image.name)

    def _register_existing_image_uploads(self, worker):
        images = DibImageFile.from_images_dir(self._config.imagesdir)
        for image in images:
            self._register_image_id(image.image_id)

    def _register_image_id(self, worker, image_id):
        self.log.debug('registering image %s', image_id)
        worker.registerFunction('image-upload:%s' % image_id)
        worker.registerFunction('image-delete:%s' % image_id)
        self._built_image_ids.add(image_id)

    def _unregister_image_id(self, worker, image_id):
        if image_id in self._built_image_ids:
            self.log.debug('unregistering image %s', image_id)
            worker.unRegisterFunction('image-upload:%s' % image_id)
            self._built_image_ids.remove(image_id)
        else:
            self.log.warning('Attempting to remove image %d but image not '
                             'found', image_id)

    def _can_handle_imageid_job(self, job, image_op):
        return (job.name.startswith(image_op + ':') and
                job.name.split(':')[1]) in self._built_image_ids

    def _handle_job(self, job, gearman_worker):
        try:
            self.log.debug('got job %s with data %s',
                           job.name, job.arguments)
            if job.name.startswith('image-build:'):
                args = json.loads(job.arguments)
                image_name = job.name.split(':', 1)[1]
                image_id = self._build_image(image_name)

                if image_id is not None:
                    # We can now upload this image
                    self._register_image_id(gearman_worker, image_id)
                    job.sendWorkComplete(json.dumps({'image-id': image_id}))
                else:
                    job.sendWorkFail()
            elif self._can_handle_imageid_job(job, 'image-upload'):
                args = json.loads(job.arguments)
                image_id = job.name.split(':')[1]
                external_id = self._upload_image(image_id,
                                                 args['provider'])
                job.sendWorkComplete(json.dumps({'external-id': external_id}))
            elif self._can_handle_imageid_job(job, 'image-delete'):
                image_id = job.name.split(':')[1]
                self._delete_image(image_id)
                self._unregister_image_id(gearman_worker, image_id)
                job.sendWorkComplete()
            else:
                self.log.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.log.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())

    def _delete_image(self, image_id):
        image_files = DibImageFile.from_image_id(self._config.imagesdir,
                                                 image_id)

        # Delete a dib image and it's associated file
        for image_file in image_files:
            img_path = image_file.to_path(self._config.imagesdir)
            if os.path.exists(img_path):
                self.log.debug('Removing filename %s', img_path)
                os.remove(img_path)
            else:
                self.log.debug('No filename %s found to remove', img_path)

        self.log.info("Deleted dib image id: %s" % image_id)

    def _upload_image(self, image_id, provider_name):
        start_time = time.time()
        timestamp = int(start_time)

        provider = self._config.providers[provider_name]
        image_type = provider.image_type

        image_files = DibImageFile.from_image_id(self._config.imagesdir,
                                                 image_id)
        image_files = filter(lambda x: x.extension == image_type, image_files)
        if len(image_files) == 0:
            self.log.error("Unable to find image file for id %s to upload",
                           image_id)
            return
        if len(image_files) > 1:
            self.log.error("Found more than one image for id %s. This should "
                           "never happen.", image_id)

        image_file = image_files[0]
        image_name = image_file.name
        filename = image_file.to_path(self._config.imagesdir,
                                      with_extension=False)

        dummy_image = type('obj', (object,),
                           {'name': image_name})
        ext_image_name = provider.template_hostname.format(
            provider=provider, image=dummy_image, timestamp=str(timestamp))
        self.log.info("Uploading dib image id: %s from %s in %s" %
                      (image_id, filename, provider.name))

        manager = self._config.provider_managers[provider.name]
        provider_image = filter(lambda x: x.diskimage == image_name,
                                provider.images.values())
        if len(provider_image) != 1:
            self.log.error("Could not find matching provider image for %s",
                           image_name)
            return
        provider_image = provider_image[0]
        image_meta = provider_image.meta
        external_id = manager.uploadImage(ext_image_name, filename,
                                          image_file.extension, 'bare',
                                          image_meta)
        self.log.debug("Saving image id: %s", external_id)
        # It can take a _very_ long time for Rackspace 1.0 to save an image
        manager.waitForImage(external_id, IMAGE_TIMEOUT)

        if statsd:
            dt = int((time.time() - start_time) * 1000)
            key = 'nodepool.image_update.%s.%s' % (image_name,
                                                   provider.name)
            statsd.timing(key, dt)
            statsd.incr(key)

        self.log.info("Image %s in %s is ready" % (image_id,
                                                   provider.name))
        return external_id

    def _run_dib_for_image(self, image, filename):
        env = os.environ.copy()

        env['DIB_RELEASE'] = image.release
        env['DIB_IMAGE_NAME'] = image.name
        env['DIB_IMAGE_FILENAME'] = filename
        # Note we use a reference to the nodepool config here so
        # that whenever the config is updated we get up to date
        # values in this thread.
        env['ELEMENTS_PATH'] = self._config.elementsdir
        env['NODEPOOL_SCRIPTDIR'] = self._config.scriptdir

        # send additional env vars if needed
        for k, v in image.env_vars.items():
            env[k] = v

        img_elements = image.elements
        img_types = ",".join(image.image_types)

        qemu_img_options = ''
        if 'qcow2' in img_types:
            qemu_img_options = DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS

        if 'fake-' in image.name:
            dib_cmd = 'nodepool/tests/fake-image-create'
        else:
            dib_cmd = 'disk-image-create'

        cmd = ('%s -x -t %s --no-tmpfs %s -o %s %s' %
               (dib_cmd, img_types, qemu_img_options, filename, img_elements))

        log = logging.getLogger("nodepool.image.build.%s" %
                                (image.name,))

        self.log.info('Running %s' % cmd)

        try:
            p = subprocess.Popen(
                shlex.split(cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env)
        except OSError as e:
            raise Exception("Failed to exec '%s'. Error: '%s'" %
                            (cmd, e.strerror))

        while True:
            ln = p.stdout.readline()
            log.info(ln.strip())
            if not ln:
                break

        p.wait()
        ret = p.returncode
        if ret:
            raise Exception("DIB failed creating %s" % (filename,))

    def _get_diskimage_by_name(self, name):
        for image in self._config.diskimages.values():
            if image.name == name:
                return image
        return None

    def _build_image(self, image_name):
        diskimage = self._get_diskimage_by_name(image_name)
        if diskimage is None:
            self.log.error('Could not find matching image in config for %s',
                           image_name)
            return

        start_time = time.time()
        image_id = str(uuid.uuid4())
        image_file = DibImageFile(image_id, image_name)
        filename = image_file.to_path(self._config.imagesdir, False)

        self.log.info("Creating image: %s with filename %s" %
                      (diskimage.name, filename))
        try:
            self._run_dib_for_image(diskimage, filename)
        except Exception:
            self.log.exception("Exception building DIB image %s:" %
                               (diskimage.name,))
            return None
        else:
            self.log.info("DIB image %s with file %s is built" % (
                image_name, filename))

            if statsd:
                dt = int((time.time() - start_time) * 1000)
                key = 'nodepool.dib_image_build.%s' % diskimage.name
                statsd.timing(key, dt)
                statsd.incr(key)
            return image_id
