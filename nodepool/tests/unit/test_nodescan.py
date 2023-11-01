# Copyright (C) 2023 Acme Gating, LLC
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
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

from nodepool import tests
from nodepool.nodeutils import iterate_timeout
from nodepool.zk.zookeeper import Node
from nodepool.driver.statemachine import NodescanWorker, NodescanRequest
from unittest.mock import patch

import testtools


class FakeSocket:
    def __init__(self):
        self.blocking = True
        self.fd = 1

    def setblocking(self, b):
        self.blocking = b

    def getsockopt(self, level, optname):
        return None

    def connect(self, addr):
        if not self.blocking:
            raise BlockingIOError()
        raise Exception("blocking connect attempted")

    def fileno(self):
        return self.fd


class FakePoll:
    def __init__(self, _fail=False):
        self.fds = []
        self._fail = _fail

    def register(self, fd, bitmap):
        self.fds.append(fd)

    def unregister(self, fd):
        if fd in self.fds:
            self.fds.remove(fd)

    def poll(self, timeout=None):
        if self._fail:
            return []
        fds = self.fds[:]
        self.fds = [f for f in fds if not isinstance(f, FakeSocket)]
        fds = [f.fileno() if hasattr(f, 'fileno') else f for f in fds]
        return [(f, 0) for f in fds]


class Dummy:
    pass


class FakeKey:
    def get_name(self):
        return 'fake key'

    def get_base64(self):
        return 'fake base64'


class FakeTransport:
    def __init__(self, _fail=False):
        self.active = True
        self._fail = _fail

    def start_client(self, event=None, timeout=None):
        if not self._fail:
            event.set()

    def get_security_options(self):
        ret = Dummy()
        ret.key_types = ['rsa']
        return ret

    def get_remote_server_key(self):
        return FakeKey()


class TestNodescanWorker(tests.BaseTestCase):

    @patch('paramiko.transport.Transport')
    @patch('socket.socket')
    @patch('select.epoll')
    def test_nodescan(self, mock_epoll, mock_socket, mock_transport):
        # Test the nodescan worker
        fake_socket = FakeSocket()
        mock_socket.return_value = fake_socket
        mock_epoll.return_value = FakePoll()
        mock_transport.return_value = FakeTransport()
        worker = NodescanWorker()
        node = Node()
        node.id = '1'
        node.interface_ip = '198.51.100.1'
        node.connection_port = 22
        node.connection_type = 'ssh'
        worker.start()
        log = logging.getLogger('nodepool.test')
        request = NodescanRequest(node, True, 300, log)
        worker.addRequest(request)
        for _ in iterate_timeout(30, Exception, 'nodescan'):
            if request.complete:
                break
        result = request.result()
        self.assertEqual(result, ['fake key fake base64'])
        worker.stop()
        worker.join()

    @patch('paramiko.transport.Transport')
    @patch('socket.socket')
    @patch('select.epoll')
    def test_nodescan_connection_timeout(
            self, mock_epoll, mock_socket, mock_transport):
        # Test a timeout during socket connection
        fake_socket = FakeSocket()
        mock_socket.return_value = fake_socket
        mock_epoll.return_value = FakePoll(_fail=True)
        mock_transport.return_value = FakeTransport()
        worker = NodescanWorker()
        node = Node()
        node.id = '1'
        node.interface_ip = '198.51.100.1'
        node.connection_port = 22
        node.connection_type = 'ssh'
        worker.start()
        log = logging.getLogger('nodepool.test')
        request = NodescanRequest(node, True, 1, log)
        worker.addRequest(request)
        for _ in iterate_timeout(30, Exception, 'nodescan'):
            if request.complete:
                break
        with testtools.ExpectedException(Exception):
            request.result()
        worker.stop()
        worker.join()

    @patch('paramiko.transport.Transport')
    @patch('socket.socket')
    @patch('select.epoll')
    def test_nodescan_ssh_timeout(
            self, mock_epoll, mock_socket, mock_transport):
        # Test a timeout during ssh connection
        fake_socket = FakeSocket()
        mock_socket.return_value = fake_socket
        mock_epoll.return_value = FakePoll()
        mock_transport.return_value = FakeTransport(_fail=True)
        worker = NodescanWorker()
        node = Node()
        node.id = '1'
        node.interface_ip = '198.51.100.1'
        node.connection_port = 22
        node.connection_type = 'ssh'
        worker.start()
        log = logging.getLogger('nodepool.test')
        request = NodescanRequest(node, True, 1, log)
        worker.addRequest(request)
        for _ in iterate_timeout(30, Exception, 'nodescan'):
            if request.complete:
                break
        with testtools.ExpectedException(Exception):
            request.result()
        worker.stop()
        worker.join()

    @patch('paramiko.transport.Transport')
    @patch('socket.socket')
    @patch('select.epoll')
    def test_nodescan_queue(self, mock_epoll, mock_socket, mock_transport):
        # Test the max_requests queing function
        fake_socket1 = FakeSocket()
        fake_socket2 = FakeSocket()
        fake_socket2.fd = 2
        # We get two sockets for each host
        sockets = [fake_socket1, fake_socket1, fake_socket2, fake_socket2]

        def getsocket(*args, **kw):
            return sockets.pop(0)

        mock_socket.side_effect = getsocket
        mock_epoll.return_value = FakePoll()
        mock_transport.return_value = FakeTransport()
        worker = NodescanWorker()
        worker.MAX_REQUESTS = 1
        node1 = Node()
        node1.id = '1'
        node1.interface_ip = '198.51.100.1'
        node1.connection_port = 22
        node1.connection_type = 'ssh'
        node2 = Node()
        node2.id = '2'
        node2.interface_ip = '198.51.100.2'
        node2.connection_port = 22
        node2.connection_type = 'ssh'

        log = logging.getLogger('nodepool.test')
        request1 = NodescanRequest(node1, True, 300, log)
        request2 = NodescanRequest(node2, True, 300, log)
        worker.addRequest(request1)
        worker.addRequest(request2)
        worker.start()
        for _ in iterate_timeout(5, Exception, 'nodescan'):
            if request1.complete and request2.complete:
                break
        result1 = request1.result()
        result2 = request2.result()
        self.assertEqual(result1, ['fake key fake base64'])
        self.assertEqual(result2, ['fake key fake base64'])
        worker.stop()
        worker.join()
