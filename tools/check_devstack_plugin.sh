#!/bin/bash -x

function waitforimage {
    name=$1
    state='ready'

    while ! nodepool image-list | grep $name | grep $state; do
	nodepool image-list > /tmp/.nodepool-image-list.txt
	nodepool list > /tmp/.nodepool-list.txt
	mv /tmp/.nodepool-image-list.txt $WORKSPACE/logs/nodepool-image-list.txt
	mv /tmp/.nodepool-list.txt $WORKSPACE/logs/nodepool-list.txt
	sleep 10
    done
}

function waitfornode {
    name=$1
    state='ready'

    while ! nodepool list | grep $name | grep $state; do
	nodepool image-list > /tmp/.nodepool-image-list.txt
	nodepool list > /tmp/.nodepool-list.txt
	mv /tmp/.nodepool-image-list.txt $WORKSPACE/logs/nodepool-image-list.txt
	mv /tmp/.nodepool-list.txt $WORKSPACE/logs/nodepool-list.txt
	sleep 10
    done
}

# Check that snapshot image built
waitforimage trusty-server
# check that dib image built
waitforimage ubuntu-dib

# check snapshot image was bootable
waitfornode trusty-server
# check dib image was bootable
waitfornode ubuntu-dib
