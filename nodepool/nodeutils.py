#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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
#
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import time
import logging

import paramiko


log = logging.getLogger("nodepool.utils")


ITERATE_INTERVAL = 2  # How long to sleep while waiting for something
                      # in a loop


def iterate_timeout(max_seconds, exc, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(ITERATE_INTERVAL)
    raise exc("Timeout waiting for %s" % purpose)


def keyscan(ip):
    '''
    Scan the IP address for public SSH keys.

    Keys are returned formatted as: "<type> <base64_string>"
    '''
    if 'fake' in ip:
        return ['ssh-rsa FAKEKEY']

    keys = []

    key = None
    try:
        t = paramiko.transport.Transport('%s:%s' % (ip, "22"))
        t.start_client()
        key = t.get_remote_server_key()
        t.close()
    except Exception as e:
        log.exception("ssh-keyscan failure: %s", e)

    # Paramiko, at this time, seems to return only the ssh-rsa key, so
    # only the single key is placed into the list.
    if key:
        keys.append(
            "%s %s" % (key.get_name(),
                       base64.encodestring(str(key)).replace('\n', ''))
        )

    return keys
