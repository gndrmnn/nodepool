#!/bin/bash -ex

LOGDIR=/home/zuul/zuul-output/logs

# Set to indiciate an error return
RETURN=0
FAILURE_REASON=""

if [[ ${NODEPOOL_FUNCTIONAL_CHECK:-} == "installed" ]]; then
    NODEPOOL_INSTALL=${NODEPOOL_INSTALL:-~/.venv}
    NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
    NODEPOOL="$NODEPOOL_INSTALL/bin/nodepool -c $NODEPOOL_CONFIG"
elif [[ ${NODEPOOL_FUNCTIONAL_CHECK:-} == "containers" ]]; then
    NODEPOOL="docker exec nodepool_nodepool-launcher_1 nodepool"
else
    echo "Running in unknown environment!"
    exit 1
fi

cat > /tmp/ssh_wrapper <<EOF
#!/bin/bash -ex
sudo -H -u zuul ssh -o StrictHostKeyChecking=no -i $HOME/.ssh/id_nodepool root@\$@

EOF
sudo chmod 0755 /tmp/ssh_wrapper

function sshintonode {
    name=$1
    state='ready'

    node=`$NODEPOOL list | grep $name | grep $state | cut -d '|' -f6 | tr -d ' '`
    /tmp/ssh_wrapper $node ls /

    # Check that the root partition grew on boot; it should be a 5GiB
    # partition minus some space for the boot partition.  However
    # emperical evidence suggests there is some modulo maths going on,
    # (possibly with alignment?) that means we can vary up to even
    # 64MiB.  Thus we choose an expected value that gives us enough
    # slop to avoid false matches, but still indicates we resized up.
    root_size=$(/tmp/ssh_wrapper $node -- lsblk -rbno SIZE /dev/vda1)
    expected_root_size=$(( 5000000000 ))
    if [[ $root_size -lt $expected_root_size ]]; then
        echo "*** Root device does not appear to have grown: $root_size"
        FAILURE_REASON="Root partition of $name does not appear to have grown: $root_size < $expected_root_size"
        RETURN=1
    fi

    # Check we saw metadata deployed to the config-drive
    /tmp/ssh_wrapper $node \
        "dd status=none if=/dev/sr0 | tr -cd '[:print:]' | grep -q nodepool_devstack"
    if [[ $? -ne 0 ]]; then
        echo "*** Failed to find metadata in config-drive"
        FAILURE_REASON="Failed to find meta-data in config-drive for $node"
        RETURN=1
    fi
}

function checknm {
    name=$1
    state='ready'

    node=`$NODEPOOL list | grep $name | grep $state | cut -d '|' -f6 | tr -d ' '`
    nm_output=$(/tmp/ssh_wrapper $node -- nmcli c)

    # virtio device is eth0 on older, ens3 on newer
    if [[ ! ${nm_output} =~ (eth0|ens3) ]]; then
        echo "*** Failed to find interface in NetworkManager connections"
        /tmp/ssh_wrapper $node -- nmcli c
        /tmp/ssh_wrapper $node -- nmcli device
        FAILURE_REASON="Failed to find interface in NetworkManager connections"
        RETURN=1
    fi
}

function waitforimage {
    local name=$1
    local state='ready'
    local builds

    while ! $NODEPOOL image-list | grep $name | grep $state; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list --detail > ${LOGDIR}/nodepool-list.txt

        builds=$(ls -l /var/log/nodepool/builds/ | grep $name | wc -l)
        if [[ ${builds} -ge 4 ]]; then
            echo "*** Build of $name failed at least 3 times, aborting"
            exit 1
        fi
        sleep 10
    done
}

function waitfornode {
    name=$1
    state='ready'

    while ! $NODEPOOL list | grep $name | grep $state | grep "unlocked"; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list --detail > ${LOGDIR}/nodepool-list.txt
        sleep 10
    done
}

# check that image built
waitforimage test-image
# check image was bootable
waitfornode test-image
# check ssh for root user
sshintonode test-image
# networkmanager check
# TODO(jeblair): This should not run in all cases; in fact, most of
# this checking should move into the dib repo
#checknm test-image
# userdata check

set -o errexit
# Show the built nodes
$NODEPOOL list

# Try to delete the nodes that were just built
$NODEPOOL delete --now 0000000000

# show the deleted nodes (and their replacements may be building)
$NODEPOOL list

if [[ -n "${FAILURE_REASON}" ]]; then
    echo "${FAILURE_REASON}"
fi
exit $RETURN
