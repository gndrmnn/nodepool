#!/bin/bash

# This runs ZooKeeper in a docker container, which is required for
# tests.

# This setup needs to be run as a user that can run docker or podman, or by
# setting $ROOTCMD to a user substitution tool like "sudo" in the calling
# environment.

set -xeu

# Default ROOTCMD to the 'env' command, otherwise variable assignments will be
# interpreted as command when no ROOTCMD is given. The reason for that is
# Bash's simple command expansion.
ROOTCMD=${ROOTCMD:-env}

cd $(dirname $0)
SCRIPT_DIR="$(pwd)"

# Select docker or podman
if command -v docker > /dev/null; then
  DOCKER=docker
  if ! ${ROOTCMD} docker ps; then
    ${ROOTCMD} systemctl start docker
  fi
elif command -v podman > /dev/null; then
  DOCKER=podman
else
  echo "Please install docker or podman."
  exit 1
fi

# Select docker-compose or podman-compose
if command -v docker-compose > /dev/null; then
  COMPOSE=docker-compose
elif command -v podman-compose > /dev/null; then
  COMPOSE=podman-compose
else
  echo "Please install docker-compose or podman-compose."
  exit 1
fi

CA_DIR=$SCRIPT_DIR/ca

mkdir -p $CA_DIR
$SCRIPT_DIR/zk-ca.sh $CA_DIR nodepool-test-zookeeper

${ROOTCMD} ${COMPOSE} down

${ROOTCMD} ${COMPOSE} up -d

echo "Finished"
