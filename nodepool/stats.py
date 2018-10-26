#!/usr/bin/env python
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

"""
Helper to create a statsd client from environment variables
"""

import os
import logging
import statsd
import time

from nodepool import zk
from threading import Timer

log = logging.getLogger("nodepool.stats")

STATS_INTERVAL = 15


def get_client():
    """Return a statsd client object setup from environment variables; or
    None if they are not set
    """

    # note we're just being careful to let the default values fall
    # through to StatsClient()
    statsd_args = {}
    if os.getenv('STATSD_HOST', None):
        statsd_args['host'] = os.environ['STATSD_HOST']
    if os.getenv('STATSD_PORT', None):
        statsd_args['port'] = os.environ['STATSD_PORT']
    if statsd_args:
        return statsd.StatsClient(**statsd_args)
    else:
        return None


class StatsReporter(object):

    # This holds a tuple of (last_sent, <True if scheduled>) per provider
    provider_stats = {}

    '''
    Class adding statsd reporting functionality.
    '''
    def __init__(self):
        super(StatsReporter, self).__init__()
        self._statsd = get_client()

    def recordLaunchStats(self, subkey, dt):
        '''
        Record node launch statistics.

        :param str subkey: statsd key
        :param int dt: Time delta in milliseconds
        '''
        if not self._statsd:
            return

        keys = [
            'nodepool.launch.provider.%s.%s' % (
                self.provider_config.name, subkey),
            'nodepool.launch.%s' % (subkey,),
        ]

        if self.node.az:
            keys.append('nodepool.launch.provider.%s.%s.%s' %
                        (self.provider_config.name, self.node.az, subkey))

        if self.handler.request.requestor:
            # Replace '.' which is a graphite hierarchy, and ':' which is
            # a statsd delimeter.
            requestor = self.handler.request.requestor.replace('.', '_')
            requestor = requestor.replace(':', '_')
            keys.append('nodepool.launch.requestor.%s.%s' %
                        (requestor, subkey))

        pipeline = self._statsd.pipeline()
        for key in keys:
            pipeline.timing(key, dt)
            pipeline.incr(key)
        pipeline.send()

    def updateNodeStats(self, zk_conn, provider, force=False):
        '''
        Refresh statistics for all known nodes.

        :param ZooKeeper zk_conn: A ZooKeeper connection object.
        :param Provider provider: A config Provider object.
        :param: bool force: Force update
        '''
        if not self._statsd:
            return

        if not force:
            last_sent, scheduled = StatsReporter.provider_stats.get(
                provider.name, (None, False))
            if scheduled:
                # There is already a scheduled update so nothing to do here
                return

            if not last_sent:
                last_sent = time.time() - STATS_INTERVAL

            wait_time = max((last_sent + STATS_INTERVAL) - time.time(), 1)
            timer = Timer(wait_time, self.updateNodeStats,
                          args=[zk_conn, provider, True])

            # Flag that the next update is scheduled
            StatsReporter.provider_stats[provider.name] = (last_sent, True)
            timer.daemon = True
            timer.start()
            return

        states = {}

        # Initialize things we know about to zero
        for state in zk.Node.VALID_STATES:
            key = 'nodepool.nodes.%s' % state
            states[key] = 0
            key = 'nodepool.provider.%s.nodes.%s' % (provider.name, state)
            states[key] = 0

        # Initialize label stats to 0
        for label in provider.getSupportedLabels():
            for state in zk.Node.VALID_STATES:
                key = 'nodepool.label.%s.nodes.%s' % (label, state)
                states[key] = 0

        try:
            # Note that we intentionally don't use caching here because we
            # don't know when the next update will happen and thus need to
            # report the correct most recent state.
            for node in zk_conn.nodeIterator(cached=False):
                # nodepool.nodes.STATE
                key = 'nodepool.nodes.%s' % node.state
                states[key] += 1

                # nodepool.label.LABEL.nodes.STATE
                # nodes can have several labels
                for label in node.type:
                    key = 'nodepool.label.%s.nodes.%s' % (label, node.state)
                    # It's possible we could see node types that aren't in our
                    # config
                    if key in states:
                        states[key] += 1
                    else:
                        states[key] = 1

                # nodepool.provider.PROVIDER.nodes.STATE
                key = 'nodepool.provider.%s.nodes.%s' % (node.provider,
                                                         node.state)
                # It's possible we could see providers that aren't in our
                # config
                if key in states:
                    states[key] += 1
                else:
                    states[key] = 1
        except AttributeError:
            # zk throws an AttributeError if it is shutdown. This is normally
            # caused by a nodepool shutdown.
            return

        pipeline = self._statsd.pipeline()
        for key, count in states.items():
            pipeline.gauge(key, count)

        # nodepool.provider.PROVIDER.max_servers
        key = 'nodepool.provider.%s.max_servers' % provider.name
        max_servers = sum([p.max_servers for p in provider.pools.values()
                           if p.max_servers])
        pipeline.gauge(key, max_servers)
        pipeline.send()

        # Record reported time and reset scheduled flag
        StatsReporter.provider_stats[provider.name] = (time.time(), False)
        log.info('Updated node stats of provider %s', provider.name)
