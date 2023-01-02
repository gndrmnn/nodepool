================
Testing Nodepool
================

Below you can find the instructions, how to run the tests for Nodepool in Ubuntu 22.04 and Python 3.10.


-------------
Prerequisites
-------------

We assume you have the following tools installed::  
- [Docker](https://docs.docker.com/engine/install/)  
- [Docker Compose](https://docs.docker.com/compose/install/)  


-----------------
Setup environment
-----------------

Navigate to the project's root directory and execute the following command to build the testing container locally::

  docker build . -f tools/Dockerfile_Testing -t nodepool-testing-container

To run the Nodepool tests, a running Zookeeper is required. Zookeeper also needs to be configured for TLS and a certificate authority set up to handle socket authentication. Because of these complexities, it's recommended to use the helper script to set up these dependencies and configure the Noodepool environment::

  ROOTCMD=sudo tools/test-setup-docker.sh

Now access the bash in `nodepool-testing-container` by executing::

  docker-compose -f tools/docker-compose.yaml exec nodepool-testing-container bash


-------------
Run The Tests
-------------

In the project's root directory, execute::

  tox

Note: completing this command may take a long time (depends on system resources)
also, you might not see any output until tox is complete.

Information about tox can be found here: http://testrun.org/tox/latest/


Run The Tests in One Environment
--------------------------------

Tox will run your entire test suite in the environments specified in the project tox.ini::

  [tox]

  envlist = <list of available environments>

To run the test suite in just one of the environments in envlist execute::

  tox -e <env>
so for example, *run the test suite in py35*::

  tox -e py35

Run One Test
------------

To run individual tests with tox::

  tox -e <env> -- path.to.module.Class.test

For example, to *run a single Nodepool test*::

  tox -e py35 -- nodepool.tests.unit.test_launcher.TestLauncher.test_node_assignment

To *run one test in the foreground* (after previously having run tox
to set up the virtualenv)::

  .tox/py35/bin/stestr run nodepool.tests.unit.test_launcher.TestLauncher.test_node_assignment

List Failing Tests
------------------

  .tox/py35/bin/activate
  stestr failing --list

Hanging Tests
-------------

The following will run each test in turn and print the name of the
test as it is run::

  . .tox/py35/bin/activate
  stestr run

You can compare the output of that to::

  python -m testtools.run discover --list


--------------------
Teardown environment
--------------------

To exit the container, run::

  exit

To stop the Zookeeper and Testing container, run::

  docker-compose -f tools/docker-compose.yaml down


Need More Info?
---------------

More information about stestr: http://stestr.readthedocs.io/en/latest/
