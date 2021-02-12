#!/usr/bin/env python3
import logging
import time

from nodepool import launcher, zk
from nodepool.cmd import NodepoolApp


class LoadTest(NodepoolApp):
    log = logging.getLogger('nodepool.loadtest')

    def create_parser(self):
        parser = super().create_parser()

        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            help='path to secure file')
        parser.add_argument('--debug', dest='debug', action='store_true',
                            help='show DEBUG level logging')
        parser.add_argument('--max-queue', dest='max_queue', default=5)

        parser.add_argument('--label', help='Label to use', required=True)

        return parser

    def setup_logging(self):
        if self.args.debug:
            m = '%(asctime)s %(levelname)s %(name)s: %(message)s'
            logging.basicConfig(level=logging.DEBUG, format=m)

        elif self.args.logconfig:
            super().setup_logging()

        else:
            m = '%(asctime)s %(levelname)s %(name)s: %(message)s'
            logging.basicConfig(level=logging.INFO, format=m)

            l = logging.getLogger('kazoo')
            l.setLevel(logging.WARNING)

    def run(self):
        self.log.debug('debug log')
        self.log.info('info log')

        self.pool = launcher.NodePool(self.args.secure, self.args.config)
        config = self.pool.loadConfig()

        self.zk = zk.ZooKeeper(enable_cache=False)
        self.zk.connect(
            list(config.zookeeper_servers.values()),
            tls_cert=config.zookeeper_tls_cert,
            tls_key=config.zookeeper_tls_key,
            tls_ca=config.zookeeper_tls_ca)

        label = self.args.label
        max_queue = int(self.args.max_queue)

        self.log.info('Starting load test:')
        self.log.info('  label: %s', label)
        self.log.info('  max_queue: %s', max_queue)
        while True:
            self._handle_finished_requests()
            self._create_requests(label, max_queue)
            time.sleep(5)

    def _create_requests(self, label, max_queue):
        pending_requests = [
            r for r in self.zk.nodeRequestIterator(cached=False)
            if r.state in (zk.REQUESTED, zk.PENDING)
        ]

        missing_requests = max(max_queue - len(pending_requests), 0)
        self.log.info('Pending requests: %s, creating %s new requests' % (
            len(pending_requests), missing_requests))

        for _ in range(missing_requests):
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.requestor = 'NodePool:load-test'
            req.node_types.append(label)
            req.reuse = False
            self.zk.storeNodeRequest(req)

    def _handle_finished_requests(self):
        finished_requests = [
            r for r in self.zk.nodeRequestIterator(cached=False)
            if r.requestor == 'NodePool:load-test'
            if r.state in (zk.FULFILLED, zk.FAILED)
        ]
        fulfilled_requests = [
            r for r in finished_requests if r.state == zk.FULFILLED
        ]
        failed_requests = [
            r for r in finished_requests if r.state == zk.FAILED
        ]
        if failed_requests:
            self.log.error('Handling %s failed requests', len(failed_requests))
            for request in failed_requests:
                self.zk.deleteNodeRequest(request)

        self.log.info('Handling %s fulfilled requests', len(fulfilled_requests))
        for request in fulfilled_requests:
            # TODO: handle nodes
            self.zk.deleteNodeRequest(request)



if __name__ == "__main__":
    LoadTest.main()

