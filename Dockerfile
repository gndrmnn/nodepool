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

FROM docker.io/opendevorg/python-builder as builder
# ============================================================================

ARG ZUUL_SIBLINGS=""
COPY . /tmp/src
RUN assemble

FROM docker.io/opendevorg/python-base as nodepool-base
# ============================================================================

COPY --from=builder /output/ /output
RUN /output/install-from-bindep

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

# binary deps; see
#  https://docs.openstack.org/diskimage-builder/latest/developer/vhd_creation.html
# about the vhd-util deps
RUN \
  apt-get update \
  && apt-get install -y gnupg2 \
  && apt-key adv --keyserver keyserver.ubuntu.com --recv 2B5DE24F0EC9F98BD2F85CA315B6CE7C018D05F5 \
  && echo "deb http://ppa.launchpad.net/openstack-ci-core/vhd-util/ubuntu bionic main" >> /etc/apt/sources.list \
  && apt-get update \
  && apt-get install -y \
      curl \
      debian-keyring \
      git \
      kpartx \
      qemu-utils \
      ubuntu-keyring \
      vhd-util \
      debootstrap \
      procps \
      yum \
      yum-utils \
      zypper \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-builder ${_DAEMON_FLAG}
