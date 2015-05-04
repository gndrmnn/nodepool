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

Devstack
--------

You can also run your nodepool against an OpenStack deployed by
Devstack. This is handy because Devstack installs a mysql for us and
we can make some assumptions about the config required to talk to
a Devstack deployed cloud.

First run devstack::

  git clone https://git.openstack.org/openstack-dev/devstack
  cd devstack
  ./stack.sh

Install nodepool to a virtualenv::

  git clone https://git.openstack.org/openstack-infra/nodepool
  virtualenv venv
  venv/bin/pip install -U ./nodepool

Write a nodepool configuration file to
``/path/to/nodepool/things/nodepool.yaml``. A minimal nodepool.yaml
config file that does not communicate with Jenkins and Gearman would
look like::

  # You will need to make and populate these two paths as necessary,
  # cloning nodepool does not do this. Further in this doc we have an
  # example script for /path/to/nodepool/things/scripts.
  script-dir: /path/to/nodepool/things/scripts
  elements-dir: /path/to/nodepool/things/elements
  # The mysql password here may be different depending on your
  # devstack install, you should double check it (the devstack var
  # is MYSQL_PASSWORD and if unset devstack should prompt you for
  # the value).
  dburi: 'mysql+pymysql://root:secretmysql@localhost/nodepool'

  gearman-servers: []
  zmq-publishers: []
  # Need to have at least one target for node allocations, but
  # this does not need to be a jenkins target.
  targets:
    - name: dummy

  cron:
    cleanup: '*/1 * * * *'
    check: '*/15 * * * *'
    image-update: '14 14 * * *'

  # Devstack does not make an Ubuntu image by default. You can
  # grab one from Ubuntu and upload it yourself. Note that we
  # cannot use devstack's cirros default because cirros does not
  # support sftp.
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
      password: 'secretadmin'
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
          private-key: $HOME/.ssh/id_rsa

We need to upload the ubuntu image to glance::

  wget https://cloud-images.ubuntu.com/trusty/current/trusty-server-cloudimg-amd64-disk1.img
  source /path/to/devstack/openrc
  glance image-create --name ubuntu --disk-format qcow2 --container-format bare --file trusty-server-cloudimg-amd64-disk1.img

We also need to write out our prepare_node_ubuntu.sh script. Its job is
to give us a jenkins user that allows ssh using the ``$HOME/.ssh/id_rsa``
key::

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
  EOF
  chmod +x /path/to/nodepool/things/scripts/prepare_node_ubuntu.sh

To allow connectivity from nodepool to its nodes we also need to open up
our default security group::

  nova secgroup-add-rule default tcp 1 65535 0.0.0.0/0
  nova secgroup-add-rule default udp 1 65535 0.0.0.0/0

Note that this just opens up all the tcp and udp ports but your nodes
should run iptables if that matters anyways.

Last step before starting nodepool is to make sure the database it needs
exists in the MySQL server::

  mysql -u root -p -e "CREATE DATABASE nodepool;"

Now you can run nodepool in the foreground against your devstack cloud::

  venv/bin/nodepoold -c /path/to/nodepool/things/nodepool.yaml -d
