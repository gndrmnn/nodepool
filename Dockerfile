# Copyright (c) 2019 Red Hat, Inc.
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
# See the License for the specific language governing permissions and
# limitations under the License.

FROM docker.io/opendevorg/python-builder:3.9-bullseye as builder
# ============================================================================

ARG ZUUL_SIBLINGS=""
COPY . /tmp/src
RUN if [ `uname -m` = "aarch64" ] ; then \
      echo "Installing arm64 pip.conf" ; \
      cp /tmp/src/tools/pip.conf.arm64 /etc/pip.conf ; \
      cp /tmp/src/tools/pip.conf.arm64 /output/pip.conf ; \
    fi
RUN assemble

FROM docker.io/opendevorg/python-base:3.9-bullseye as nodepool-base
# ============================================================================

COPY --from=builder /output/ /output
RUN if [ -f /output/pip.conf ] ; then \
      echo "Installing pip.conf from builder" ; \
      cp /output/pip.conf /etc/pip.conf ; \
    fi
RUN /output/install-from-bindep nodepool_base

RUN useradd -u 10001 -m -d /var/lib/nodepool -c "Nodepool Daemon" nodepool

FROM nodepool-base as nodepool
# ============================================================================

CMD ["/usr/local/bin/nodepool"]

FROM nodepool-base as nodepool-launcher
# ============================================================================

CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-launcher ${_DAEMON_FLAG}

FROM nodepool-base as nodepool-builder
# ============================================================================

# dib needs sudo
RUN echo "nodepool ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nodepool-sudo \
  && chmod 0440 /etc/sudoers.d/nodepool-sudo

# We have some out-of-tree of binary dependencies expressed below:
#
#  * vhd-util is required to create .vhd images, mostly used in
#    Rackspace.  For full details see:
#      https://docs.openstack.org/diskimage-builder/latest/developer/vhd_creation.html
#
#  * debootstrap unmounts /proc in the container causing havoc when
#    using -minimal elements on debuntu.  Two unmerged fixes are in the bullseye version:
#      https://salsa.debian.org/installer-team/debootstrap/-/merge_requests/26
#      https://salsa.debian.org/installer-team/debootstrap/-/merge_requests/27
#    are at least required.  We also need a later version to support jammy.  We
#    grab from unstable for this reason.

COPY tools/openstack-ci-core-ppa.asc /etc/apt/trusted.gpg.d/

RUN \
  echo "deb http://ppa.launchpad.net/openstack-ci-core/vhd-util/ubuntu focal main" >> /etc/apt/sources.list \
  && echo "deb http://deb.debian.org/debian unstable main" >> /etc/apt/sources.list \
  && echo "APT::Default-Release: 'stable';" >> /etc/apt/apt.conf.d/default-release \
  && apt-get update \
  && apt-get install -y \
      binutils \
      curl \
      dnf \
      debian-keyring \
      dosfstools \
      gdisk \
      git \
      kpartx \
      qemu-utils \
      vhd-util \
      procps \
      xz-utils \
      zypper \
      zstd \
      debootstrap/unstable

# Podman install mainly for the "containerfile" elements of dib that
# build images from extracts of upstream containers.
# --install-recommends is important for getting some reccommends like
# slirp4netns and uidmap that help with non-root usage -- by default
# our base container sets no-reccomends in apt config to keep package
# sizes down.
#
# Podman defaults to trying to use systemd to do cgroup things (insert
# hand-wavy motion) but it's not in the container; override to use
# cgroupfs manager.  Also disable trying to send logs to the journal.
#
# The glibc in Fedora >35 uses clone3() which causes seccomp issues.
# For details see:
#  https://bugs.debian.org/995777
# We install podman from unstable until these fixes make it into bullseye
#
# For some reason, the unstable podman only suggests iptables (not
# recommends) so we need to pull that explicitly too.  Unclear if
# this is a feature or a bug; see:
#  https://bugs.debian.org/997976
#
# We are getting errors like
#  level=warning msg="\"/\" is not a shared mount, this could cause issues or missing mounts with rootless containers"
#  Error: command required for rootless mode with multiple IDs: exec: "newuidmap": executable file not found in $PATH
# on the production hosts (but not in the gate?).  uidmap is a
# recommended package, but we need to explicitly pull in its
# unstable requirements or it seems like apt decides just not install it.
# The problem is uidmap -> libsubid4 -> libsemanage2 -> libsemanage-common
# and so unless we have the unstable version of libsemanage-common, it won't
# install.

RUN apt-get install -y --install-recommends podman/unstable iptables uidmap/unstable libsemanage-common/unstable \
  && printf '[engine]\ncgroup_manager="cgroupfs"\nevents_logger="file"\n' > /etc/containers/containers.conf

# There is a Debian package in the NEW queue currently for dnf-plugins-core
#  https://ftp-master.debian.org/new/dnf-plugins-core_4.0.21-1~exp1.html
# Until this is generally available; manually install "dnf download"
# for the yum-minimal element
RUN \
  git clone https://github.com/rpm-software-management/dnf-plugins-core \
  && mkdir /usr/lib/python3/dist-packages/dnf-plugins \
  && cp -r dnf-plugins-core/plugins/dnfpluginscore /usr/lib/python3/dist-packages \
  && cp dnf-plugins-core/plugins/download.py /usr/lib/python3/dist-packages/dnf-plugins \
  && rm -rf dnf-plugins-core

# Cleanup
RUN \
  apt-get clean \
  && rm -rf /var/lib/apt/lists/*

CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-builder ${_DAEMON_FLAG}
