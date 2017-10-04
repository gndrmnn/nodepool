:title: Installation

Installation
============

Nodepool consists of a long-running daemon which uses ZooKeeper
for coordination with Zuul.

External Requirements
---------------------

ZooKeeper
~~~~~~~~~

Nodepool uses ZooKeeper to coordinate image builds with its separate
image builder component.  A single ZooKeeper instance running on the
Nodepool server is fine.  Larger installations may wish to use a
multi-node ZooKeeper installation, in which case three nodes are
usually recommended.

Nodepool only needs to be told how to contact the ZooKeeper cluster;
it will automatically populate the ZNode structure as needed.

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

Nodepool has one required configuration file, which defaults to
``/etc/nodepool/nodepool.yaml``. This can be changed with the ``-c`` option.
The Nodepool configuration file is described in :ref:`configuration`.

Although there is support for a secure file that is used to store nodepool
configurations that contain sensitive data, this is currently not used, but
may be in the future.

The Nodepool configuration file is described in :ref:`configuration`.

Logging
-------

Nodepool uses standard `python logging`_.

There is an optional logging configuration file, specified with the ``-l``
option. The logging configuration file can accept either:

* the traditional `ini config format`_.

* a `.yml` or `.yaml` suffixed file that will be parsed and loaded as the newer
`dictConfig format`_.

Nodepool provides additional variables that can be used in
`Formatter Strings`_, and provides special semantics for one of the standard
`Log Record Attributes`_:

resource_name
  Name of a specific instance of a resource.

short_name
  Name of the logger used without resource information attached.

name
  ``name`` is a standard `Log Record`_ attribute. In Nodepool, if a logger
  has a ``resource_name``, it will be appended to ``name``, otherwise it will
  be the raw logger name.

For instance, image builds log to a logger called ``nodepool.image.build`` and
specify the name of the diskimage being built as the ``resource_name``. For a
diskimage named ``example``, this leads to the following values being set:

.. code-block:: python

  name = 'nodepool.image.build.example'
  resource_name = 'example'
  short_name = 'nodepool.image.build'

This allows configuring logging to have per-image build logs keyed off of
``name``. For example, assuming diskimages called ``example`` and ``other``:

::

  [loggers]
  keys=example,other

  [handlers]
  keys=example,other

  [formatters]
  keys=simple

  [formatter_simple]
  format=%(asctime)s %(levelname)s %(message)s

  [logger_example]
  level=DEBUG
  handler=example
  qualname=nodepool.image.build.example

  [logger_other]
  level=DEBUG
  handler=example
  qualname=nodepool.image.build.other

  [handler_example]
  level=DEBUG
  class=logging.handlers.TimedRotatingFileHandler
  formatter=simple
  args=('/var/log/nodepool/image-example.log', 'H', 8, 30,)

  [handler_example]
  level=DEBUG
  class=logging.handlers.TimedRotatingFileHandler
  formatter=simple
  args=('/var/log/nodepool/image-other.log', 'H', 8, 30,)

Or to put it all in one file with the ``resource_name`` included via
the logger name:

::

  [loggers]
  keys=image

  [handlers]
  keys=image

  [formatters]
  keys=simple

  [formatter_simple]
  format=%(asctime)s %(levelname)s %(name)s: %(message)s

  [logger_image]
  level=DEBUG
  handler=image
  qualname=nodepool.image.build

  [handler_image]
  level=DEBUG
  class=logging.handlers.TimedRotatingFileHandler
  formatter=simple
  args=('/var/log/nodepool/image-debug.log', 'H', 8, 30,)

Or to put it all in one file with the ``resource_name`` included called out
separately:

::

  [loggers]
  keys=image

  [handlers]
  keys=image

  [formatters]
  keys=simple

  [formatter_simple]
  format=%(asctime)s %(levelname)s %(short_name)s [%(resource_name)s]: %(message)s

  [logger_image]
  level=DEBUG
  handler=image
  qualname=nodepool.image.build

  [handler_image]
  level=DEBUG
  class=logging.handlers.TimedRotatingFileHandler
  formatter=simple
  args=('/var/log/nodepool/image-debug.log', 'H', 8, 30,)

.. _python logging: https://docs.python.org/3/library/logging.html
.. _ini config format: https://docs.python.org/3/library/logging.config.html#configuration-file-format
.. _dictConfig format: https://docs.python.org/3/library/logging.config.html#configuration-dictionary-schema
.. _Formatter Strings: https://docs.python.org/3/library/logging.html#logging.Formatter
.. _Log Record Attributes: https://docs.python.org/3/library/logging.html#logrecord-attributes
.. _Log Record: https://docs.python.org/3/library/logging.html#logrecord-objects
