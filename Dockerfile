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

COPY . /tmp/src
RUN assemble

FROM opendevorg/python-base as nodepool

COPY --from=builder /output/ /output
RUN /output/install-from-bindep

### Containers should NOT run as root as a good practice
RUN chmod g=u /etc/passwd
ENV APP_ROOT=/var/lib/nodepool
ENV HOME=${APP_ROOT}
ENV USER_NAME=nodepool
RUN mkdir ${APP_ROOT}
USER 10001
COPY tools/uid_entrypoint.sh /uid_entrypoint
ENTRYPOINT ["/uid_entrypoint"]

CMD ["/usr/local/bin/nodepool"]

FROM nodepool as nodepool-launcher
CMD ["/usr/local/bin/nodepool-launcher", "-f"]

FROM nodepool as nodepool-builder

USER root
RUN echo "nodepool ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nodepool-sudo \
  && chmod 0440 /etc/sudoers.d/nodepool-sudo
RUN \
  apt-get update \
  && apt-get install -y gnupg2 \
  && apt-key adv --keyserver keyserver.ubuntu.com --recv 2B5DE24F0EC9F98BD2F85CA315B6CE7C018D05F5 \
  && echo "deb http://ppa.launchpad.net/openstack-ci-core/vhd-util/ubuntu bionic main" >> /etc/apt/sources.list \
  && apt-get update \
  && apt-get install -y \
      debian-keyring \
      kpartx \
      qemu-utils \
      ubuntu-keyring \
      vhd-util \
      yum \
      yum-utils \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*
USER 10001

CMD ["/usr/local/bin/nodepool-builder", "-f"]
