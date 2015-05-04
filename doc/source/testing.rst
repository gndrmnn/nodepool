.. _testing:

Testing
=======

Functional Tests
----------------

Nodepool comes with a set of functional tests that exercise Nodepool's
functionality with mocked out clouds. Running these tests is
straightforward, needing only a mysql database and tox.

First create the database user (note the user/password must be these
values)::

  mysql -e "CREATE USER 'openstack_citest'@'localhost' IDENTIFIED BY 'openstack_citest';"

Next grant the required permissions to the user (note that you likely do
not want to use a production DB for this)::

  mysql -e "GRANT ALL ON *.* TO 'openstack_citest'@'localhost' WITH GRANT OPTION;"

These permissions are necessary for the tests to create per test DB
instances to avoid collisions between tests on the mysql tables.

Final setup step is to create the required database::

  mysql -e "CREATE DATABASE openstack_citest;"

Now you can run the tests with a simple tox command::

  tox -e py27

Running with fakes
------------------

Install dependencies:

.. code-block:: bash

    sudo apt-get update
    sudo apt-get -qy install git mysql-server g++\
                     python-dev python-pip libffi-dev libssl-dev qemu-utils\
                     libxml2-dev libxslt1-dev python-lxml
    mkdir src
    cd ~/src
    git clone git://git.openstack.org/openstack-infra/project-config
    git clone git://git.openstack.org/openstack-infra/nodepool
    cd nodepool
    sudo pip install -U -r requirements.txt
    sudo pip install -e .

If you're testing a specific patch that is already in gerrit, you will also
want to install git-review and apply that patch while in the nodepool
directory, ie:

.. code-block:: bash

    git review -x XXXXX

Create or adapt a nodepool yaml file. You can adapt an infra/project-config
one, or fake.yaml as desired. Note that fake.yaml's settings won't
Just Work - consult ./nodepool/nodepool.yaml in the infra/project-config
tree to see a production config.

If the cloud being used has no default_floating_pool defined in nova.conf,
you will need to define a pool name using the nodepool yaml file to use
floating ips.

Set up database for interactive testing:

.. code-block:: bash

    mysql -u root

    mysql> create database nodepool;
    mysql> GRANT ALL ON nodepool.* TO 'nodepool'@'localhost';
    mysql> flush privileges;

Export variable for your ssh key so you can log into the created instances:

.. code-block:: bash

    export NODEPOOL_SSH_KEY=`cat ~/.ssh/id_rsa.pub | awk '{print $2}'`

Start nodepool with a demo config file (copy or edit fake.yaml
to contain your data):

.. code-block:: bash

    export STATSD_HOST=127.0.0.1
    export STATSD_PORT=8125
    nodepoold -d -c tools/fake.yaml

All logging ends up in stdout.

Use the following tool to check on progress:

.. code-block:: bash

    nodepool image-list

After each run (the fake nova provider is only in-memory):

.. code-block:: bash

    mysql> delete from snapshot_image; delete from node;

Devstack
--------

You can also run your nodepool against an OpenStack deployed by
Devstack. This is handy because Devstack's plugin mechanism and the
nodepool devstack plugin can work together to handle all of the setup
for us.

First configure devstack::

  git clone https://git.openstack.org/openstack-dev/devstack
  cd devstack
  cat > localrc <<EOF
  ENABLED_SERVICES=dstat,g-api,g-reg,key,mysql,n-api,n-cond,n-cpu,n-crt,n-sch,q-agt,q-dhcp,q-l3,q-lbaas,q-meta,q-metering,q-svc,rabbit,s-account,s-container,s-object,s-proxy
  FORCE_CONFIG_DRIVE=True
  enable_plugin nodepool git://git.openstack.org/openstack-infra/nodepool
  EOF

Now run devstack::

  ./stack.sh

This will install an OpenStack cloud for us and configure nodepool to
speak to this cloud. This nodepool configuration is configured to build
an image via the snapshot mechanism and the dib image build worker.

You should be able to follow along using the screen-nodepool and
screen-nodepool-builder logs. When Devstack and nodepool are done you
should have two VMs running, one snapshot built VM and the other a
DIB built VM.

Please refer to the nodepool plugin at ``nodepool/devstack`` for more
details.
