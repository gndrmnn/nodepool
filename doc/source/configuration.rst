.. _configuration:

.. default-domain:: zuul

Configuration
=============

Nodepool reads its configuration from ``/etc/nodepool/nodepool.yaml``
by default.  The configuration file follows the standard YAML syntax
with a number of sections defined with top level keys.  For example, a
full configuration file may have the ``diskimages``, ``labels``,
and ``providers`` sections::

  diskimages:
    ...
  labels:
    ...
  providers:
    ...

The following drivers are available.

.. toctree::
   :maxdepth: 1

   aws
   azure
   gce
   kubernetes
   openshift
   openshift-pods
   openstack
   static

The following sections are available.  All are required unless
otherwise indicated.

.. attr-overview::
   :maxdepth: 1

Options
-------

.. attr:: webapp

   Define the webapp endpoint port and listen address

   .. attr:: port
      :default: 8005
      :type: int

      The port to provide basic status information

   .. attr:: listen_address
      :default: 0.0.0.0

      Listen address for web app

.. attr:: elements-dir
   :example: /path/to/elements/dir
   :type: str

   If an image is configured to use diskimage-builder and glance to locally
   create and upload images, then a collection of diskimage-builder elements
   must be present. The ``elements-dir`` parameter indicates a directory
   that holds one or more elements.

.. attr:: images-dir
   :example: /path/to/images/dir
   :type: str

   When we generate images using diskimage-builder they need to be
   written to somewhere. The ``images-dir`` parameter is the place to
   write them.

   .. note:: The builder daemon creates a UUID to uniquely identify
          itself and to mark image builds in ZooKeeper that it
          owns. This file will be named ``builder_id.txt`` and will
          live in the directory named by the :attr:`images-dir`
          option. If this file does not exist, it will be created on
          builder startup and a UUID will be created automatically.


.. attr:: build-log-dir
   :example: /path/to/log/dir
   :type: str

   The builder will store build logs in this directory.  It will create
   one file for each build, named `<image>-<build-id>.log`; for example,
   `fedora-0000000004.log`.  It defaults to ``/var/log/nodepool/builds``.

.. attr:: build-log-retention
   :default: 7
   :type: int

   At the start of each build, the builder will remove old build logs if
   they exceed this value.  This option specifies how many will be
   kept (usually you will see one more, as deletion happens before
   starting a new build).  By default, the last 7 old build logs are
   kept.  Set this to ``-1`` to disable removal of logs.

.. attr:: zookeeper-servers
   :type: list
   :required:

   Lists the ZooKeeper servers uses for coordinating information between
   nodepool workers.

   .. code-block:: yaml

      zookeeper-servers:
        - host: zk1.example.com
          port: 2181
          chroot: /nodepool

   Each entry is a dictionary with the following keys

   .. attr:: host
      :type: str
      :example: zk1.example.com
      :required:

      A zookeeper host

   .. attr:: port
      :default: 2181
      :type: int

      Port to talk to zookeeper

   .. attr:: chroot
      :type: str
      :example: /nodepool

      The ``chroot`` key, used for interpreting ZooKeeper paths
      relative to the supplied root path, is also optional and has no
      default.

.. attr:: zookeeper-tls
   :type: dict

   To use TLS connections with Zookeeper, provide this dictionary with
   the following keys:

   .. attr:: cert
      :type: string
      :required:

      The path to the PEM encoded certificate.

   .. attr:: key
      :type: string
      :required:

      The path to the PEM encoded key.

   .. attr:: ca
      :type: string
      :required:

      The path to the PEM encoded CA certificate.


.. attr:: labels
   :type: list

   Defines the types of nodes that should be created.  Jobs should be
   written to run on nodes of a certain label. Example

   .. code-block:: yaml

      labels:
        - name: my-precise
          max-ready-age: 3600
          min-ready: 2
        - name: multi-precise
          min-ready: 2

   Each entry is a dictionary with the following keys

   .. attr:: name
      :type: string
      :required:

      Unique name used to tie jobs to those instances.

   .. attr:: max-ready-age
      :type: int
      :default: 0

       Maximum number of seconds the node shall be in ready state. If
       this is exceeded the node will be deleted. A value of 0 disables
       this.

   .. attr:: min-ready
      :type: int
      :default: 0

      Minimum number of instances that should be in a ready
      state. Nodepool always creates more nodes as necessary in response
      to demand, but setting ``min-ready`` can speed processing by
      attempting to keep nodes on-hand and ready for immedate use.
      ``min-ready`` is best-effort based on available capacity and is
      not a guaranteed allocation.  The default of 0 means that nodepool
      will only create nodes of this label when there is demand.  Set
      to -1 to have the label considered disabled, so that no nodes will
      be created at all.

