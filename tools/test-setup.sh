#!/bin/bash -xe

# This setup needs to be run as a user that can run sudo

DB_ROOT_PW=insecure_slave
DB_USER=openstack_citest
DB_PW=openstack_citest
sudo -H mysqladmin -u root password $DB_ROOT_PW
# note; we remove anonymous users first
sudo -H mysql -u root -p$DB_ROOT_PW -h localhost -e "
    DELETE FROM mysql.user WHERE User='';
    FLUSH PRIVILEGES;
    GRANT ALL PRIVILEGES ON *.*
        TO '$DB_USER'@'%' identified by '$DB_PW' WITH GRANT OPTION;"

mysql -u $DB_USER -p$DB_PW -h 127.0.0.1 -e "
    SET default_storage_engine=MYISAM;
    DROP DATABASE IF EXISTS {db_name};
    CREATE DATABASE {db_name} CHARACTER SET utf8;"
