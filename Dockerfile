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

FROM opendevorg/python-builder as builder

ARG ZUUL_SIBLINGS=""
COPY . /tmp/src
RUN assemble

FROM opendevorg/python-base as nodepool-base

COPY --from=builder /output/ /output
RUN /output/install-from-bindep

### Containers should NOT run as root as a good practice
RUN useradd -u 10001 -m -d /var/lib/nodepool -c "Nodepool Daemon" nodepool

# although this feels odd ... by default has group "shadow", meaning
# uid_entrypoint can't update it.  This is necessary for things like
# sudo to work.
RUN chown root:root /etc/shadow

RUN chmod g=u /etc/passwd /etc/shadow
ENV APP_ROOT=/var/lib/nodepool
ENV HOME=${APP_ROOT}
ENV USER_NAME=nodepool
RUN chown 10001:1001 ${APP_ROOT}
COPY tools/uid_entrypoint.sh /uid_entrypoint
ENTRYPOINT ["/uid_entrypoint"]

FROM nodepool-base as nodepool
USER 10001
CMD ["/usr/local/bin/nodepool"]

FROM nodepool-base as nodepool-launcher
USER 10001
CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-launcher ${_DAEMON_FLAG}

FROM nodepool-base as nodepool-builder
# dib needs sudo
RUN echo "nodepool ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nodepool-sudo \
  && chmod 0440 /etc/sudoers.d/nodepool-sudo
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
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

USER 10001
CMD _DAEMON_FLAG=${DEBUG:+-d} && \
    _DAEMON_FLAG=${_DAEMON_FLAG:--f} && \
    /usr/local/bin/nodepool-builder ${_DAEMON_FLAG}
