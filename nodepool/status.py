#!/usr/bin/env python
#
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
import time

from nodepool import nodedb

from prettytable import PrettyTable


def age(timestamp):
    now = time.time()
    dt = now - timestamp
    m, s = divmod(dt, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return '%02d:%02d:%02d:%02d' % (d, h, m, s)


def node_list(db, node_id=None, format='pretty'):
    """returns the nodes list, formatted as 'format'."""
    objs = []
    with db.getSession() as session:
        for node in session.getNodes():
            if node_id and node.id != node_id:
                continue
            objs.append({'id': node.id,
                         'provider': node.provider_name,
                         'AZ': node.az,
                         'label': node.label_name,
                         'target': node.target_name,
                         'manager': node.manager_name,
                         'hostname': node.hostname,
                         'nodename': node.nodename,
                         'server_id': node.external_id,
                         'ip': node.ip,
                         'state': nodedb.STATE_NAMES[node.state],
                         'age': node.state_time,
                         'comment': node.comment})
    if format == 'pretty':
        t = PrettyTable(["ID", "Provider", "AZ", "Label", "Target",
                         "Manager", "Hostname", "NodeName", "Server ID",
                         "IP", "State", "Age", "Comment"])
        t.align = 'l'
        for obj in objs:
            t.add_row([obj['id'], obj['provider'], obj['AZ'],
                       obj['label'], obj['target'],
                       obj['manager'], obj['hostname'],
                       obj['nodename'], obj['server_id'], obj['ip'],
                       obj['state'],
                       age(obj['age']), obj['comment']])
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown output format %s' % format)
    return str(t)


def dib_image_list(zk, format='pretty'):
    """returns the DIB image list, formatted as 'format'."""
    objs = []
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            build = zk.getBuild(image_name, build_no)
            objs.append({'id' : '-'.join([image_name, build_no]),
                         'image': image_name,
                         'builder': build.builder,
                         'formats': build.formats,
                         'state': build.state,
                         'age': int(build.state_time)
            })
    if format == 'pretty':
        t = PrettyTable(["ID", "Image", "Builder", "Formats",
                         "State", "Age"])
        t.align = 'l'
        for obj in objs:
            t.add_row([obj['id'], obj['image'],
                       obj['builder'], ','.join(obj['formats']),
                       obj['state'], age(obj['age'])])
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown output format %s' % format)


def image_list(zk, format='pretty'):
    """returns the image list, formatted as 'format'."""
    objs = []
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            for provider in zk.getBuildProviders(image_name, build_no):
                for upload_no in zk.getImageUploadNumbers(
                        image_name, build_no, provider):
                    upload = zk.getImageUpload(image_name, build_no,
                                               provider, upload_no)
                    objs.append({'id': build_no,
                                 'upload_id': upload_no,
                                 'provider': provider,
                                 'image': image_name,
                                 'provider_image_name': upload.external_name,
                                 'provider_image_id': upload.external_id,
                                 'state': upload.state,
                                 'age': int(upload.state_time)
                    })
    if format == 'pretty':
        t = PrettyTable(["Build ID", "Upload ID", "Provider", "Image",
                         "Provider Image Name", "Provider Image ID", "State",
                         "Age"])
        t.align = 'l'
        for obj in objs:
            t.add_row([obj['id'], obj['upload_id'], obj['provider'],
                       obj['image'],
                       obj['provider_image_name'],
                       obj['provider_image_id'],
                       obj['state'],
                       age(obj['age'])])
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown output format %s' % format)
