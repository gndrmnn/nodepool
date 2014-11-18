import time
import os
import threading
import tempfile
from nodepool import tests
from nodepool.nodepool import NodePool
from nodepool.tests.api import FunctionalTest
from pecan import conf


class TestImagesController(FunctionalTest):
    def setup_config(self, filename):
        configfile = os.path.join(os.path.dirname(tests.__file__),
                                  'fixtures', filename)
        config = open(configfile).read()
        (fd, path) = tempfile.mkstemp()
        os.write(fd, config.format(dburi=self.dburi))
        os.close(fd)
        return path

    def wait_for_threads(self):
        whitelist = ['APScheduler',
                     'MainThread',
                     'NodePool',
                     'NodeUpdateListener',
                     'Gearman client connect',
                     'Gearman client poll',
                     'fake-provider',
                     'fake-dib-provider',
                     'fake-jenkins',
                     'fake-target',
                     'DiskImageBuilder queue',
                     ]

        while True:
            done = True
            for t in threading.enumerate():
                if t.name not in whitelist:
                    done = False
            if done:
                return
            time.sleep(0.1)

    def wait_for_config(self, pool):
        for x in range(300):
            if pool.config is not None:
                return
            time.sleep(0.1)

    def waitForNodes(self, pool):
        self.wait_for_config(pool)
        while True:
            self.wait_for_threads()
            with pool.getDB().getSession() as session:
                needed = pool.getNeededNodes(session)
                if not needed:
                    break
                time.sleep(1)
        self.wait_for_threads()

    def test_get_images(self):
        configfile = self.setup_config('node.yaml')
        conf['nodepool_conf_file'] = configfile
        pool = NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
        time.sleep(3)
        self.waitForNodes(pool)

        resp = self.app.get('/v1/images', expect_errors=True)
        time.sleep(3)
        assert resp.status_int == 200 and len(resp.json) == 1

    def test_get_image(self):
        configfile = self.setup_config('node.yaml')
        conf['nodepool_conf_file'] = configfile
        pool = NodePool(configfile, watermark_sleep=1)
        pool.start()
        self.addCleanup(pool.stop)
        time.sleep(3)
        self.waitForNodes(pool)

        resp = self.app.get('/v1/images/1', expect_errors=True)
        time.sleep(3)
        assert resp.status_int == 200 and \
            resp.json['image_name'] == 'fake-image'
