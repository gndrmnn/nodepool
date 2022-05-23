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
import logging
import time
from abc import ABCMeta
from threading import Thread

from kazoo.client import KazooClient
from kazoo.handlers.threading import KazooTimeoutError
from kazoo.protocol.states import KazooState

from nodepool.zk.exceptions import NoClientException
from nodepool.zk.handler import PoolSequentialThreadingHandler


class ZooKeeperClient(object):
    log = logging.getLogger("nodepool.zk.ZooKeeperClient")

    # Log zookeeper retry every 10 seconds
    retry_log_rate = 10

    def __init__(
        self,
        hosts,
        read_only=False,
        timeout=10.0,
        tls_cert=None,
        tls_key=None,
        tls_ca=None,
    ):
        """
        Initialize the ZooKeeper base client object.

        :param str hosts: Comma-separated list of hosts to connect to (e.g.
            127.0.0.1:2181,127.0.0.1:2182,[::1]:2183).
        :param bool read_only: If True, establishes a read-only connection.
        :param float timeout: The ZooKeeper session timeout, in
            seconds (default: 10.0).
        :param str tls_key: Path to TLS key
        :param str tls_cert: Path to TLS cert
        :param str tls_ca: Path to TLS CA cert
        """
        self.hosts = hosts
        self.read_only = read_only
        self.timeout = timeout
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.tls_ca = tls_ca
        self.was_lost = False

        self.client = None

        if not (tls_key and tls_cert and tls_ca):
            raise Exception("A TLS ZooKeeper connection is required; "
                            "please supply the zookeeper-tls "
                            "config values.")

        # Verify that we can read the cert files (Kazoo doesn't
        # provide useful error messages).
        for fn in (tls_cert, tls_key, tls_ca):
            if fn:
                with open(fn):
                    pass

        self._last_retry_log = 0
        self.on_connect_listeners = []
        self.on_disconnect_listeners = []
        self.on_connection_lost_listeners = []
        self.on_reconnect_listeners = []

    def _connectionListener(self, state):
        """
        Listener method for Kazoo connection state changes.

        .. warning:: This method must not block.
        """
        if state == KazooState.LOST:
            self.log.debug("ZooKeeper connection: LOST")
            self.was_lost = True
            for listener in self.on_connection_lost_listeners:
                try:
                    listener()
                except Exception:
                    self.log.exception("Exception calling listener:")
        elif state == KazooState.SUSPENDED:
            self.log.debug("ZooKeeper connection: SUSPENDED")
        else:
            self.log.debug("ZooKeeper connection: CONNECTED")
            # Create a throwaway thread since zk operations can't
            # happen in this one.
            if self.was_lost:
                self.was_lost = False
                for listener in self.on_reconnect_listeners:
                    t = Thread(target=listener)
                    t.daemon = True
                    t.start()

    @property
    def connected(self):
        return self.client and self.client.state == KazooState.CONNECTED

    @property
    def suspended(self):
        return self.client and self.client.state == KazooState.SUSPENDED

    @property
    def lost(self):
        return not self.client or self.client.state == KazooState.LOST

    def logConnectionRetryEvent(self):
        now = time.monotonic()
        if now - self._last_retry_log >= self.retry_log_rate:
            self.log.warning("Retrying zookeeper connection")
            self._last_retry_log = now

    def connect(self):
        if self.client is None:
            args = dict(
                hosts=self.hosts,
                read_only=self.read_only,
                timeout=self.timeout,
                handler=PoolSequentialThreadingHandler(),
            )
            if self.tls_key:
                args['use_ssl'] = True
                args['keyfile'] = self.tls_key
                args['certfile'] = self.tls_cert
                args['ca'] = self.tls_ca
            self.client = KazooClient(**args)
            self.client.add_listener(self._connectionListener)
            # Manually retry initial connection attempt
            while True:
                try:
                    self.client.start(1)
                    break
                except KazooTimeoutError:
                    self.logConnectionRetryEvent()

            for listener in self.on_connect_listeners:
                listener()

    def disconnect(self):
        """
        Close the ZooKeeper cluster connection.

        You should call this method if you used connect() to establish a
        cluster connection.
        """
        for listener in self.on_disconnect_listeners:
            listener()

        if self.client is not None and self.client.connected:
            self.client.stop()
            self.client.close()
            self.client = None

    def resetHosts(self, hosts):
        """
        Reset the ZooKeeper cluster connection host list.

        :param str hosts: Comma-separated list of hosts to connect to (e.g.
            127.0.0.1:2181,127.0.0.1:2182,[::1]:2183).
        """
        if self.client is not None:
            self.client.set_hosts(hosts=hosts)

    def commitTransaction(self, tr):
        results = tr.commit()
        for res in results:
            self.log.debug("Transaction response %s", repr(res))
        for res in results:
            if isinstance(res, Exception):
                raise res
        return results


class ZooKeeperSimpleBase(metaclass=ABCMeta):
    """Base class for stateless Zookeeper interaction."""

    def __init__(self, client):
        self.client = client

    @property
    def kazoo_client(self):
        if not self.client.client:
            raise NoClientException()
        return self.client.client


class ZooKeeperBase(ZooKeeperSimpleBase):
    """Base class for registering state handling methods with ZooKeeper."""

    def __init__(self, client):
        super().__init__(client)
        if client:
            self.client.on_connect_listeners.append(self._onConnect)
            self.client.on_disconnect_listeners.append(self._onDisconnect)
            self.client.on_reconnect_listeners.append(self._onReconnect)

    def _onConnect(self):
        pass

    def _onDisconnect(self):
        pass

    def _onReconnect(self):
        pass
