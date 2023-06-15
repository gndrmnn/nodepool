#!/usr/bin/env python3


from collections import defaultdict
import logging
from nodepool import launcher
from nodepool.zk.components import COMPONENT_REGISTRY
import nodepool.zk.zookeeper as zk
from nodepool.cmd import NodepoolApp
from nodepool.zk import ZooKeeperClient


class RequestAnalyzer(NodepoolApp):

    def create_parser(self):
        parser = super().create_parser()

        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            help='path to secure file')
        parser.add_argument('--debug', dest='debug', action='store_true',
                            help='show DEBUG level logging')

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

            l = logging.getLogger('nodepool.ComponentRegistry')
            l.setLevel(logging.WARNING)

    def run(self):
        self.pool = launcher.NodePool(self.args.secure, self.args.config)
        config = self.pool.loadConfig()

        self.zk_client = ZooKeeperClient(
            config.zookeeper_servers,
            tls_cert=config.zookeeper_tls_cert,
            tls_key=config.zookeeper_tls_key,
            tls_ca=config.zookeeper_tls_ca
        )
        self.zk_client.connect()
        self.zk = zk.ZooKeeper(self.zk_client, enable_cache=False)

        requests = self.zk.nodeRequestIterator(cached=False)
        requests = (r for r in requests if r.state in (zk.REQUESTED))

        pools = self.zk.getRegisteredPools()

        seen_providers = set()

        labels_by_provider = {}

        providers_by_label = defaultdict(list)

        print(len(pools))

        for pool in pools:
            provider = pool.provider_name

            if provider in seen_providers:
                # we've seen already a pool of this provider so skip it
                continue

            seen_providers.add(provider)
            supported_labels = pool.supported_labels
            labels_by_provider[provider] = supported_labels
            for label in supported_labels:
                providers_by_label[label].append(provider)

        provider_queues = defaultdict(lambda: 0)

        for idx, request in enumerate(requests):
            # print(request)
            # if idx > 10:
                # break

            for provider in providers_by_label.get(request.node_types[0], ['unknown']):
                provider_queues[provider] += 1

        for provider, queue in sorted(provider_queues.items()):
            print(provider, queue)

if __name__ == "__main__":
    RequestAnalyzer.main()
