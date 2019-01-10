#!/bin/bash -xe

# This script will be run by OpenStack CI before unit tests are run,
# it sets up the test system as needed.
# Developers should setup their test systems in a similar way.

# This setup needs to be run as a user that can run sudo.

# Config Zookeeper to run on tmpfs
sudo service zookeeper stop
DATADIR=$(sed -n -e 's/^dataDir=//p' /etc/zookeeper/conf/zoo.cfg)
grep -q sasl /etc/zookeeper/conf/zoo.cfg || {
    (
        echo requireClientAuthScheme=sasl
        echo authProvider.1=org.apache.zookeeper.server.auth.SASLAuthenticationProvider
    ) | sudo tee -a /etc/zookeeper/conf/zoo.cfg
}
sudo mount -t tmpfs -o nodev,nosuid,size=500M none $DATADIR
sudo cp nodepool/tests/fixtures/zookeeper/auth.conf /etc/zookeeper

# Enable authentication
echo 'JVMFLAGS="-Djava.security.auth.login.config=/etc/zookeeper/auth.conf"' | \
    sudo tee -a /etc/default/zookeeper
echo 'JAVA_OPTS="-Djava.security.auth.login.config=/etc/zookeeper/auth.conf"' | \
    sudo tee -a /etc/default/zookeeper
sudo service zookeeper start
