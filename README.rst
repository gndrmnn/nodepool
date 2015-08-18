Nodepool
========

Nodepool is a service used by the OpenStack CI team to deploy and manage a pool
of devstack images on a cloud server for use in OpenStack project testing.

Developer Quickstart setup
==========================

Nodepool can be installed globally or to a virtualenv. If you're not familiar
with virtualenv, you can find out more here_.

.. _here: https://pypi.python.org/pypi/virtualenv

Install dependencies:

.. code-block:: bash

    sudo apt-get update
    sudo apt-get -qy install git mysql-server libmysqlclient-dev g++\
    python-dev python-pip libffi-dev libssl-dev qemu-utils
    mkdir src
    cd ~/src
    git clone git://git.openstack.org/openstack-infra/system-config
    git clone git://git.openstack.org/openstack-infra/nodepool
    cd nodepool

Install nodepool globally

.. code-block:: bash

    sudo pip install -U -r requirements.txt
    sudo pip install -e .

Install nodepool to a virtualenv

.. code-block:: bash

    git clone https://git.openstack.org/openstack-infra/nodepool
    virtualenv venv
    venv/bin/pip install -U ./nodepool

If you're testing a specific patch that is already in gerrit, you will also
want to install git-review and apply that patch while in the nodepool
directory:

.. code-block:: bash

    git review -x XXXXX

Create or adapt a nodepool yaml file. The fake.yaml file included needs to be
edited in order to work. You can adapt an infra/system-config_ one, or fake.yaml
as desired. For a production config, consult
./modules/openstack_project/templates/nodepool/nodepool.yaml.erb_ in the
infra/system-config_ tree.

.. _system-config: https://git.openstack.org/cgit/openstack-infra/system-config/tree/modules/openstack_project/templates/nodepool/
.. _nodepool.yaml.erb: https://git.openstack.org/cgit/openstack-infra/system-config/tree/modules/openstack_project/templates/nodepool/nodepool.yaml.erb

If the cloud being used has no default_floating_pool defined in nova.conf,
you will need to define a pool name using the nodepool yaml file to use
floating ips. # TODO maybe make this a little more clear

Nodepool uses the database to store metadata and status information about its
nodes. For more information about the specific database fields, see nodedb.py
where the database fields are mapped to Python classes. #TODO link?

Create a database called nodepool

.. code-block:: bash

    mysql -u root

    mysql> create database nodepool;
    mysql> GRANT ALL ON nodepool.* TO 'nodepool'@'localhost';
    mysql> flush privileges;

To set up the database for testing, see the Testing documentation. # TODO internal link

Export the variable NODEPOOL_SSH_KEY for your ssh key so you can log into the created instances:

.. code-block:: bash

    export NODEPOOL_SSH_KEY=`cat ~/.ssh/id_rsa.pub | awk '{print $2}'`

Start nodepool with a demo config file (copy or edit fake.yaml
to contain your data):

.. code-block:: bash

    export STATSD_HOST=127.0.0.1
    export STATSD_PORT=8125
    nodepoold -d -c tools/fake.yaml

All logging ends up in stdout. # TODO change that using nodepoold -l...

# TODO what is image-list and what should you do if you don't see anything
Use the following tool to check on progress:

.. code-block:: bash

    nodepool image-list

# TODO what does this mean?
After each run (the fake nova provider is only in-memory):

.. code-block:: bash

   mysql> delete from snapshot_image; delete from node;

# TODO next steps...
