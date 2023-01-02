================
Testing Nodepool
================

Below you can find the instructions, how to run the tests for Nodepool in Debian Bullseye.


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

To run the Nodepool tests, a running Zookeeper is required. Zookeeper also needs to be configured for TLS and a certificate authority set up to handle socket authentication. Because of these complexities, it's recommended to use the helper script to set up these dependencies and to configure and run the Noodepool environment::

  ROOTCMD=sudo tools/test-setup-docker.sh

Now access the bash in `nodepool-testing-container` by executing::

  docker-compose -f tools/docker-compose.yaml exec nodepool-testing-container bash


-------------
Run The Tests
-------------

As the project's root directory is mounted as `WORKDIR` in the `nodepool-testing-container`, we can simply execute the below command to run all the tests::

  nox

Note: completing this command may take a long time (depends on system resources)
also, you might not see any output until tox is complete.

Information about nox can be found here: https://nox.thea.codes/en/stable/


Run The Tests in One Environment
--------------------------------

Nox will run your entire test suite in the environments specified in the project noxfile.py::

  @nox.session(python='3')

To run the test suite in specific Python environments, adjust the session config as described here: https://nox.thea.codes/en/stable/tutorial.html#testing-against-different-and-multiple-pythons
 ::

  @nox.session(python='3.10')


Run One Test
------------

To run individual tests with nox::

  nox -s tests -- path.to.module.Class.test

For example, to *run a single Nodepool test*::

  nox -s tests -- nodepool.tests.unit.test_launcher.TestLauncher.test_node_assignment


List Failing Tests
------------------

The following will list all failed unit tests::

  chmod u+x .nox/tests/bin/activate
  source .nox/tests/bin/activate
  stestr failing --list

Hanging Tests
-------------

The following will run each unit test in turn and print the name of the
test as it is run::

  chmod u+x .nox/tests/bin/activate
  source .nox/tests/bin/activate
  stestr --test-path ./nodepool/tests/unit run

You can compare the output of that to::

  python -m testtools.run discover --list

------------------------
Generating documentation
------------------------

To generate the docs locally, run::

  nox -s docs

You can find the resulting html files in the ./doc/build/html directory.

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
