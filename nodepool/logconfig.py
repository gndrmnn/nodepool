# Copyright 2017 Red Hat, Inc.
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

import abc
import copy
import logging.config
import json
import os

import yaml


_DEFAULT_SERVER_LOGGING_CONFIG = {
    'version': 1,
    'formatters': {
        'simple': {
            'class': 'nodepool.logconfig.MultiLineFormatter',
            'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            # Used for printing to stdout
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'level': 'INFO',
            'formatter': 'simple',
        },
        'null': {
            'class': 'logging.NullHandler',
        }
    },
    'loggers': {
        'nodepool': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'requests': {
            'handlers': ['console'],
            'level': 'WARN',
        },
        'openstack': {
            'handlers': ['console'],
            'level': 'WARN',
        },
        'kazoo': {
            'handlers': ['console'],
            'level': 'WARN',
        },
    },
    'root': {'handlers': ['null']},
}

_DEFAULT_SERVER_FILE_HANDLERS = {
    'normal': {
        # Used for writing normal log files
        'class': 'logging.handlers.WatchedFileHandler',
        # This will get set to something real by DictLoggingConfig.server
        'filename': '/var/log/nodepool/{server}.log',
        'level': 'INFO',
        'formatter': 'simple',
    },
}


def _read_config_file(filename: str):
    if not os.path.exists(filename):
        raise ValueError("Unable to read logging config file at %s" % filename)

    if os.path.splitext(filename)[1] in ('.yml', '.yaml', '.json'):
        return yaml.safe_load(open(filename, 'r'))
    return filename


def load_config(filename: str):
    config = _read_config_file(filename)
    if isinstance(config, dict):
        return DictLoggingConfig(config)
    return FileLoggingConfig(filename)


class MultiLineFormatter(logging.Formatter):
    def format(self, record):
        rec = super().format(record)
        ret = []
        # Save the existing message and re-use this record object to
        # format each line.
        saved_msg = record.message
        for i, line in enumerate(rec.split('\n')):
            if i:
                record.message = '  ' + line
                ret.append(self.formatMessage(record))
            else:
                ret.append(line)
        # Restore the message
        record.message = saved_msg
        return '\n'.join(ret)


class LoggingConfig(object, metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def apply(self):
        """Apply the config information to the current logging config."""


class DictLoggingConfig(LoggingConfig, metaclass=abc.ABCMeta):

    def __init__(self, config):
        self._config = config

    def apply(self):
        logging.config.dictConfig(self._config)

    def writeJson(self, filename: str):
        with open(filename, 'w') as f:
            f.write(json.dumps(self._config, indent=2))


class ServerLoggingConfig(DictLoggingConfig):

    def __init__(self, config=None, server=None):
        if not config:
            config = copy.deepcopy(_DEFAULT_SERVER_LOGGING_CONFIG)
        super(ServerLoggingConfig, self).__init__(config=config)
        if server:
            self.server = server

    @property
    def server(self):
        return self._server

    @server.setter
    def server(self, server):
        self._server = server
        # Add the normal file handler. It's not included in the default
        # config above because we're templating out the filename. Also, we
        # only want to add the handler if we're actually going to use it.
        for name, handler in _DEFAULT_SERVER_FILE_HANDLERS.items():
            server_handler = copy.deepcopy(handler)
            server_handler['filename'] = server_handler['filename'].format(
                server=server)
            self._config['handlers'][name] = server_handler
        # Change everything configured to write to stdout to write to
        # log files instead.
        for logger in self._config['loggers'].values():
            if logger['handlers'] == ['console']:
                logger['handlers'] = ['normal']

    def setDebug(self):
        # Change level from INFO to DEBUG
        for section in ('handlers', 'loggers'):
            for handler in self._config[section].values():
                if handler.get('level') == 'INFO':
                    handler['level'] = 'DEBUG'


class FileLoggingConfig(LoggingConfig):

    def __init__(self, filename):
        self._filename = filename

    def apply(self):
        logging.config.fileConfig(self._filename)


def get_annotated_logger(logger, event_id=None, node_request_id=None,
                         node_id=None):
    # Note: When running with python 3.5 log adapters cannot be
    # stacked. We need to detect this case and modify the original one.
    if isinstance(logger, EventIdLogAdapter):
        extra = logger.extra
    else:
        extra = {}

    if event_id is not None:
        extra["event_id"] = event_id

    if node_request_id is not None:
        extra['node_request'] = node_request_id

    if node_id is not None:
        extra["node"] = node_id

    if isinstance(logger, EventIdLogAdapter):
        return logger

    return EventIdLogAdapter(logger, extra)


class EventIdLogAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        msg, kwargs = super().process(msg, kwargs)
        extra = kwargs.get("extra", {})

        new_msg = []
        event_id = extra.get("event_id")
        if event_id is not None:
            new_msg.append('[e: {}]'.format(event_id))

        node_request = extra.get("node_request")
        if node_request is not None:
            new_msg.append("[node_request: {}]".format(node_request))

        node = extra.get("node")
        if node is not None:
            new_msg.append("[node: {}]".format(node))

        new_msg.append(msg)
        msg = " ".join(new_msg)
        return msg, kwargs
