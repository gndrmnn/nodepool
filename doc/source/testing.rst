.. _testing:

Testing
=======

Functional Tests
----------------

Nodepool's functional test suites check. This is done with mocked out
clouds. You can either run these tests with minimal setup or against a
devstack. Before following the steps below, if you haven't already, install and
run nodepool per the instructions in the README.  # TODO Link?

Minimal Setup
-------------

# TODO this section needs subsections

The most basic way to run the functional tests requires tox and MySQL.
# TODO what is tox and where can i learn more?
# TODO why does tox need the database?


First create the database user (note the user/password must be these
values)

.. code-block:: bash

  mysql -e "CREATE USER 'openstack_citest'@'localhost' IDENTIFIED BY 'openstack_citest';"

Next grant the required permissions to the user (note that you likely do
not want to use a production DB for this)

.. code-block:: bash

  mysql -e "GRANT ALL ON *.* TO 'openstack_citest'@'localhost' WITH GRANT OPTION;"

These permissions are necessary for the tests to create per test DB
instances to avoid collisions between tests on the mysql tables.

Final setup step is to create the required database

.. code-block:: bash

  mysql -e "CREATE DATABASE openstack_citest;"

Now you can run the tests with a simple tox command

.. code-block:: bash

  tox -e py27

Devstack
--------

You can also run your nodepool against an OpenStack deployed by
Devstack. This is handy because Devstack installs a mysql for us and
we can make some assumptions about the config required to talk to
a Devstack deployed cloud.

If you don't already have a devstack installed, follow the installation
instructions to set one up. # TODO link to devstack instructions

## TODO: verify it works like this, i'm pretty sure it doesn't since you need to
do something with the config

Run devstack

.. code-block:: bash

  git clone https://git.openstack.org/openstack-dev/devstack
  cd devstack
  ./stack.sh

# TODO: do details on sections to add to the yaml file, then provide the full copyable sample that builds on the one in the README
If you followed the README #TODO link instructions, you may have created a
nodepool.yaml config file. If you haven't yet, then create one now. The default
location for this is ``/etc/nodepool/nodepool.yaml``. Here is a minimal nodepool.yaml
config file that does not communicate with Jenkins and Gearman. It's broken up
further below per section with specific comments and instructions.

.. code-block:: yaml

  # location to nodepool support items
  script-dir: /etc/nodepool/scripts
  elements-dir: /etc/nodepool/elements

  # mysql db info
  dburi: 'mysql+pymysql://root:secretmysql@localhost/nodepool'

  gearman-servers: []
  zmq-publishers: []

  # Target for node allocations
  targets:
    - name: dummy

  cron:
    cleanup: '*/1 * * * *'
    check: '*/15 * * * *'
    image-update: '14 14 * * *'

  # available images
  labels:
    - name: ubuntu
      image: ubuntu
      min-ready: 1
      providers:
        - name: devstack

  providers:
    - name: devstack
      region-name: 'RegionOne'
      service-type: 'compute'
      username: 'demo'
      project-id: 'demo'
      password: 'secretadmin' # your devstack admin password
      auth-url: 'http://127.0.0.1:5000/v2.0'
      api-timeout: 60
      # Long boot timeout to deal with potentially nested virt.
      boot-timeout: 600
      max-servers: 2
      rate: 0.25
      images:
        - name: ubuntu
          base-image: 'ubuntu'
          min-ram: 2048
          # This script should setup the jenkins user to accept
          # the ssh key configured below. It goes in the script-dir
          # configured above and an example is below.
          setup: prepare_node_ubuntu.sh
          username: jenkins
          # Alter below to point to your local user private key
          private-key: /home/user/.ssh/id_rsa

You will need to make and populate these two paths as necessary, cloning
nodepool does not do this. Further in this doc we have an example script you'll
need to copy to /path/to/nodepool/scripts.

.. code-block:: yaml

  script-dir: /etc/nodepool/scripts
  elements-dir: /etc/nodepool/elements

The mysql password here may be different depending on your devstack install. The
devstack environment variable is MYSQL_PASSWORD. If this is not set, and
devstack doesn't prompt you, try the admin password you used when you set up
devstack (see the devstack config file). # TODO link/location

.. code-block:: yaml

  dburi: 'mysql+pymysql://root:secretmysql@localhost/nodepool'

Need to have at least one target for node allocations, but this does not need to
be a jenkins target.

.. code-block:: yaml

  targets:
    - name: dummy

Devstack does not make an Ubuntu image by default. You can grab one from Ubuntu
and upload it yourself, per the instructions below. Devstack provides a cirrOs_
image, which is a minimal Linux distribution. Unfortunately, we cannot use
devstack's cirrOs_ default because cirrOs_ does not support sftp.

.. _cirrOs: https://launchpad.net/cirros

.. code-block:: yaml

  labels:
    - name: ubuntu
      image: ubuntu
      min-ready: 1
      providers:
        - name: devstack

Once you've finished your nodepool.yaml config file, upload the ubuntu image to
glance:

.. code-block:: bash

  wget https://cloud-images.ubuntu.com/trusty/current/trusty-server-cloudimg-amd64-disk1.img
  source /path/to/devstack/openrc
  glance image-create --name ubuntu --disk-format qcow2 --container-format bare --file trusty-server-cloudimg-amd64-disk1.img

Check that the image has been uploaded to devstack:

.. code-block:: bash

  # TODO

# TODO i thought our config didn't communicate with jenkins??
We also need to write out our prepare_node_ubuntu.sh script. Its job is
to give us a jenkins user that allows ssh using the ``$HOME/.ssh/id_rsa``
key

.. code-block:: bash

  PUB_KEY=$(cat $HOME/.ssh/id_rsa.pub)
  cat > /path/to/nodepool/things/scripts/prepare_node_ubuntu.sh << EOF
  #!/bin/bash -x
  sudo adduser --disabled-password --gecos "" jenkins
  sudo mkdir -p /home/jenkins/.ssh
  cat > tmp_authorized_keys << INNEREOF
  $PUB_KEY
  INNEREOF
  sudo mv tmp_authorized_keys /home/jenkins/.ssh/authorized_keys
  sudo chmod 700 /home/jenkins/.ssh
  sudo chmod 600 /home/jenkins/.ssh/authorized_keys
  sudo chown -R jenkins:jenkins /home/jenkins
  sleep 5
  sync
  EOF
  chmod +x /path/to/nodepool/things/scripts/prepare_node_ubuntu.sh

To allow connectivity from nodepool to its nodes we also need to open up
our default security group

.. code-block:: bash

  nova secgroup-add-rule default tcp 1 65535 0.0.0.0/0
  nova secgroup-add-rule default udp 1 65535 0.0.0.0/0

Note that this just opens up all the tcp and udp ports but your nodes
should run iptables if that matters anyways.

Now you can run nodepool in the foreground against your devstack cloud::

  venv/bin/nodepoold -c /path/to/nodepool/things/nodepool.yaml -d

# TODO how do i know if it's working??

# TODO add troubleshooting tips

To test a specific patch that is already in gerrit, you will also
want to install git-review and apply that patch while in the nodepool
directory:

.. code-block:: bash

    git review -x XXXXX