.. attr:: max-hold-age
   :type: int
   :default: 0

   Maximum number of seconds a node shall be in "hold" state. If this
   is exceeded the node will be deleted. A value of 0 disables this.

   This setting is applied to all nodes, regardless of label or
   provider.

.. attr:: diskimages
   :type: list

   This section lists the images to be built using
   diskimage-builder. The name of the diskimage is mapped to the
   :attr:`providers.[openstack].diskimages` section of the provider,
   to determine which providers should received uploads of each image.
   The diskimage will be built in every format required by the
   providers with which it is associated.  Because Nodepool needs to
   know which formats to build, if the diskimage will only be built if
   it appears in at least one provider.

   To remove a diskimage from the system entirely, remove all
   associated entries in :attr:`providers.[openstack].diskimages` and
   remove its entry from ``diskimages``.  All uploads will be deleted
   as well as the files on disk.

   A sample configuration section is illustrated below.

   .. code-block:: yaml

      diskimages:
        - name: base
          abstract: True
          elements:
            - vm
            - simple-init
            - openstack-repos
            - nodepool-base
            - cache-devstack
            - cache-bindep
            - growroot
            - infra-package-needs
          env-vars:
            TMPDIR: /opt/dib_tmp
            DIB_CHECKSUM: '1'
            DIB_IMAGE_CACHE: /opt/dib_cache

        - name: ubuntu-bionic
          parent: base
          pause: False
          rebuild-age: 86400
          elements:
            - ubuntu-minimal
          release: bionic
          username: zuul
          env-vars:
            DIB_APT_LOCAL_CACHE: '0'
            DIB_DISABLE_APT_CLEANUP: '1'
            FS_TYPE: ext3

        - name: ubuntu-focal
          base: ubuntu-bionic
          release: focal
          env-vars:
            DIB_DISABLE_APT_CLEANUP: '0'

        - name: centos-8
          parent: base
          pause: True
          rebuild-age: 86400
          formats:
            - raw
            - tar
          elements:
            - centos-minimal
            - epel
          release: '8'
          username: centos
          env-vars:
            FS_TYPE: xfs

   Each entry is a dictionary with the following keys

   .. attr:: name
      :type: string
      :required:

      Identifier to reference the disk image in
      :attr:`providers.[openstack].diskimages` and :attr:`labels`.

   .. attr:: abstract
      :type: bool
      :default: False

      An ``abstract`` entry is used to group common configuration
      together, but will not create any actual image.  A ``diskimage``
      marked as ``abstract`` should be inherited from in another
      ``diskimage`` via its :attr:`diskimages.parent` attribute.

      An `abstract` entry can have a :attr:`diskimages.parent`
      attribute as well; values will merge down.

   .. attr:: parent
      :type: str

      A parent ``diskimage`` entry to inherit from.  Any values from the
      parent will be populated into this image.  Setting any fields in
      the current image will override the parent values execept for
      the following:

      * :attr:`diskimages.env-vars`: new keys are additive, any
        existing keys from the parent will be overwritten by values in
        the current diskimage (i.e. Python `update()` semantics for a
        dictionary).
      * :attr:`diskimages.elements`: values are additive; the list of
        elements from the parent will be extended with any values in
        the current diskimage.  Note that the element list passed to
        `diskimage-builder` is not ordered; elements specify their own
        dependencies and `diskimage-builder` builds a graph from that,
        not the command-line order.

      Note that a parent ``diskimage`` may also have it's own parent,
      creating a chain of inheritance.  See also
      :attr:`diskimages.abstract` for defining common configuration
      that does not create a diskimage.

   .. attr:: formats
      :type: list

      The list of formats to build is normally automatically created
      based on the needs of the providers to which the image is
      uploaded.  To build images even when no providers are configured
      or to build additional formats which you know you may need in the
      future, list those formats here.

      In case the diskimage is not used by any provider and no formats
      are configured, the image won't be built.

   .. attr:: rebuild-age
      :type: int
      :default: 86400

      If the current diskimage is older than this value (in seconds),
      then nodepool will attempt to rebuild it.  Defaults to 86400 (24
      hours).

   .. attr:: release
      :type: string

      Specifies the distro to be used as a base image to build the image using
      diskimage-builder.

   .. attr:: build-timeout
      :type: int

      How long (in seconds) to wait for the diskimage build before giving up.
      The default is 8 hours.

   .. attr:: elements
      :type: list

      Enumerates all the elements that will be included when building the image,
      and will point to the :attr:`elements-dir` path referenced in the same
      config file.

   .. attr:: env-vars
      :type: dict

      Arbitrary environment variables that will be available in the spawned
      diskimage-builder child process.

   .. attr:: pause
      :type: bool
      :default: False

      When set to True, ``nodepool-builder`` will not build the diskimage.

   .. attr:: username
      :type: string
      :default: zuul

      The username that a consumer should use when connecting to the
      node.

   .. attr:: python-path
      :type: string
      :default: auto

      The path of the default python interpreter.  Used by Zuul to set
      ``ansible_python_interpreter``.  The special value ``auto`` will
      direct Zuul to use inbuilt Ansible logic to select the
      interpreter on Ansible >=2.8, and default to
      ``/usr/bin/python2`` for earlier versions.

   .. attr:: shell-type
      :type: str
      :default: sh

      The shell type of the node's default shell executable. Used by Zuul
      to set ``ansible_shell_type``.  This setting should not be used
      unless the default shell is a non-Bourne (sh) compatible shell, e.g.
      ``csh`` or ``fish``. For a windows image with the experimental
      `connection-type` ``ssh``, ``cmd`` or ``powershell`` should be set
      and reflect the node's ``DefaultShell`` configuration.

   .. attr:: dib-cmd
      :type: string
      :default: disk-image-create

      Configure the command called to create this disk image.  By
      default this just ``disk-image-create``; i.e. it will use the
      first match in ``$PATH``.  For example, you may want to override
      this with a fully qualified path to an alternative executable if
      a custom ``diskimage-builder`` is installed in another
      virutalenv.

      .. note:: Any wrapping scripts or similar should consider that
                the command-line or environment arguments to
                ``disk-image-create`` are not considered an API and
                may change.

