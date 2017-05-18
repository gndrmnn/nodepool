FROM alpine:3.5

ENV DEV_PACKAGES=" \
                 alpine-sdk \
                 gcc \
                 gmp-dev \
                 libffi-dev \
                 linux-headers \
                 musl-dev \
                 openssl-dev \
                 python-dev \
                 "

VOLUME /var/log/nodepool
VOLUME /etc/nodepool
VOLUME /etc/openstack
VOLUME /opt/setup_scripts

RUN \
    apk add --update --no-cache \
    bash \
    ca-certificates \
    coreutils \
    curl \
    device-mapper \
    e2fsprogs \
    e2fsprogs-extra \
    findutils \
    git \
    gmp \
    libffi \
    libzmq \
    multipath-tools \
    openssh \
    openssl \
    parted \
    python \
    py-pip \
    py-virtualenv \
    py-yaml \
    qemu-img \
    rsync \
    sudo \
    tar \
    udev \
    util-linux


### diskimage builder ###

RUN virtualenv /opt/diskimage-builder

RUN \
    cd /tmp && \
    git clone https://github.com/openstack/dib-utils.git && \
    cd dib-utils && \
    git checkout 0.0.11 && \
    /opt/diskimage-builder/bin/pip install .


RUN ln -s /opt/diskimage-builder/bin/dib-run-parts /usr/bin/

RUN \
    cd /tmp && \
    git clone https://github.com/openstack/diskimage-builder.git && \
    cd diskimage-builder && \
    git checkout 1.26.1 && \
    /opt/diskimage-builder/bin/pip install .

RUN ln -s /opt/diskimage-builder/bin/disk-image-create /usr/bin/


### requirements ###

# For faster development cycle the dependencies are installed separately from
# nodepool itself. This way these layers can be taken from the docker cache if
# there are only changes to the nodepool source.
COPY requirements.txt /tmp/

RUN \
    apk add --update --no-cache $DEV_PACKAGES && \
    virtualenv /opt/nodepool && \
    /opt/nodepool/bin/pip install -U -r /tmp/requirements.txt && \
    # install logstash_formatter to support structured json logging
    /opt/nodepool/bin/pip install logstash_formatter && \
    apk del --purge $DEV_PACKAGES

# add tini for zombie reaping as nodepool also launches further processes
# refer to https://blog.phusion.nl/2015/01/20/docker-and-the-pid-1-zombie-reaping-problem/
RUN apk add --update --no-cache tini


### nodepool ###

RUN mkdir /opt/nodepool-source
COPY . /opt/nodepool-source

RUN \
    cd /opt/nodepool-source && \
    git describe && \
    /opt/nodepool/bin/pip install -e .


RUN ln -s /opt/nodepool/bin/nodepool* /usr/bin/

ENTRYPOINT ["/sbin/tini", "-g", "--"]
