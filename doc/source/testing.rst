Testing
=======

Functional Tests
----------------

Nodepool's functional tests check basic commands, image creation and management,
and integration with other systems. This is done with mocked out clouds. You can
either run these tests with minimal setup or against a devstack. Before
following the steps below, make sure you have at least a basic nodepool instance
up and running on your target system. For a basic setup guide, see the README_.

Minimal Setup
-------------

The most basic way to run the functional tests requires tox and MySQL.
*TODO what is tox and where can i learn more?*
*TODO why does tox need the database?*

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
Now you can run the tests with a simple tox command

.. code-block:: bash

  tox -e py27

Cleanup
```````

The tests provide a fake nova provider that only lives in memory. Unfortunately
these tests don't clean up after themselves, so you need to manually remove data
from the image tables.

.. code-block:: bash

   mysql> delete from snapshot_image; delete from node;

*TODO verify that this is still true*

Devstack
--------

You can also run your nodepool against an OpenStack deployed by
Devstack. This is handy because Devstack installs a mysql for us and
we can make some assumptions about the config required to talk to
a Devstack deployed cloud.

If you don't already have a devstack installed, follow the `DevStack Installation
Instructions`_ to set one up.

*TODO verify it works like this*

Set up Devstack
```````````````
Run devstack

.. code-block:: bash

  git clone https://git.openstack.org/openstack-dev/devstack
  cd devstack
  ./stack.sh

Configure Nodepool for Devstack
````````````````````````````````
If you followed the README_, you may have created a
nodepool.yaml config file. If you haven't yet, then create one now. The default
location for this is ``/etc/nodepool/nodepool.yaml``. Add the sections detailed
below to your yaml file. The full sample yaml file is available at the end of
this section.

script-dir and elements-dir
'''''''''''''''''''''''''''

You will need to make and populate these two paths as necessary, cloning
nodepool does not do this. Further in this doc we have an example script you'll
need to copy to /path/to/nodepool/scripts.

.. code-block:: yaml

  script-dir: /etc/nodepool/scripts
  elements-dir: /etc/nodepool/elements

dburi
'''''

The mysql password here may be different depending on your devstack install. The
devstack environment variable is MYSQL_PASSWORD. If this is not set, and
devstack doesn't prompt you, try the admin password you used when you set up
devstack (see the `devstack config file`_).

.. code-block:: yaml

  dburi: 'mysql+pymysql://root:secretmysql@localhost/nodepool'

targets
'''''''

Need to have at least one target for node allocations, but this does not need to
be a jenkins target.

.. code-block:: yaml

  targets:
    - name: dummy

labels
'''''''

Devstack does not make an Ubuntu image by default. You can grab one from Ubuntu
and upload it yourself, per the instructions below. Devstack provides a cirrOs_
image, which is a minimal Linux distribution. Unfortunately, we cannot use
devstack's cirrOs_ default because cirrOs_ does not support sftp. See Images_ for
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
    * *TODO remove this, something is breaking my syntax highlighting*

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

Upload Image
`````````````

Once you've finished your nodepool.yaml config file, upload the ubuntu image to
glance:

*TODO what is glance, how did it get installed, where do i find out more?*

.. code-block:: bash

  wget https://cloud-images.ubuntu.com/trusty/current/trusty-server-cloudimg-amd64-disk1.img
  source /path/to/devstack/openrc
  glance image-create --name ubuntu --disk-format qcow2 --container-format bare --file trusty-server-cloudimg-amd64-disk1.img

Check that the image has been uploaded to devstack:

.. code-block:: bash

  *TODO*

*TODO where can i find out more about nodepool and images?*

prepare_node_ubuntu.sh
```````````````````````
*TODO better section title*

We also need to write out our prepare_node_ubuntu.sh script. Its job is
to give us a jenkins user that allows ssh using the ``$HOME/.ssh/id_rsa``
key.

*TODO I thought our config didn't communicate with jenkins??*

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

Open Ports
```````````

To allow connectivity from nodepool to its nodes we also need to open up
our default security group

.. code-block:: bash

  nova secgroup-add-rule default tcp 1 65535 0.0.0.0/0
  nova secgroup-add-rule default udp 1 65535 0.0.0.0/0

Note that this just opens up all the tcp and udp ports but your nodes
should run iptables if that matters anyways.

Run Nodepool With Devstack
```````````````````````````

Now you can run nodepool in the foreground against your devstack cloud::

  venv/bin/nodepoold -c /path/to/nodepool/things/nodepool.yaml -d

*TODO how do i know if it's working??*

Troubleshooting
````````````````

*TODO add troubleshooting tips*

Testing a Specific Patch
`````````````````````````

*TODO where in this document should this go??*

To test a specific patch that is already in gerrit, you will also
want to install git-review and apply that patch while in the nodepool
repository:

.. code-block:: bash

    cd ~/src/nodepool
    git review -x XXXXX

.. _README: *TODO*
.. _Images: *TODO*

.. _devstack config file: *TODO*
.. _DevStack Installation Instructions: *TODO*
