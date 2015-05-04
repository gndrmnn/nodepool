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

  mysql -e "GRANT ALL ON *.* TO 'openstack_citest'@'localhost';"

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

Write a nodepool configuration file to ``$NODEPOOL_ROOT/nodepool.yaml``.
A minimal nodepool.yaml config file that does not communicate with
Jenkins and Gearman would look like::

  script-dir: $NODEPOOL_ROOT/scripts
  elements-dir: $NODEPOOL_ROOT/elements
  # The mysql password here may be different depending on your
  # devstack install, you should double check it.
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
          min-ram: 512
          # This script should setup the jenkins user to accept
          # the ssh key configured below.
          setup: prepare_node_ubuntu.sh
          username: jenkins
          private-key: $HOME/.ssh/id_rsa

Now you can run nodepool in the foreground against your devstack cloud::

  venv/bin/nodepoold -c $NODEPOOL_ROOT/nodepool.yaml -d
