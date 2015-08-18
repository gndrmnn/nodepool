.. _testing:

Testing
=======

Functional Tests
----------------

Nodepool's functional tests check basic commands, image creation and management,
and integration with other systems. This is done with mocked out clouds. You can
either run these tests with minimal setup or against a devstack. Before
following the steps below, make sure you have at least a basic Nodepool instance
up and running on your target system. For a basic setup guide, see :ref:`quickstart`.

Basic Test Setup
----------------

The most basic way to run the functional tests requires tox and MySQL.

The OpenStack uses tox as the primary testing framework. To learn more about the
tox project, see the `tox documentation
<https://tox.readthedocs.org/en/latest>`_. For more information about
OpenStack's usage of tox, see the `OpenStack Python Developer's Guide
<http://docs.openstack.org/infra/manual/python.html#python-unit-tests>`_.

Database Setup
```````````````

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

Run the Tests
`````````````
Run the tests with a simple tox command.

.. code-block:: bash

  cd ~/src/nodepool
  tox -e py27

Cleanup
```````

The tests provide a fake nova provider that only lives in memory. Unfortunately
these tests don't clean up after themselves, so you need to manually remove data
from the image tables.

.. code-block:: bash

   mysql> delete from snapshot_image; delete from node;

Testing a Specific Patch
`````````````````````````

To test a specific patch that is already in gerrit, install git-review and apply
that patch while in the Nodepool repository.

.. code-block:: bash

    cd ~/src/nodepool
    git review -x XXXXX

Testing with DevStack
---------------------

You can also run Nodepool against an OpenStack deployed by
DevStack. This is handy because DevStack installs a MySQL for us and
we can make some assumptions about the config required to talk to
a DevStack deployed cloud.

Set up DevStack
```````````````

If you don't already have a DevStack installed, follow the `DevStack
Installation Instructions`_ to set one up.

.. _DevStack Installation Instructions: http://docs.openstack.org/developer/devstack/#quick-start

.. code-block:: bash

  cd ~/src
  git clone https://git.openstack.org/openstack-dev/devstack

Run DevStack.

.. code-block:: bash

  cd ~/src/devstack
  ./stack.sh

Configure Nodepool for DevStack
````````````````````````````````
If you followed the :ref:`quickstart`, you may have created a nodepool.yaml config
file. If you haven't yet, then create one now. The default location for this is
``/etc/nodepool/nodepool.yaml``. Add the sections detailed below to your yaml
file. The full sample yaml file is available in the section `Example Nodepool
Config`_.

script-dir and elements-dir
'''''''''''''''''''''''''''

You will need to make and populate these two paths as necessary, cloning
Nodepool does not do this. See `Example Setup Script`_ further in this doc for an
example script you'll need to copy to your Nodepool scripts path (default: ``/etc/nodepool/scripts``).

.. code-block:: yaml

  script-dir: /etc/nodepool/scripts
  elements-dir: /etc/nodepool/elements

dburi
'''''

The MySQL password here may be different depending on your DevStack install. The
DevStack environment variable is MYSQL_PASSWORD. If this is not set, and
DevStack doesn't prompt you, try the admin password you used when you set up
DevStack (see the `DevStack config file`_).

.. _DevStack config file: http://docs.openstack.org/developer/devstack/configuration.html#minimal-configuration

.. code-block:: yaml

  dburi: 'mysql+pymysql://root:secretmysql@localhost/nodepool'

targets
'''''''

Need to have at least one target for node allocations, but this does not need to
be a Jenkins target.

.. code-block:: yaml

  targets:
    - name: dummy

labels
'''''''

DevStack does not make an Ubuntu image by default. You can grab one from Ubuntu
and upload it yourself, per the instructions below. DevStack provides a cirrOs_
image, which is a minimal Linux distribution. Unfortunately, we cannot use
DevStack's cirrOs_ default because cirrOs_ does not support sftp. See :ref:`images` for
creating the Ubuntu image.

.. _cirrOs: https://launchpad.net/cirros

.. code-block:: yaml

  labels:
    - name: ubuntu
      image: ubuntu
      min-ready: 1
      providers:
        - name: devstack

Example Nodepool Config
'''''''''''''''''''''''

.. code-block:: yaml

  # location to Nodepool support items
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
      password: 'secretadmin' # your DevStack admin password
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

Upload Image
`````````````

DevStack uses Glance to manage images. Glance is installed as part of the
DevStack setup process. For more information about Glance, see the `Glance
documentation`_.

.. _Glance documentation: http://docs.openstack.org/developer/glance/

Once you've finished your nodepool.yaml config file, upload the Ubuntu image to
Glance.

.. code-block:: bash

  wget https://cloud-images.ubuntu.com/trusty/current/trusty-server-cloudimg-amd64-disk1.img
  source /path/to/devstack/openrc
  glance image-create --name ubuntu --disk-format qcow2 --container-format bare --file trusty-server-cloudimg-amd64-disk1.img

Check that the image has been uploaded to DevStack:

.. code-block:: bash

  glance image-list

For more details on how Nodepool works with images, see :ref:`images`.


Example Setup Script
`````````````````````

We need to write a setup script to give our Ubuntu images a user that allows ssh
using the ``$HOME/.ssh/id_rsa`` key. Nodepool will copy and run this script when
it creates the snapshot image. To learn more about how Nodepool uses scripts
when creating snapshots, see :ref:`scripts`.

This example calls the user "jenkins" because that is what most of the machines
in the OpenStack CI environment use to test VM's, and that's where this example
orginally came from. Feel free to use a different user name.

Also remember to change the path to the scripts directory to match the one you
used in your config file above.

.. code-block:: bash

  PUB_KEY=$(cat $HOME/.ssh/id_rsa.pub)
  cat > /etc/nodepool/scripts/prepare_node_ubuntu.sh << EOF
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
  chmod +x /etc/nodepool/scripts/prepare_node_ubuntu.sh

Open Ports
```````````

To allow connectivity from Nodepool to its nodes we also need to open up
our default security group

.. code-block:: bash

  nova secgroup-add-rule default tcp 1 65535 0.0.0.0/0
  nova secgroup-add-rule default udp 1 65535 0.0.0.0/0

Note that this just opens up all the tcp and udp ports but your nodes
should run iptables if that matters anyways.

Run Nodepool With Devstack
```````````````````````````

Now you can run Nodepool in the foreground against your DevStack cloud.

.. code-block:: bash

  venv/bin/nodepoold -c /etc/nodepool/nodepool.yaml -d

Verify it's working with the image-list command.

.. code-block:: bash

  nodepool image-list

This command returns information about your image. For other available commands,
type ``nodepool -h``

If you don't see any images listed, check the Nodepool debug log output for any
errors. Also double check the MySQL credentials in your Nodepool config file.

.. *TODO add troubleshooting tips*
.. Troubleshooting
.. ````````````````



