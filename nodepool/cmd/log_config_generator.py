#!/usr/bin/env python

# Copyright 2015 OpenStack Foundation
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


"""Generate a sample logging configuration file

use log_config_generator.generate_log_config() to generate a sample
logging configuration file.

The sample splits up the log output for general logging and image
builds and applys some sensible rotation defaults.
"""

import logging
import yaml

_BASIC_FILE = """

#
# THIS FILE HAS BEEN AUTOGENERATED
#

[loggers]
keys=root,nodepool,requests,image,%(logger_titles)s

[handlers]
keys=console,debug,normal,image,%(handler_titles)s

[formatters]
keys=simple

[logger_root]
level=WARNING
handlers=console

[logger_requests]
level=WARNING
handlers=debug,normal
qualname=requests

[logger_nodepool]
level=DEBUG
handlers=debug,normal
qualname=nodepool

[logger_image]
level=INFO
handlers=image
qualname=nodepool.image.build
propagate=0

[handler_console]
level=WARNING
class=StreamHandler
formatter=simple
args=(sys.stdout,)

[handler_debug]
level=DEBUG
class=logging.handlers.TimedRotatingFileHandler
formatter=simple
args=('%(log_dir)s/debug.log', 'H', 8, 30,)

[handler_normal]
level=INFO
class=logging.handlers.TimedRotatingFileHandler
formatter=simple
args=('%(log_dir)s/nodepool.log', 'H', 8, 30,)

[handler_image]
level=INFO
class=logging.handlers.TimedRotatingFileHandler
formatter=simple
args=('%(image_log_dir)s/image.log', 'H', 8, 30,)

[formatter_simple]
format=%%(asctime)s %%(levelname)s %%(name)s: %%(message)s
datefmt=

# ==== individual image loggers ====

%(image_loggers_and_handlers)s"""

_IMAGE_HANDLER = """
[handler_%(title)s]
level=INFO
class=logging.handlers.TimedRotatingFileHandler
formatter=simple
args=('%(image_log_dir)s/%(filename)s', 'H', 8, 30,)
"""

_IMAGE_LOGGER = """
[logger_%(title)s]
level=INFO
handlers=%(handler)s
qualname=nodepool.image.build.%(qualname)s
propagate=0
"""


def _get_providers_and_images(config):
    ret = []
    with open(config, 'r') as stream:
        config = yaml.load(stream)
        for label in config['labels']:
            for provider in label['providers']:
                ret.append((provider['name'], label['name']))
    logging.debug("Added %d providers & images" % len(ret))
    return ret


def _generate_logger_and_handler(image_log_dir, provider, image):
    handler = _IMAGE_HANDLER % {
        'image_log_dir': image_log_dir,
        'title': '%s_%s' % (provider, image),
        'filename': '%s.%s.log' % (provider, image),
    }
    logger = _IMAGE_LOGGER % {
        'title': '%s_%s' % (provider, image),
        'handler': '%s_%s' % (provider, image),
        'qualname': '%s.%s' % (provider, image),
    }

    return {
        'handler_title': '%s_%s' % (provider, image),
        'logger_title': '%s_%s' % (provider, image),
        'handler': handler,
        'logger': logger,
    }


def generate_log_config(config, log_dir, image_log_dir, output):

    """Generate a sample logging file

    :param config: input config file
    :param log_dir: directory for main log file
    :param image_log_dir: directory for image build logs
    :param output: open file handle to output sample configuration to
    """

    loggers_and_handlers = []
    logging.debug("Reading config file %s" % config)
    for (provider, image) in _get_providers_and_images(config):
        loggers_and_handlers.append(
            _generate_logger_and_handler(image_log_dir, provider, image))

    logger_titles = []
    handler_titles = []
    image_loggers_and_handlers = ""
    for item in loggers_and_handlers:
        logger_titles.append(item['logger_title'])
        handler_titles.append(item['handler_title'])
        image_loggers_and_handlers += item['logger'] + item['handler']

    final_output = _BASIC_FILE % {
        'log_dir': log_dir,
        'image_log_dir': image_log_dir,
        'logger_titles': ','.join(logger_titles),
        'handler_titles': ','.join(handler_titles),
        'image_loggers_and_handlers': image_loggers_and_handlers,
    }

    output.write(final_output)
    output.flush()
