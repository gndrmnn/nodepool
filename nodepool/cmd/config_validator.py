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
import voluptuous as v
import yaml

from nodepool.driver import ProviderConfig
from nodepool.config import get_provider_config

log = logging.getLogger(__name__)


class ConfigValidator:
    """Check the layout and certain configuration options"""

    def __init__(self, config_file):
        self.config_file = config_file

    def validate(self):
        '''
        Validate a configuration file

        :return: 0 for success, non-zero for failure
        '''
        provider = ProviderConfig.getCommonSchemaDict()

        label = {
            'name': str,
            'min-ready': int,
            'max-ready-age': int,
        }

        diskimage = {
            'name': str,
            'dib-cmd': str,
            'pause': bool,
            'elements': [str],
            'formats': [str],
            'release': v.Any(str, int),
            'rebuild-age': int,
            'env-vars': {str: str},
            'username': str,
            'python-path': str,
            'build-timeout': int,
        }

        webapp = {
            'port': int,
            'listen_address': str,
        }

        top_level = {
            'webapp': webapp,
            'elements-dir': str,
            'images-dir': str,
            'build-log-dir': str,
            'build-log-retention': int,
            'zookeeper-servers': [{
                'host': str,
                'port': int,
                'chroot': str,
            }],
            'providers': list,
            'labels': [label],
            'diskimages': [diskimage],
            'max-hold-age': int,
        }

        log.info("validating %s" % self.config_file)

        try:
            with open(self.config_file) as f:
                config = yaml.safe_load(f)
        except Exception:
            log.exception('YAML parsing failed')
            return 1

        try:
            # validate the overall schema
            schema = v.Schema(top_level)
            schema(config)
            for provider_dict in config.get('providers', []):
                provider_schema = \
                    get_provider_config(provider_dict).getSchema()
                provider_schema.extend(provider)(provider_dict)
        except Exception:
            log.exception('Schema validation failed')
            return 1

        errors = False

        # Ensure in openstack provider sections, diskimages have
        # top-level labels
        labels = [x['name'] for x in config.get('labels', [])]
        for provider in config.get('providers', []):
            if provider.get('driver', 'openstack') != 'openstack':
                continue
            for pool in provider.get('pools', []):
                for label in pool.get('labels', []):
                    if label['name'] not in labels:
                        errors = True
                        log.error("diskimage %s in provider %s "
                                  "not in top-level labels" %
                                  (label['name'], provider['name']))

        if errors is True:
            return 1
        else:
            return 0
