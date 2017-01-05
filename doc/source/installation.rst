:title: Installation

Installation
============

Nodepool consists of a long-running daemon which uses ZooKeeper
for coordination with Zuul.

External Requirements
---------------------

Statsd and Graphite
~~~~~~~~~~~~~~~~~~~

If you have a Graphite system with ``statsd``, Nodepool can be
configured to send information to it.  Set the environment variable
``STATSD_HOST`` to the ``statsd`` hostname (and optionally
``STATSD_PORT`` if this should be different to the default ``8125``)
for the Nodepool daemon to enable this support.

Install Nodepool
----------------

Install Nodepool prerequisites.

Nodepool requires Python 2.7 or newer.

RHEL 7 / CentOS 7::

  yum install libffi libffi-devel @development python python-devel

You may install Nodepool directly from PyPI with pip::

  pip install nodepool

Or install directly from a git checkout with::

  pip install .

Configuration
-------------

Nodepool has two required configuration files: secure.conf and
nodepool.yaml, and an optional logging configuration file logging.conf.
The secure.conf file is used to store nodepool configurations that contain
sensitive data, such as the Nodepool database password and Jenkins
api key. The nodepool.yaml files is used to store all other
configurations.

The logging configuration file is in the standard python logging
`configuration file format
<http://docs.python.org/2/library/logging.config.html#configuration-file-format>`_.
The Nodepool configuration file is described in :ref:`configuration`.
