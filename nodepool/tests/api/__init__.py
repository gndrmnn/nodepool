import os
from pecan import set_config
from pecan.testing import load_test_app
from nodepool.tests import DBTestCase


class FunctionalTest(DBTestCase):

    def setUp(self):
        super(FunctionalTest, self).setUp()
        self.app = load_test_app(os.path.join(
            os.path.dirname(__file__),
            'config.py'
        ))

    def tearDown(self):
        set_config({}, overwrite=True)
        super(FunctionalTest, self).tearDown()
