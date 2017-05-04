#!/bin/bash -x

/opt/stack/new/nodepool/devstack/gate/check_devstack_plugin.sh
local ret=$?

/opt/stack/new/nodepool/devstack/gate/copy_logs.sh

exit $ret
