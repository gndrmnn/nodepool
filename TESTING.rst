================
Testing Nodepool
================
------------
A Quickstart
------------

This is designed to be enough information for you to run your first tests on
an Ubuntu 16.04 (or later) host.

*Install pip*::

  sudo apt-get install python3-pip

More information on pip here: http://www.pip-installer.org/en/latest/

*Use pip to install tox*::

  sudo pip3 install tox

A running zookeeper is required to execute tests.

*Install zookeeper*::

  sudo apt-get install zookeeperd

*Start zookeeper*::

  sudo service zookeeper start

Run The Tests
-------------

*Navigate to the project's root directory and execute*::

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

Need More Info?
---------------

More information about stestr: http://stestr.readthedocs.io/en/latest/
