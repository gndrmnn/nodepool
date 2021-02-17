#!/bin/bash

# This script will be run by OpenStack CI before unit tests are run,
# it sets up the test system as needed.
# Developers should setup their test systems in a similar way.

# This setup needs to be run as a user that can run docker or podman.

set -xeu

cd $(dirname $0)
SCRIPT_DIR="$(pwd)"

# Select docker or podman
if command -v docker > /dev/null; then
  DOCKER="sudo docker"
  if ! docker ps; then
    sudo systemctl start docker
  fi
elif command -v podman > /dev/null; then
  DOCKER=podman
else
  echo "Please install docker or podman."
  exit 1
fi

# Select docker-compose or podman-compose
if command -v docker-compose > /dev/null; then
  COMPOSE="sudo docker-compose"
elif command -v podman-compose > /dev/null; then
  COMPOSE=podman-compose
else
  echo "Please install docker-compose or podman-compose."
  exit 1
fi

CA_DIR=$SCRIPT_DIR/ca

mkdir -p $CA_DIR
$SCRIPT_DIR/zk-ca.sh $CA_DIR nodepool-test-zookeeper

${COMPOSE} down

${COMPOSE} up -d

echo "Finished"
