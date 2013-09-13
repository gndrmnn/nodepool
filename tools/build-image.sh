#!/bin/bash

set -e

export ELEMENTS_PATH=elements
export DIB_RELEASE=precise
disk-image-create -n -o devstack-gate-$DIB_RELEASE ubuntu infra-puppet openstack-repos