.. attr:: providers
   :type: list

   Lists the providers Nodepool should use. Each provider is associated to
   a driver listed below.

   Each entry is a dictionary with the following keys

   .. attr:: name
      :type: string
      :required:

      Name of the provider

   .. attr:: max-concurrency
      :type: int
      :default: 0

      Maximum number of node requests that this provider is allowed to
      handle concurrently. The default, if not specified, is to have
      no maximum. Since each node request is handled by a separate
      thread, this can be useful for limiting the number of threads
      used by the nodepool-launcher daemon.

   .. attr:: driver
      :type: string
      :default: openstack

      The driver type.

      .. value:: aws

         For details on the extra options required and provided by the
         AWS driver, see the separate section
         :ref:`aws-driver`

      .. value:: azure

         For details on the extra options required and provided by the
         Azure driver, see the separate section
         :ref:`azure-driver`

      .. value:: gce

         For details on the extra options required and provided by the
         GCE driver, see the separate section
         :ref:`gce-driver`

      .. value:: kubernetes

         For details on the extra options required and provided by the
         kubernetes driver, see the separate section
         :ref:`kubernetes-driver`

      .. value:: openshift

         For details on the extra options required and provided by the
         openshift driver, see the separate section
         :ref:`openshift-driver`

      .. value:: openshiftpods

         For details on the extra options required and provided by the
         openshiftpods driver, see the separate section
         :ref:`openshift-pods-driver`

      .. value:: openstack

         For details on the extra options required and provided by the
         OpenStack driver, see the separate section
         :ref:`openstack-driver`

      .. value:: static

         For details on the extra options required and provided by the
         static driver, see the separate section
         :ref:`static-driver`
