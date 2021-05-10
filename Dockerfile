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

FROM docker.io/opendevorg/python-builder:3.7 as builder
# ============================================================================

ARG ZUUL_SIBLINGS=""
COPY . /tmp/src
RUN if [ `uname -m` = "aarch64" ] ; then \
      echo "Installing arm64 pip.conf" ; \
      cp /tmp/src/tools/pip.conf.arm64 /etc/pip.conf ; \
      cp /tmp/src/tools/pip.conf.arm64 /output/pip.conf ; \
    fi
RUN assemble

FROM docker.io/opendevorg/python-base:3.7 as nodepool-base
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
#    using -minimal elements on debuntu.  Two unmerged fixes:
#      https://salsa.debian.org/installer-team/debootstrap/-/merge_requests/26
#      https://salsa.debian.org/installer-team/debootstrap/-/merge_requests/27
#    are incoporated into the openstack-ci-core version

COPY tools/openstack-ci-core-ppa.asc /etc/apt/trusted.gpg.d/
COPY tools/kubic.asc /etc/apt/trusted.gpg.d/

RUN \
  echo "deb http://ppa.launchpad.net/openstack-ci-core/vhd-util/ubuntu focal main" >> /etc/apt/sources.list \
  && echo "deb http://ppa.launchpad.net/openstack-ci-core/debootstrap/ubuntu focal main" >> /etc/apt/sources.list \
  && apt-get update \
  && apt-get install -y \
      curl \
      debian-keyring \
      dosfstools \
      gdisk \
      git \
      kpartx \
      qemu-utils \
      ubuntu-keyring \
      vhd-util \
      debootstrap \
      procps \
      xz-utils \
      yum \
      yum-utils \
      zypper

# Podman install mainly for the "containerfile" elements of dib that
# build images from extracts of upstream containers.
# --install-recommends is important for getting
# containernetwork-plugins and other packages.  Current podman
# requires a later libseccomp2 only provided by backports.
#
# Podman defaults to trying to use systemd to do cgroup things (insert
# hand-wavy motion) but it's not in the container; override to use
# cgroupfs manager.  Also disable trying to send logs to the journal.
#
# Kernel may not support overlayfsmetacopy options (bionic?), need to
# turn that off for compatability.  See various error messages related
# to:
#   Error: error creating libpod runtime: failed to mount overlay for
#   metacopy check: invalid argument
RUN \
  echo "deb https://deb.debian.org/debian buster-backports main" >> /etc/apt/sources.list \
  && echo "deb https://download.opensuse.org/repositories/devel:/kubic:/libcontainers:/stable/Debian_10/ /" > "/etc/apt/sources.list.d/devel:kubic:libcontainers:stable.list" \
  && apt-get update \
  && apt-get install -y --install-recommends \
      libseccomp2/buster-backports \
      podman \
  && printf '[engine]\ncgroup_manager="cgroupfs"\nevents_logger="file"\n' > /etc/containers/containers.conf \
  && sed -i 's/,metacopy=on//g' /etc/containers/storage.conf

# Cleanup
RUN \
  apt-get clean \
  && rm -rf /var/lib/apt/lists/*

CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-builder ${_DAEMON_FLAG}
