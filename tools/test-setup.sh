#!/bin/bash

# This script will be run by OpenStack CI before unit tests are run,
# it sets up the test system as needed.
# Developers should setup their test systems in a similar way.

# This setup needs to be run as a user that can run docker or podman.

set -xeu

cd $(dirname $0)

# The zuul user on OpenDev's test nodes is not part of the docker group,
# so this script must be run as root.
sudo ./test-setup-docker.sh
