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

from collections import namedtuple
import json
import logging
import os
import subprocess
import threading
import time
import traceback
import uuid
import yaml

import gear
import shlex
from stats import statsd

MINS = 60
HOURS = 60 * MINS
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save

# HP Cloud requires qemu compat with 0.10. That version works elsewhere,
# so just hardcode it for all qcow2 building
DEFAULT_QEMU_IMAGE_COMPAT_OPTIONS = "--qemu-img-options 'compat=0.10'"

DiskImage = namedtuple('DiskImage',
                       ['name', 'elements', 'release', 'env_vars',
                        'image_types'])


class NodePoolBuilder(object):
    log = logging.getLogger("Nodepool Builder")

    def __init__(self, config_path, nodepool):
        self.config_path = config_path
        self.nodepool = nodepool
        self._running = False
        self._built_image_ids = set()
        self.gearman_worker_lock = threading.Lock()
        self.diskimages = None

    def start(self):
        self.images_dir, self.diskimages = self.parse_config(self.config_path)

        with threading.Lock():
            if self._running:
                raise RuntimeError('Cannot start, already running.')
            self._running = True

        self.gearman_worker = self._initialize_gearman_worker(
            self.nodepool.config.gearman_servers.values())
        self._register_gearman_functions(self.gearman_worker, self.diskimages)
        self.thread = threading.Thread(target=self._run,
                                       name='NodePool Builder')
        self.thread.start()

    def stop(self):
        self.log.debug('Stopping')
        self._running = False
        self.gearman_worker.stopWaitingForJobs()
        self.thread.join()
        with self.gearman_worker_lock:
            self.gearman_worker.shutdown()
            self.log.debug('Waiting for builder thread to complete.')
        self.log.debug('Builder thread completed.')

    def parse_config(self, config_path):
        config = yaml.safe_load(open(config_path))

        images_dir = config['images-dir']

        diskimages = []

        for diskimage in config.get('diskimages', ()):
            name = diskimage['name']
            elements = u' '.join(diskimage.get('elements', ()))

            # must be a string, as it's passed as env-var to
            # d-i-b, but might be untyped in the yaml and
            # interpreted as a number (e.g. "21" for fedora)
            release = str(diskimage.get('release', ''))
            env_vars = diskimage.get('env-vars', {})
            if not isinstance(env_vars, dict):
                self.log.error("%s: ignoring env-vars; "
                               "should be a dict" % diskimage.name)
                env_vars = {}

            # determine output formats needed for this image
            image_types = set()
            for provider in config.get('providers', []):
                for image in provider.get('images', []):
                    if ('diskimage' in image and
                        image.get('diskimage') == name):
                        image_types.add(provider.get('image-type', 'qcow2'))

            diskimage = DiskImage(name=name, elements=elements,
                                  release=release, env_vars=env_vars,
                                  image_types=image_types)
            diskimages.append(diskimage)

        return images_dir, diskimages

    def filename_for_image(self, image_name, image_id):
        image_dir = os.path.join(self.images_dir, image_id)
        if not os.path.isdir(image_dir):
            os.mkdir(image_dir)
        return os.path.join(image_dir, image_name)

    def name_for_image_id(self, image_id):
        image_dir = os.path.join(self.images_dir, image_id)
        if os.path.isdir(image_dir):
            image_files = os.listdir(image_dir)
            return image_files[0].rsplit('.', 1)[0]
        return None

    def _run(self):
        self.log.debug('Starting listener for build jobs')
        while self._running:
            try:
                job = self.gearman_worker.getJob()
                with self.gearman_worker_lock:
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
                self._unregister_image_id(gearman_worker, image_id)
                self._delete_image(image_id)
                job.sendWorkComplete()
            else:
                self.log.error('Unable to handle job %s', job.name)
                job.sendWorkFail()
        except Exception:
            self.log.exception('Exception while running job')
            job.sendWorkException(traceback.format_exc())

    def _delete_image(self, image_id):
        image_name = self.name_for_image_id(image_id)
        filename = self.filename_for_image(image_name, image_id)
        image = self._get_diskimage_by_name(image_name)

        # Delete a dib image and it's associated file
        for img_type in image.image_types:
            img_filename = filename + '.' + img_type
            if os.path.exists(img_filename):
                self.log.debug('Removing filename %s', img_filename)
                os.remove(img_filename)
            else:
                self.log.debug('No filename %s found to remove', img_filename)

        self.log.info("Deleted dib image id: %s" % image_id)

    def _upload_image(self, image_id, provider_name):
        start_time = time.time()
        timestamp = int(start_time)

        provider = self.nodepool.config.providers[provider_name]

        image_name = self.name_for_image_id(image_id)
        filename = self.filename_for_image(image_name, image_id)

        dummy_image = type('obj', (object,),
                           {'name': image_name})
        ext_image_name = provider.template_hostname.format(
            provider=provider, image=dummy_image, timestamp=str(timestamp))
        self.log.info("Uploading dib image id: %s from %s in %s" %
                      (image_id, filename, provider.name))

        manager = self.nodepool.getProviderManager(provider)
        image_meta = provider.images[image_name].meta
        external_id = manager.uploadImage(ext_image_name, filename,
                                          provider.image_type, 'bare',
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
        env['ELEMENTS_PATH'] = self.nodepool.config.elementsdir
        env['NODEPOOL_SCRIPTDIR'] = self.nodepool.config.scriptdir

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
        for image in self.diskimages:
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
        filename = self.filename_for_image(image_name, image_id)

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
