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

FROM docker.io/opendevorg/python-builder:3.11-bookworm as builder
# ============================================================================

ARG ZUUL_SIBLINGS=""
COPY . /tmp/src
RUN assemble

FROM docker.io/opendevorg/python-base:3.11-bookworm as nodepool-base
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

COPY tools/openstack-ci-core-ppa.asc /etc/apt/trusted.gpg.d/

RUN \
  echo "deb http://ppa.launchpad.net/openstack-ci-core/vhd-util/ubuntu focal main" >> /etc/apt/sources.list \
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
      debootstrap

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
RUN apt-get install -y --install-recommends podman containernetworking-plugins uidmap libsemanage-common \
  && printf '[engine]\ncgroup_manager="cgroupfs"\nevents_logger="file"\n' > /etc/containers/containers.conf

# There is a Debian package for dnf-plugins-core but it breaks and replaces
# zypper which we also want to install. Prior to dnf-plugins-core existing
# in Debian we fetched the content we needed from github. Continue doing
# that but pin the version for compatibility with Debian's dnf.
# Until Debian fixes its dnf-plugins-core package in bookworm; manually
# install "dnf download" for the yum-minimal element. Note version 4.4.4
# is the last version compatible with bookworm's dnf package.
RUN \
  git clone --depth 1 --branch 4.4.4 https://github.com/rpm-software-management/dnf-plugins-core \
  && mkdir /usr/lib/python3/dist-packages/dnf-plugins \
  && cp -r dnf-plugins-core/plugins/dnfpluginscore /usr/lib/python3/dist-packages \
  && cp dnf-plugins-core/plugins/download.py /usr/lib/python3/dist-packages/dnf-plugins \
  && rm -rf dnf-plugins-core

# Cleanup
RUN \
  apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# NOTE(ianw) 2022-08-02 : move this into its own cgroup on cgroupsv2
# hosts for nested podman calls to work; see comments in
#  https://github.com/containers/podman/issues/14884
CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    if [ -e /sys/fs/cgroup/cgroup.controllers ]; then \
      sudo mkdir /sys/fs/cgroup/nodepool && \
      for p in `cat /sys/fs/cgroup/cgroup.procs`; do echo $p | sudo tee /sys/fs/cgroup/nodepool/cgroup.procs || true; done \
    fi; \
    /usr/local/bin/nodepool-builder ${_DAEMON_FLAG}
