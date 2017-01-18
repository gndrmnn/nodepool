#!/bin/bash -e

cd "$(dirname "$0")"

mkdir -p /tmp/nodepool/images
mkdir -p /tmp/nodepool/log

nodepool-builder -c nodepool.yaml -l builder-logging.conf --fake
nodepoold -c nodepool.yaml -s secure.conf -l launcher-logging.conf
