#!/bin/bash -ex

# Sleep long enough for the below checks to have a chance
# at being completed.
sleep 15m

NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
NODEPOOL_SECURE=${NODEPOOL_SECURE:-/etc/nodepool/secure.conf}
NODEPOOL_CMD="nodepool -c $NODEPOOL_CONFIG -s $NODEPOOL_SECURE"
# Check that snapshot image built
# Print out the full details to help debugging errors
IMAGE_LIST=$($NODEPOOL_CMD image-list)
echo "$IMAGE_LIST" | grep ready | grep trusty-server
# check that dib image built
echo "$IMAGE_LIST" | grep ready | grep ubuntu-dib

# Print out the full details to help debugging errors
LIST=$($NODEPOOL_CMD list)
# check snapshot image was bootable
echo "$LIST" | grep ready | grep trusty-server
# check dib image was bootable
echo "$LIST" | grep ready | grep ubuntu-dib
