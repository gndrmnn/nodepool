#!/bin/bash -e

cd "$(dirname "$0")"

mkdir -p /tmp/nodepool/images
mkdir -p /tmp/nodepool/log

nodepool-builder -c `pwd`/nodepool.yaml -l `pwd`/builder-logging.conf -p /tmp/nodepool/builder.pid --fake
nodepoold -c `pwd`/nodepool.yaml -s secure.conf -l `pwd`/launcher-logging.conf -p /tmp/nodepool/launcher.pid
