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
import io
import os
import traceback

import yaml


_DEFAULT_SERVER_LOGGING_CONFIG = {
    'version': 1,
    'formatters': {
        'simple': {
            '()': 'nodepool.logconfig.exception_collection_formatter_factory',
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
    'root': {'handlers': []},
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


class ExceptionCollectorFormatter(logging.Formatter):
    '''Prefix multi-line exception output with formatting

    This is a customised version of the standard logging.Formatter
    that prefixes all lines of exception traceback with the time,
    level and thread that it came from.  On a very busy,
    multi-threaded app like nodepool this can be a real help in the
    logs.
    '''
    def __init__(self):
        # Note, if you change this, need to change prefix line in
        # formatException as well.
        fmt = '%(asctime)s %(levelname)s %(name)s: %(message)s'
        super().__init__(fmt)

    def formatException(self, record):
        ei = record.exc_info
        sio = io.StringIO()
        tb = ei[2]
        traceback.print_exception(ei[0], ei[1], tb, None, sio)
        s = sio.getvalue()
        sio.close()
        if s[-1:] == '\n':
            s = s[:-1]
        # Now go through, and prepend the info from the record to each
        # line
        sfull = []
        for l in s.split('\n'):
            # Note this is offset couple of extra spaces, it looks like
            #
            #  Exception in main loop:
            #    Traceback (most recent call last)
            #      File "/path" ...
            sfull.append("%s %s %s    %s" %
                         (record.asctime, record.levelname, record.name, l))
        sfull = '\n'.join(sfull)
        return sfull

    def format(self, record):
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        s = self.formatMessage(record)
        # This is the only magic compared to the regular format()
        # function ... the original just passes record.exc_text to
        # formatException() which means it does not not have the
        # time/level/thread info from the record.  Here we pass the
        # full record and our formatException() will prefix exception
        # lines with the right details.
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record)
        if record.exc_text:
            if s[-1:] != "\n":
                s = s + "\n"
            s = s + record.exc_text
        if record.stack_info:
            if s[-1:] != "\n":
                s = s + "\n"
            s = s + self.formatStack(record.stack_info)
        return s


def exception_collection_formatter_factory():
    return ExceptionCollectorFormatter()


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
