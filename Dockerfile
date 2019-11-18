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

FROM zuul/diskimage-builder as nodepool-base

COPY --from=builder /output/ /output
RUN /output/install-from-bindep

### Containers should NOT run as root as a good practice

# although this feels odd ... by default has group "shadow", meaning
# uid_entrypoint can't update it.  This is necessary for things like
# sudo to work.
RUN chown root:root /etc/shadow

RUN chmod g=u /etc/passwd /etc/shadow
ENV APP_ROOT=/var/lib/nodepool
ENV HOME=${APP_ROOT}
ENV USER_NAME=nodepool
RUN mkdir ${APP_ROOT}
RUN chown 10001:1001 ${APP_ROOT}
COPY tools/uid_entrypoint.sh /uid_entrypoint
ENTRYPOINT ["/uid_entrypoint"]

FROM nodepool-base as nodepool
USER 10001
CMD ["/usr/local/bin/nodepool"]

FROM nodepool-base as nodepool-launcher
USER 10001
CMD ["/usr/local/bin/nodepool-launcher", "-f"]

FROM nodepool-base as nodepool-builder
# dib needs sudo
RUN echo "nodepool ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nodepool-sudo \
  && chmod 0440 /etc/sudoers.d/nodepool-sudo
CMD ["/usr/local/bin/nodepool-builder", "-f"]
