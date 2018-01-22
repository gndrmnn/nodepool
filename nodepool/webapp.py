# Copyright 2012 Hewlett-Packard Development Company, L.P.
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
import logging
import re
import threading
import time
from paste import httpserver
import webob
from webob import dec

from nodepool import status
from nodepool import node as nd
from nodepool import image as img

"""Nodepool main web app.

Nodepool supports HTTP requests directly against it for determining
status. These responses are provided as preformatted text for now, but
should be augmented or replaced with JSON data structures.
"""


class Cache(object):
    def __init__(self, expiry=1):
        self.cache = {}
        self.expiry = expiry

    def get(self, key):
        now = time.time()
        if key in self.cache:
            lm, value = self.cache[key]
            if now > lm + self.expiry:
                del self.cache[key]
                return None
            return (lm, value)

    def put(self, key, value):
        now = time.time()
        res = (now, value)
        self.cache[key] = res
        return res


class BaseWebApp(threading.Thread):
    log = logging.getLogger("nodepool.BaseWebApp")

    def __init__(self, nodepool, port, listen_address, cache_expiry):
        threading.Thread.__init__(self)
        self.nodepool = nodepool
        self.port = port
        self.listen_address = listen_address
        self.cache = Cache(cache_expiry)
        self.cache_expiry = cache_expiry
        self.daemon = True
        self.server = httpserver.serve(dec.wsgify(self.app),
                                       host=self.listen_address,
                                       port=self.port, start_loop=False)

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.server_close()

    def _app(self, request):
        raise NotImplementedError('Override me')

    def app(self, request):
        try:
            return self._app(request)
        except Exception as e:
            self.log.error(e.message)
            raise


class WebApp(BaseWebApp):
    log = logging.getLogger("nodepool.WebApp")

    def __init__(self, nodepool, port=8005, listen_address='0.0.0.0',
                 cache_expiry=1):
        super(WebApp, self).__init__(nodepool,
                                     port, listen_address,
                                     cache_expiry)

    def get_cache(self, path, params):
        # TODO quick and dirty way to take query parameters
        # into account when caching data
        if params:
            index = path + json.dumps(params.dict_of_lists(), sort_keys=True)
        else:
            index = path
        result = self.cache.get(index)
        if result:
            return result
        if path == '/image-list':
            output = status.image_list(self.nodepool.getZK(),
                                       format='pretty')
        elif path == '/image-list.json':
            output = status.image_list(self.nodepool.getZK(),
                                       format='json')
        elif path == '/dib-image-list':
            output = status.dib_image_list(self.nodepool.getZK(),
                                           format='pretty')
        elif path == '/dib-image-list.json':
            output = status.dib_image_list(self.nodepool.getZK(),
                                           format='json')
        elif path == '/node-list':
            output = status.node_list(self.nodepool.getZK(),
                                      format='pretty',
                                      node_id=params.get('node_id'))
        elif path == '/node-list.json':
            output = status.node_list(self.nodepool.getZK(),
                                      format='json',
                                      node_id=params.get('node_id'))
        elif path == '/label-list':
            output = status.label_list(self.nodepool.getZK(),
                                       format='pretty')
        elif path == '/label-list.json':
            output = status.label_list(self.nodepool.getZK(),
                                       format='json')
        elif path == '/request-list':
            output = status.request_list(self.nodepool.getZK(),
                                         format='pretty')
        elif path == '/request-list.json':
            output = status.request_list(self.nodepool.getZK(),
                                         format='json')
        elif path == '/alien-image-list':
            output = status.alien_image_list(self.nodepool.getZK(),
                                             self.nodepool,
                                             provider=params.get('provider'),
                                             format='pretty')
        elif path == '/alien-image-list.json':
            output = status.alien_image_list(self.nodepool.getZK(),
                                             self.nodepool,
                                             provider=params.get('provider'),
                                             format='json')
        elif path == '/info':
            output = status.info(self.nodepool.getZK(),
                                 provider=params.get('provider'),
                                 format='pretty')
        elif path == '/info.json':
            output = status.info(self.nodepool.getZK(),
                                 provider=params.get('provider'),
                                 format='json')
        else:
            return None
        return self.cache.put(index, output)

    def _app(self, request):
        result = self.get_cache(request.path, request.params)
        if result is None:
            raise webob.exc.HTTPNotFound()
        last_modified, output = result

        if request.path.endswith('.json'):
            content_type = 'application/json'
        else:
            content_type = 'text/plain'

        response = webob.Response(body=output,
                                  charset='UTF-8',
                                  content_type=content_type)
        response.headers['Access-Control-Allow-Origin'] = '*'

        response.cache_control.public = True
        response.cache_control.max_age = self.cache_expiry
        response.last_modified = last_modified
        response.expires = last_modified + self.cache_expiry

        return response.conditional_response_app


