Nodepool
========

Nodepool is a service used by the OpenStack CI team to deploy and manage a pool
of devstack images on a cloud server for use in OpenStack project testing.

Quickstart Setup
==========================

The goal of the Quickstart Setup is to get a very barebones Nodepool daemon up and
running in order to verify that all dependencies have been met and there are no
issues with your installation. Each section provides links to resources where
you can further customize your Nodepool daemon.

Setup
-----

Install dependencies using apt-get. If you are not using a Debian-based
distribution, you may need to use a different package manager and different
package names.

.. code-block:: bash

    sudo apt-get update
    sudo apt-get -qy install git mysql-server libmysqlclient-dev g++\
                     python-dev python-pip libffi-dev libssl-dev qemu-utils

Create a directory to store the source code and clone the nodepool and
system-config directories from git.openstack.org.

.. code-block:: bash

    mkdir src
    cd ~/src
    git clone git://git.openstack.org/openstack-infra/system-config
    git clone git://git.openstack.org/openstack-infra/nodepool


Install Nodepool
----------------

Nodepool can be installed globally or to a virtualenv. If you're not familiar
with virtualenv, you can find out more here_.

.. _here: virtualenv_

If you're not using virtualenv, install nodepool globally using pip:

.. code-block:: bash

    cd ~/src/nodepool
    sudo pip install -U -r requirements.txt
    sudo pip install -e .

If you are using virtualenv, create "venv" and install nodepool to it:

.. code-block:: bash

    cd ~/src
    virtualenv venv
    venv/bin/pip install -U ./nodepool

Configuration
-------------

Nodepool requires a configuration file in order to run. Here is a barebones
nodepool.yaml file that will let you launch nodepool and verify that your
installation worked. See Configuration_ for more information on
available sections. Links to additional example configuration files are provided
below.

.. code-block:: yaml

  # mysql db info
  dburi: 'mysql+pymysql://nodepool@localhost/nodepool'

  zmq-publishers: []
  providers: []
  labels: []
  targets: []

An example nodepool configuration file is available in the tools directory of
the nodepool repository, called fake.yaml. Additionally, infra/system-config_
contains several. For a production example, see nodepool.yaml.erb_ in
the infra/system-config_ tree (under
modules/openstack_project/templates/nodepool). If you plan on setting up
nodepool for testing against a devstack, see Testing_ for more configuration
specifics.

Save the nodepool.yaml file to ``/etc/nodepool/nodepool.yaml``. This is the
default location for any supporting files such as configuration files and
scripts. If you want to use a nodepool config file in a different location,
specify the path following the -c command when running nodepoold. See `Launch
Nodepool`_.

If the cloud being used has no default_floating_pool defined in nova.conf,
you will need to define a pool name using the nodepool yaml file to use
floating ips.

*TODO explain more specifically who/what situations this applies
to and where they can find examples of this kind of config*

Database
--------

Nodepool uses the database to store metadata and status information about its
nodes. This example uses MySQL, but other options are also supported. For more
information about Nodepool's database usage and requirements, see Installation_.
For more information about the specific database fields, see nodedb.py_ where the
database fields are mapped to Python classes.

Create a database called nodepool:

.. code-block:: bash

    mysql -u root

    mysql> create database nodepool;
    mysql> GRANT ALL ON nodepool.* TO 'nodepool'@'localhost';
    mysql> flush privileges;

To set up the database for testing against a DevStack, see Testing_.

SSH Key
--------

Export the variable NODEPOOL_SSH_KEY for your ssh key so you can log into the created instances:

.. code-block:: bash

    export NODEPOOL_SSH_KEY=`cat ~/.ssh/id_rsa.pub | awk '{print $2}'`

Launch Nodepool
---------------

Start nodepool, specify the debug flag to turn on debug level logging, and
provide the nodepool.yaml file you created in the previous steps:

.. code-block:: bash

    nodepoold -d

If you are using a config file somewhere other than
``/etc/nodepool/nodepool.yaml``, use the -c command to provide the path:

.. code-block:: bash

  cd nodepool
  nodepool -d -c tools/fake.yaml

If you used a virtualenv and haven't added its path to your environment, make
sure you specify the full path to the nodepoold in the venv bin directory
(``~/src/venv/bin/nodepoold``)

When you launch nodepoold, all logging ends up in stdout by default. You can change this by providing a
logging configuration file with the -l argument when running nodepoold. For more
details, see Installation_.

Verify
------

Check that Nodepool is running by getting the version number:

.. code-block:: bash

  nodepool --version

This should return the current version number.

List images to check the database connection:

.. code-block:: bash

  nodepool image-list

If you just followed the barebones config, you won't get any images back.

If you used a virtualenv and haven't added its path to your environment, make
sure you specify the full path to nodepool in the venv bin directory
(``~/src/venv/bin/nodepool``)

Additional Steps
----------------

To see a list of available commands, either type nodepool -h or see Operation_.
Some of these commands may fail with the barebones configuration provided in
this README. See the Configuration_ section in this document for more information.

To set up Nodepool to work with a Devstack and build images, see Testing_.

.. _Configuration: *TODO*
.. _Installation: *TODO*
.. _Operation: *TODO*
.. _Testing: *TODO*

.. _nodedb.py: *TODO*

.. _virtualenv: https://pypi.python.org/pypi/virtualenv
.. _system-config: https://git.openstack.org/cgit/openstack-infra/system-config/tree/modules/openstack_project/templates/nodepool/
.. _nodepool.yaml.erb: https://git.openstack.org/cgit/openstack-infra/system-config/tree/modules/openstack_project/templates/nodepool/nodepool.yaml.erb
