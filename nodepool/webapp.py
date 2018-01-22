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

    def _request_wants(self, request):
        '''Find request content-type

        :param request: The incoming request
        :return str: Best guess of either 'pretty' or 'json'
        '''
        best = request.accept.best_match(
            ['application/json', 'text/plain'])
        if best == 'application/json':
            return 'json'
        else:
            return 'pretty'

    def _app(self, request, request_type):
        raise NotImplementedError('Override me')

    def app(self, request):
        request_type = self._request_wants(request)
        try:
            return self._app(request, request_type)
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

    def get_cache(self, path, params, request_type):
        # TODO quick and dirty way to take query parameters
        # into account when caching data
        if params:
            index = "%s.%s.%s" % (path,
                                  json.dumps(params.dict_of_lists(),
                                             sort_keys=True),
                                  request_type)
        else:
            index = "%s.%s" % (path, request_type)
        result = self.cache.get(index)
        if result:
            return result

        zk = self.nodepool.getZK()

        if path == '/image-list':
            results = status.image_list(zk)
        elif path == '/dib-image-list':
            results = status.dib_image_list(zk)
        elif path == '/node-list':
            results = status.node_list(zk,
                                       node_id=params.get('node_id'))
        elif path == '/request-list':
            results = status.request_list(zk)
        elif path == '/label-list':
            results = status.label_list(zk)
        elif path == '/alien-image-list':
            output = status.alien_image_list(zk,
                                             self.nodepool,
                                             provider=params.get('provider'))
        elif path == '/info/builds':
            output = status.info(zk,
                                 provider=params.get('provider'))
        elif path == '/info/nodes':
            output = status.info(zk,
                                 provider=params.get('provider'))
        else:
            return None

        fields = None
        if params.get('fields'):
            fields = params.get('fields').split(',')

        output = status.output(results, request_type, fields)
        return self.cache.put(index, output)

    def _app(self, request, request_type):
        result = self.get_cache(request.path, request.params,
                                request_type)
        if result is None:
            raise webob.exc.HTTPNotFound()
        last_modified, output = result

        if request_type == 'json':
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

    def node_cmd(self, request, node_id, request_type):
        zk = self.nodepool.getZK()
        if request.method == 'DELETE':
            try:
                nd.delete(zk,
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
                "Allowed methods are: GET, DELETE")
        output = status.node_list(zk, node_id=node_id)
        return status.output(output, request_type), status_code

    def dib_image_cmd(self, request, image, request_type):
        zk = self.nodepool.getZK()
        if request.method == 'POST':
            try:
                img.image_build(zk,
                                self.nodepool,
                                image)
                status_code = 201
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'DELETE':
            (image_arg, build_num) = image.rsplit('-', 1)
            try:
                img.dib_image_delete(zk,
                                     image_arg, build_num)
                status_code = 200
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'GET':
            status_code = 200
        else:
            raise webob.exc.HTTPMethodNotAllowed(
                "Allowed methods are: GET, POST, DELETE")
        output = status.dib_image_list(zk)
        return status.output(output, request_type), status_code

    def image_cmd(self, request, provider, image,
                  build_id, upload_id, request_type):
        zk = self.nodepool.getZK()
        if request.method == 'DELETE':
            try:
                img.image_delete(zk, provider,
                                 image, build_id, upload_id)
                status_code = 200
            except Exception as e:
                raise webob.exc.HTTPBadRequest(e.message)
        elif request.method == 'GET':
            status_code = 200
        else:
            raise webob.exc.HTTPMethodNotAllowed(
                "Allowed methods are: GET, DELETE")
        output = status.image_list(zk)
        return status.output(output, request_type), status_code

    def _app(self, request, request_type):
        def _response(output, status_code, request_type):
            if request_type == 'json':
                content_type = 'application/json'
            else:
                content_type = 'text/plain'
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
            output, status_code = self.node_cmd(request, node_id, request_type)
            return _response(output, status_code)
        # dib images
        dib_regex = re.compile('^/dib-image/(?P<image>[a-zA-Z0-9_-]+)')
        matched = dib_regex.match(request.path)
        if matched:
            image = matched.groupdict()['image']
            output, status_code = self.dib_image_cmd(request, image,
                                                     request_type)
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
                                                 opts['upload_id'],
                                                 request_type)
            return _response(output, status_code)

        raise webob.exc.HTTPNotFound()
