#!/bin/bash

# Reboot a node with an image

# Copyright (C) 2011-2012 OpenStack LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

IMAGE=$1
IMAGENAME=`basename $IMAGE`

export DEBIAN_FRONTEND=noninteractive

apt-get --option "Dpkg::Options::=--force-confold" --assume-yes install qemu-utils
rm -rf /tmp/recover
mkdir -p /tmp/recover/ssh

mv $IMAGE /tmp/recover
cp /usr/local/bin/reboot-image.sh /tmp/recover/
modprobe nbd max_part=16
rm -rf /tmp/newimage
mkdir -p /tmp/newimage
qemu-nbd -c /dev/nbd1 /tmp/recover/$IMAGENAME

mount /dev/nbd1 /tmp/newimage
cp -a -t /tmp/recover /etc/mtab /etc/hosts
# Only copy ssh key types that the target image is configured for
for fname in /tmp/newimage/etc/ssh/* ; do
    cp -a /etc/ssh/`basename $fname` /tmp/recover/ssh
done

rsync -axHAXv /tmp/newimage/ / --exclude=/tmp --delete-after

# Restore saved system files
cp -a -t /etc /tmp/recover/*
mv /etc/$IMAGENAME /var/cache
mv /etc/reboot-image.sh /usr/local/bin

apt-get --option "Dpkg::Options::=--force-confold" --assume-yes install kexec-tools
sed -i /etc/default/kexec -e s/LOAD_KEXEC=false/LOAD_KEXEC=true/
kexec -l /vmlinuz --initrd=/initrd.img --append="`cat /proc/cmdline`"
nohup bash -c "sleep 2; kexec -e" </dev/null >/dev/null 2>&1 &