class AdminWebApp(BaseWebApp):
    log = logging.getLogger("nodepool.AdminWebApp")

    def __init__(self, nodepool, port=8055, listen_address='127.0.0.1',
                 cache_expiry=1):
        super(AdminWebApp, self).__init__(nodepool,
                                          port, listen_address,
                                          cache_expiry)

    def node_cmd(self, request, node_id):
        if request.method == 'PUT':
            try:
                nd.hold(self.nodepool.getZK(),
                        node_id,
                        reason=request.params.get('reason'))
                status_code = 202
            except ValueError:
                raise webob.exc.HTTPNotFound(
                    'Node id %s not found' % node_id)
        elif request.method == 'DELETE':
            try:
                nd.delete(self.nodepool.getZK(),
                          self.nodepool,
                          node_id,
                          now=False)
                status_code = 202
            except ValueError:
                raise webob.exc.HTTPNotFound(
                    'Node id %s not found' % node_id)
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'GET':
            status_code = 200
        else:
            raise webob.exc.HTTPMethodNotAllowed(
                "Allowed methods are: GET, PUT, DELETE")
        output = status.node_list(self.nodepool.getZK(),
                                  format='json',
                                  node_id=node_id)
        return output, status_code

    def dib_image_cmd(self, request, image):
        if request.method == 'POST':
            try:
                img.image_build(self.nodepool.getZK(),
                                self.nodepool,
                                image)
                status_code = 201
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'DELETE':
            (image_arg, build_num) = image.rsplit('-', 1)
            try:
                img.dib_image_delete(self.nodepool.getZK(),
                                     image_arg, build_num)
                status_code = 200
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'GET':
            status_code = 200
        else:
            raise webob.exc.HTTPMethodNotAllowed(
                "Allowed methods are: GET, POST, DELETE")
        output = status.dib_image_list(self.nodepool.getZK(), format='json')
        return output, status_code

    def image_cmd(self, request, provider, image, build_id, upload_id):
        if request.method == 'DELETE':
            try:
                img.image_delete(self.nodepool.getZK(), provider,
                                 image, build_id, upload_id)
                status_code = 200
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'GET':
            status_code = 200
        else:
            raise webob.exc.HTTPMethodNotAllowed(
                "Allowed methods are: GET, DELETE")
        output = status.image_list(self.nodepool.getZK(), format='json')
        return output, status_code

    def _app(self, request):
        def _response(output, status_code):
            content_type = 'application/json'
            response = webob.Response(body=output,
                                      charset='UTF-8',
                                      content_type=content_type,
                                      status=status_code)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        # nodes
        node_regex = re.compile('^/node/(?P<node_id>[a-zA-Z0-9]+)')
        matched = node_regex.match(request.path)
        if matched:
            node_id = matched.groupdict()['node_id']
            output, status_code = self.node_cmd(request, node_id)
            return _response(output, status_code)
        # dib images
        dib_regex = re.compile('^/dib-image/(?P<image>[a-zA-Z0-9_-]+)')
        matched = dib_regex.match(request.path)
        if matched:
            image = matched.groupdict()['image']
            output, status_code = self.dib_image_cmd(request, image)
            return _response(output, status_code)
        # images
        image_regex = re.compile(
            '^/image/(?P<provider>[a-zA-Z0-9_-]+)/'
            '(?P<image>[a-zA-Z0-9_-]+)/'
            '(?P<build_id>[a-zA-Z0-9_-]+)/'
            '(?P<upload_id>[a-zA-Z0-9_-]+)/')
        matched = image_regex.match(request.path)
        if matched:
            opts = matched.groupdict()
            output, status_code = self.image_cmd(request,
                                                 opts['provider'],
                                                 opts['image'],
                                                 opts['build_id'],
                                                 opts['upload_id'])
            return _response(output, status_code)

        raise webob.exc.HTTPNotFound()
