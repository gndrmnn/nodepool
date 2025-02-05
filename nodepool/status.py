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

from collections import OrderedDict
import json
import time

from prettytable import PrettyTable

# General notes:
#
# All the _list functions should return a tuple
#
#  ([ {obj}, {obj}, ...], headers_table)
#
# The headers_table is an OrderedDict that maps the fields in the
# returned objs to pretty-printable headers.  Each obj in the list
# should be a dictionary with fields as described by the
# headers_table.
#
# e.g.
#
#   headers_table = OrderedDict({
#         'key1': 'Key One',
#         'key2': 'Key Two'})
#   objs = [ { 'key1': 'value', 'key2': 123 },
#            { 'key1': 'value2', 'key2': 456 } ]
#   return(objs, headers_table)
#
# The output() function takes this tuple result and a format to
# produce results for consumption by the caller.


def age(timestamp):
    now = time.time()
    dt = now - timestamp
    m, s = divmod(dt, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return '%02d:%02d:%02d:%02d' % (d, h, m, s)


def _to_pretty_table(objs, headers_table, fields):
    '''Construct a pretty table output

    :param objs: list of output objects
    :param headers_table: list of (key, desr) header tuples
    :param fields: list of fields to show; None means all

    :return str: text output
    '''
    if fields:
        headers_table = OrderedDict(
            [h for h in headers_table.items() if h[0] in fields])
    headers = headers_table.values()
    t = PrettyTable(headers)
    t.align = 'l'
    for obj in objs:
        values = []
        for k in headers_table:
            if fields and k not in fields:
                continue
            if k == 'age' or k.endswith('_age') and obj[k] is not None:
                try:
                    obj_age = age(int(obj[k]))
                except ValueError:
                    # already converted
                    obj_age = obj[k]
                values.append(obj_age)
            else:
                if isinstance(obj[k], list):
                    values.append(','.join(obj[k]))
                elif obj[k] is None:
                    values.append('')
                else:
                    values.append(obj[k])
        t.add_row(values)
    return t


def output(results, fmt, fields=None):
    '''Generate output for webapp results

    :param results: tuple (objs, headers) as returned by various _list
                    functions
    :param fmt: select from ascii pretty-table or json
    :param fields: list of fields to show in pretty-table output
    '''
    objs, headers_table = results

    if fmt == 'pretty':
        t = _to_pretty_table(objs, headers_table, fields)
        return str(t)
    elif fmt == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown format "%s"' % fmt)


def node_list(zk, node_id=None):
    headers_table = [
        ("id", "ID"),
        ("provider", "Provider"),
        ("label", "Label"),
        ("server_id", "Server ID"),
        ("public_ipv4", "Public IPv4"),
        ("ipv6", "IPv6"),
        ("state", "State"),
        ("age", "Age"),
        ("locked", "Locked"),
        ("pool", "Pool"),
        ("hostname", "Hostname"),
        ("private_ipv4", "Private IPv4"),
        ("AZ", "AZ"),
        ("username", "Username"),
        ("connection_port", "Port"),
        ("launcher", "Launcher"),
        ("allocated_to", "Allocated To"),
        ("hold_job", "Hold Job"),
        ("comment", "Comment"),
        ("user_data", "User Data"),
        ("driver_data", "Driver Data"),
    ]
    headers_table = OrderedDict(headers_table)

    def _get_node_values(node):
        locked = "unlocked"
        if zk.enable_cache:
            if node.lock_contenders:
                locked = "locked"
        else:
            if zk.getNodeLockContenders(node):
                locked = "locked"
        port = node.connection_port
        try:
            int(port)
        except (ValueError, TypeError):
            # The port field is being used to carry connection
            # information which may contain credentials (e.g., k8s
            # service account).  Suppress it.
            port = "redacted"
        values = [
            node.id,
            node.provider,
            node.type,
            node.external_id,
            node.public_ipv4,
            node.public_ipv6,
            node.state,
            age(node.state_time),
            locked,
            node.pool,
            node.hostname,
            node.private_ipv4,
            node.az,
            node.username,
            port,
            node.launcher,
            node.allocated_to,
            node.hold_job,
            node.comment,
            node.user_data,
            node.driver_data,
        ]
        return values

    objs = []
    if node_id:
        node = zk.getNode(node_id)
        if node:
            values = _get_node_values(node)
            objs.append(dict(zip(headers_table.keys(),
                                 values)))
    else:
        cached_ids = zk.enable_cache
        for node in zk.nodeIterator(cached_ids=cached_ids):
            values = _get_node_values(node)

            objs.append(dict(zip(headers_table.keys(),
                                 values)))

    return (objs, headers_table)


def dib_image_list(zk):
    headers_table = OrderedDict([
        ("id", "ID"),
        ("image", "Image"),
        ("builder", "Builder"),
        ("formats", "Formats"),
        ("state", "State"),
        ("age", "Age")])
    objs = []
    builds = []
    image_paused = {}
    if zk.enable_cache:
        builds = zk.getCachedBuilds()
        for image in zk.getCachedImages():
            image_paused[image.image_name] = image.paused
    else:
        for image_name in zk.getImageNames():
            image_paused[image_name] = \
                zk.getImagePaused(image_name)
            for build_no in zk.getBuildIds(image_name):
                build = zk.getBuild(image_name, build_no)
                if build:
                    builds.append(build)
    for build in builds:
        paused = image_paused.get(build._image_name, False)
        state = paused and 'paused' or build.state
        objs.append({'id': '-'.join([build._image_name, build.id]),
                     'image': build._image_name,
                     'builder': build.builder,
                     'formats': build.formats,
                     'state': state,
                     'age': int(build.state_time)
                     })
    return (objs, headers_table)


def image_status(zk):
    headers_table = OrderedDict([
        ("image", "Image"),
        ("paused", "Paused"),
        ("build_request", "Build Request"),
        ("build_request_age", "Build Request Age")
    ])
    objs = []
    for image_name in zk.getImageNames():
        request = zk.getBuildRequest(image_name)
        paused = zk.getImagePaused(image_name)
        if request:
            age = int(request.state_time)
            req = 'pending' if request.pending else 'building'
        else:
            age = None
            req = None
        objs.append({
            "image": image_name,
            "paused": bool(paused),
            "build_request": req,
            "build_request_age": age,
        })
    return (objs, headers_table)


def image_list(zk):
    headers_table = OrderedDict([
        ("id", "Build ID"),
        ("upload_id", "Upload ID"),
        ("provider", "Provider"),
        ("image", "Image"),
        ("external_name", "Provider Image Name"),
        ("external_id", "Provider Image ID"),
        ("state", "State"),
        ("age", "Age")])
    objs = []
    uploads = []
    if zk.enable_cache:
        uploads = zk.getCachedImageUploads()
    else:
        for image_name in zk.getImageNames():
            for build_no in zk.getBuildIds(image_name):
                for provider in zk.getBuildProviders(image_name, build_no):
                    for upload_no in zk.getImageUploadNumbers(
                            image_name, build_no, provider):
                        upload = zk.getImageUpload(image_name, build_no,
                                                   provider, upload_no)
                        if upload:
                            uploads.append(upload)

    for upload in uploads:
        values = [upload.build_id,
                  upload.id,
                  upload.provider_name,
                  upload.image_name,
                  upload.external_name,
                  upload.external_id,
                  upload.state,
                  int(upload.state_time)]
        objs.append(dict(zip(headers_table.keys(),
                             values)))
    return (objs, headers_table)


def request_list(zk):
    headers_table = OrderedDict([
        ("id", "Request ID"),
        ("relative_priority", "Priority"),
        ("state", "State"),
        ("requestor", "Requestor"),
        ("node_types", "Node Types"),
        ("nodes", "Nodes"),
        ("declined_by", "Declined By"),
        ("event_id", "Event ID"),
    ])
    objs = []
    cached_ids = zk.enable_cache
    for req in zk.nodeRequestIterator(cached_ids=cached_ids):
        values = [req.id, req.relative_priority,
                  req.state, req.requestor,
                  req.node_types,
                  req.nodes,
                  req.declined_by,
                  req.event_id]
        objs.append(dict(zip(headers_table.keys(),
                             values)))
    return (objs, headers_table)


def label_list(zk):
    headers_table = OrderedDict([
        ("label", "Label"),
    ])
    objs = []

    # Walk all launchers, find the labels they support and stick it
    # all in a set.
    # NOTE(ianw): maybe add to each entry a list of which
    #             launchers support the label?
    labels = set()
    launcher_pools = zk.getRegisteredPools()
    for launcher_pool in launcher_pools:
        labels.update(set(launcher_pool.supported_labels))

    for label in labels:
        objs.append({'label': label})

    return (objs, headers_table)
