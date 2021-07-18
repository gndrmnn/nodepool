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

import errno
import time
import socket
import logging

import paramiko

from nodepool import exceptions

log = logging.getLogger("nodepool.utils")

# How long to sleep while waiting for something in a loop
ITERATE_INTERVAL = 2


def iterate_timeout(max_seconds, exc, purpose, interval=ITERATE_INTERVAL):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(interval)
    raise exc("Timeout waiting for %s" % purpose)


def set_node_ip(node):
    '''
    Set the node public_ip
    '''
    if 'fake' in node.hostname:
        return
    addrinfo = socket.getaddrinfo(node.hostname, node.connection_port)[0]
    if addrinfo[0] == socket.AF_INET:
        node.public_ipv4 = addrinfo[4][0]
    elif addrinfo[0] == socket.AF_INET6:
        node.public_ipv6 = addrinfo[4][0]
    else:
        raise exceptions.LaunchNetworkException(
            "Unable to find public IP of server")


def nodescan(ip, port=22, timeout=60, gather_hostkeys=True):
    '''
    Scan the IP address for public SSH keys.

    Keys are returned formatted as: "<type> <base64_string>"
    '''
    if 'fake' in ip:
        if gather_hostkeys:
            return ['ssh-rsa FAKEKEY']
        else:
            return []

    addrinfo = socket.getaddrinfo(ip, port)[0]
    family = addrinfo[0]
    sockaddr = addrinfo[4]

    keys = []
    key = None
    # First we wait for the sshd to be listening
    for count in iterate_timeout(
            timeout, exceptions.ConnectionTimeoutException,
            "connection to %s on port %s" % (ip, port)):
        sock = None
        t = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(sockaddr)
            # NOTE(pabelanger): Try to connect to SSH first, before breaking
            # our loop. This is to ensure the SSHd on the remote node is
            # properly running before we scan keys below.
            if gather_hostkeys:
                t = paramiko.transport.Transport(sock)
                t.start_client(timeout=timeout)
            break
        except socket.error as e:
            if e.errno not in [errno.ECONNREFUSED, errno.EHOSTUNREACH, None]:
                log.exception(
                    'Exception connecting to %s on port %s:' % (ip, port))
        except Exception:
            log.exception("ssh socket connection failure")
        finally:
            try:
                if t:
                    t.close()
            except Exception as e:
                log.exception('Exception closing paramiko: %s', e)
            try:
                if sock:
                    sock.close()
            except Exception:
                log.exception('Exception closing socket')
            sock = None
    if gather_hostkeys:
        sock = None
        t = None
        key_index = 0
        # Now we gather hostkeys
        while key_index >= 0:
            try:
                sock = socket.socket(family, socket.SOCK_STREAM)
                # Do this early so that we don't trigger exceptions
                t = paramiko.transport.Transport(sock)
                opts = paramiko.transport.SecurityOptions(t)
                # We use each supported key type in turn to ensure we get
                # back that specific host key type.
                key_types = opts.key_types
                opts.key_types = [key_types[key_index]]
                key_index += 1
                if key_index >= len(key_types):
                    key_index = -1

                sock.settimeout(10)
                sock.connect(sockaddr)
                t.start_client(timeout=timeout)
                key = t.get_remote_server_key()
                if key:
                    keys.append("%s %s" % (key.get_name(), key.get_base64()))
                    log.debug('Added ssh host key: %s', key.get_name())
            except paramiko.SSHException as e:
                msg = str(e)
                if 'no acceptable host key' not in msg:
                    # We expect some host keys to not be valid when scanning
                    # only log if the error isn't due to mismatched host key
                    # types.
                    log.exception("ssh-keyscan failure")
            except socket.error:
                log.exception(
                    'Exception connecting to %s on port %s:' % (ip, port))
            except Exception:
                log.exception("ssh-keyscan failure")
            finally:
                try:
                    if t:
                        t.close()
                except Exception:
                    log.exception('Exception closing paramiko')
                t = None
                try:
                    if sock:
                        sock.close()
                except Exception:
                    log.exception('Exception closing socket')
                sock = None

    return keys
