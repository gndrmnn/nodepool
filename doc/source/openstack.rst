.. _openstack-driver:

.. default-domain:: zuul

OpenStack Driver
----------------

Selecting the OpenStack driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr-overview::
   :prefix: providers.[openstack]
   :maxdepth: 3

.. attr:: providers.[openstack]

   Specifying the ``openstack`` driver for a provider adds the
   following keys to the :attr:`providers` configuration.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[openstack]`` to disambiguate from other
             drivers, but ``[openstack]`` is not required in the
             configuration (e.g. below ``providers.[openstack].cloud``
             refers to the ``cloud`` key in the ``providers`` section
             when the ``openstack`` driver is selected).

   An OpenStack provider's resources are partitioned into groups
   called "pools" (see :attr:`providers.[openstack].pools` for details),
   and within a pool, the node types which are to be made available
   are listed (see :attr:`providers.[openstack].pools.labels` for
   details).

   Within each OpenStack provider the available Nodepool image types
   are defined (see :attr:`providers.[openstack].diskimages`).

   .. code-block:: yaml

      providers:
        - name: provider1
          driver: openstack
          cloud: example
          region-name: 'region1'
          rate: 1.0
          boot-timeout: 120
          launch-timeout: 900
          launch-retries: 3
          image-name-format: '{image_name}-{timestamp}'
          hostname-format: '{label.name}-{provider.name}-{node.id}'
          post-upload-hook: /usr/bin/custom-hook
          diskimages:
            - name: trusty
              meta:
                  key: value
                  key2: value
            - name: precise
            - name: devstack-trusty
          pools:
            - name: main
              max-servers: 96
              availability-zones:
                - az1
              networks:
                - some-network-name
              security-groups:
                - zuul-security-group
              labels:
                - name: trusty
                  min-ram: 8192
                  diskimage: trusty
                  console-log: True
                - name: precise
                  min-ram: 8192
                  diskimage: precise
                - name: devstack-trusty
                  min-ram: 8192
                  diskimage: devstack-trusty
        - name: provider2
          driver: openstack
          cloud: example2
          region-name: 'region1'
          rate: 1.0
          image-name-format: '{image_name}-{timestamp}'
          hostname-format: '{label.name}-{provider.name}-{node.id}'
          diskimages:
            - name: precise
              meta:
                  key: value
                  key2: value
          pools:
            - name: main
              max-servers: 96
              labels:
                - name: trusty
                  min-ram: 8192
                  diskimage: trusty
                - name: precise
                  min-ram: 8192
                  diskimage: precise
                - name: devstack-trusty
                  min-ram: 8192
                  diskimage: devstack-trusty

   .. attr:: cloud
      :type: string
      :required:

      Name of a cloud configured in ``clouds.yaml``.

      The instances spawned by nodepool will inherit the default
      security group of the project specified in the cloud definition
      in `clouds.yaml` (if other values not specified). This means
      that when working with Zuul, for example, SSH traffic (TCP/22)
      must be allowed in the project's default security group for Zuul
      to be able to reach instances.

      More information about the contents of `clouds.yaml` can be
      found in `the openstacksdk documentation
      <https://docs.openstack.org/openstacksdk/>`_.

   .. attr:: boot-timeout
      :type: int seconds
      :default: 60

      Once an instance is active, how long to try connecting to the
      image via SSH.  If the timeout is exceeded, the node launch is
      aborted and the instance deleted.

   .. attr:: launch-timeout
      :type: int seconds
      :default: 3600

      The time to wait from issuing the command to create a new instance
      until that instance is reported as "active".  If the timeout is
      exceeded, the node launch is aborted and the instance deleted.

   .. attr:: nodepool-id
      :type: string
      :default: None

      *Deprecated*

      A unique string to identify which nodepool instances is using a
      provider.  This is useful if you want to configure production
      and development instances of nodepool but share the same
      provider.

  .. attr:: launch-retries
     :type: int
     :default: 3

      The number of times to retry launching a server before
      considering the job failed.

  .. attr:: region-name
     :type: string

     The region name if the provider cloud has multiple regions.

  .. attr:: hostname-format
     :type: string
     :default: {label.name}-{provider.name}-{node.id}

     Hostname template to use for the spawned instance.

  .. attr:: image-name-format
     :type: string
     :default: {image_name}-{timestamp}

     Format for image names that are uploaded to providers.

  .. attr:: post-upload-hook
     :type: string
     :default: None

     Filename of an optional script that can be called after an image has
     been uploaded to a provider but before it is taken into use. This is
     useful to perform last minute validation tests before an image is
     really used for build nodes. The script will be called as follows:

     ``<SCRIPT> <PROVIDER> <EXTERNAL_IMAGE_ID> <LOCAL_IMAGE_FILENAME>``

     If the script returns with result code 0 it is treated as successful
     otherwise it is treated as failed and the image gets deleted.

  .. attr:: rate
     :type: int seconds
     :default: 1

     In seconds, amount to wait between operations on the provider.

  .. attr:: clean-floating-ips
     :type: bool
     :default: True

     If it is set to True, nodepool will assume it is the only user of
     the OpenStack project and will attempt to clean unattached
     floating ips that may have leaked around restarts.

  .. attr:: port-cleanup-interval
     :type: int seconds
     :default: 600

     If greater than 0, nodepool will assume it is the only user of the
     OpenStack project and will attempt to clean ports in `DOWN` state after
     the cleanup interval has elapsed. This value can be reduced if the
     instance spawn time on the provider is reliably quicker.

  .. attr:: diskimages
     :type: list

     Each entry in a provider's `diskimages` section must correspond
     to an entry in :attr:`diskimages`.  Such an entry indicates that
     the corresponding diskimage should be uploaded for use in this
     provider.  Additionally, any nodes that are created using the
     uploaded image will have the associated attributes (such as
     flavor or metadata).

     If an image is removed from this section, any previously uploaded
     images will be deleted from the provider.

     .. code-block:: yaml

        diskimages:
          - name: precise
            pause: False
            meta:
                key: value
                key2: value
          - name: windows
            connection-type: winrm
            connection-port: 5986

     Each entry is a dictionary with the following keys

     .. attr:: name
        :type: string
        :required:

        Identifier to refer this image from
        :attr:`providers.[openstack].pools.labels` and
        :attr:`diskimages` sections.

     .. attr:: pause
        :type: bool
        :default: False

        When set to True, nodepool-builder will not upload the image
        to the provider.

     .. attr:: config-drive
        :type: bool
        :default: unset

        Whether config drive should be used for the image. Defaults to
        unset which will use the cloud's default behavior.

     .. attr:: meta
        :type: dict

        Arbitrary key/value metadata to store for this server using
        the Nova metadata service. A maximum of five entries is
        allowed, and both keys and values must be 255 characters or
        less.

     .. attr:: connection-type
        :type: string

        The connection type that a consumer should use when connecting
        to the node. For most diskimages this is not
        necessary. However when creating Windows images this could be
        ``winrm`` to enable access via ansible.

     .. attr:: connection-port
        :type: int
        :default: 22 / 5986

        The port that a consumer should use when connecting to the
        node. For most diskimages this is not necessary. This defaults
        to 22 for ssh and 5986 for winrm.

  .. attr:: cloud-images
     :type: list

     Each entry in this section must refer to an entry in the
     :attr:`labels` section.

     .. code-block:: yaml

        cloud-images:
          - name: trusty-external
            config-drive: False
          - name: windows-external
            connection-type: winrm
            connection-port: 5986

     Each entry is a dictionary with the following keys

     .. attr:: name
        :type: string
        :required:

        Identifier to refer this cloud-image from :attr:`labels`
        section.  Since this name appears elsewhere in the nodepool
        configuration file, you may want to use your own descriptive
        name here and use one of ``image-id`` or ``image-name`` to
        specify the cloud image so that if the image name or id
        changes on the cloud, the impact to your Nodepool
        configuration will be minimal.  However, if neither of those
        attributes are provided, this is also assumed to be the image
        name or ID in the cloud.

     .. attr:: config-drive
        :type: bool
        :default: unset

        Whether config drive should be used for the cloud
        image. Defaults to unset which will use the cloud's default
        behavior.

     .. attr:: image-id
        :type: str

        If this is provided, it is used to select the image from the
        cloud provider by ID, rather than name.  Mutually exclusive
        with :attr:`providers.[openstack].cloud-images.image-name`

     .. attr:: image-name
        :type: str

        If this is provided, it is used to select the image from the
        cloud provider by this name or ID.  Mutually exclusive with
        :attr:`providers.[openstack].cloud-images.image-id`

     .. attr:: username
        :type: str

        The username that a consumer should use when connecting to the
        node.

     .. attr:: python-path
        :type: str
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
        to set ``ansible_shell_type``. This setting should only be used

        - For a windows image with the experimental `connection-type`
          ``ssh``, in which case ``cmd`` or ``powershell`` should be set
          and reflect the node's ``DefaultShell`` configuration.
        - If the default shell is not Bourne compatible (sh), but instead
          e.g. ``csh`` or ``fish``, and the user is aware that there is a
          long-standing issue with ``ansible_shell_type`` in combination
          with ``become``

     .. attr:: connection-type
        :type: str

        The connection type that a consumer should use when connecting
        to the node. For most diskimages this is not
        necessary. However when creating Windows images this could be
        'winrm' to enable access via ansible.

     .. attr:: connection-port
        :type: int
        :default: 22/ 5986

        The port that a consumer should use when connecting to the
        node. For most diskimages this is not necessary. This defaults
        to 22 for ssh and 5986 for winrm.

  .. attr:: pools
     :type: list

     A pool defines a group of resources from an OpenStack
     provider. Each pool has a maximum number of nodes which can be
     launched from it, along with a number of cloud-related attributes
     used when launching nodes.

     .. code-block:: yaml

        pools:
          - name: main
            max-servers: 96
            availability-zones:
              - az1
            networks:
              - some-network-name
            security-groups:
              - zuul-security-group
            auto-floating-ip: False
            host-key-checking: True
            node-attributes:
              key1: value1
              key2: value2
            labels:
              - name: trusty
                min-ram: 8192
                diskimage: trusty
                console-log: True
              - name: precise
                min-ram: 8192
                diskimage: precise
              - name: devstack-trusty
                min-ram: 8192
                diskimage: devstack-trusty

     Each entry is a dictionary with the following keys

     .. attr:: name
        :type: string
        :required:

        Pool name

     .. attr:: node-attributes
        :type: dict

        A dictionary of key-value pairs that will be stored with the node data
        in ZooKeeper. The keys and values can be any arbitrary string.

     .. attr:: max-cores
        :type: int

        Maximum number of cores usable from this pool. This can be used
        to limit usage of the tenant. If not defined nodepool can use
        all cores up to the quota of the tenant.

     .. attr:: max-servers
        :type: int

        Maximum number of servers spawnable from this pool. This can
        be used to limit the number of servers. If not defined
        nodepool can create as many servers the tenant allows.

     .. attr:: max-ram
        :type: int

        Maximum ram usable from this pool. This can be used to limit
        the amount of ram allocated by nodepool. If not defined
        nodepool can use as much ram as the tenant allows.

     .. attr:: ignore-provider-quota
        :type: bool
        :default: False

        Ignore the provider quota for this pool. Instead, only check
        against the configured max values for this pool and the
        current usage based on stored data. This may be useful in
        circumstances where the provider is incorrectly calculating
        quota.

     .. attr:: availability-zones
        :type: list

        A list of availability zones to use.

        If this setting is omitted, nodepool will fetch the list of
        all availability zones from nova.  To restrict nodepool to a
        subset of availability zones, supply a list of availability
        zone names in this setting.

        Nodepool chooses an availability zone from the list at random
        when creating nodes but ensures that all nodes for a given
        request are placed in the same availability zone.

     .. attr:: networks
        :type: list

        Specify custom Neutron networks that get attached to each
        node. Specify the name or id of the network as a string.

     .. attr:: security-groups
        :type: list

        Specify custom Neutron security groups that get attached to
        each node. Specify the name or id of the security_group as a
        string.

     .. attr:: auto-floating-ip
        :type: bool
        :default: True

        Specify custom behavior of allocating floating ip for each
        node.  When set to False, ``nodepool-launcher`` will not apply
        floating ip for nodes. When zuul instances and nodes are
        deployed in the same internal private network, set the option
        to False to save floating ip for cloud provider.

     .. attr:: host-key-checking
        :type: bool
        :default: True

        Specify custom behavior of validation of SSH host keys.  When
        set to False, nodepool-launcher will not ssh-keyscan nodes
        after they are booted. This might be needed if
        nodepool-launcher and the nodes it launches are on different
        networks.  The default value is True.

     .. attr:: labels
        :type: list

        Each entry in a pool`s `labels` section indicates that the
        corresponding label is available for use in this pool.  When
        creating nodes for a label, the flavor-related attributes in that
        label's section will be used.

        .. code-block:: yaml

           labels:
             - name: precise
               min-ram: 8192
               flavor-name: 'something to match'
               console-log: True
             - name: trusty
               min-ram: 8192
               networks:
                 - public
                 - private

        Each entry is a dictionary with the following keys

        .. attr:: name
           :type: str
           :required:

           Identifier to refer this image; from :attr:`labels` and
           :attr:`diskimages` sections.

        .. attr:: diskimage
           :type: str
           :required:

           Refers to provider's diskimages, see
           :attr:`providers.[openstack].diskimages`.  Mutually exclusive
           with :attr:`providers.[openstack].pools.labels.cloud-image`

        .. attr:: cloud-image
           :type: str
           :required:

           Refers to the name of an externally managed image in the
           cloud that already exists on the provider. The value of
           ``cloud-image`` should match the ``name`` of a previously
           configured entry from the ``cloud-images`` section of the
           provider. See :attr:`providers.[openstack].cloud-images`.
           Mutually exclusive with
           :attr:`providers.[openstack].pools.labels.diskimage`

        .. attr:: flavor-name
           :type: str

           Name or id of the flavor to use. If
           :attr:`providers.[openstack].pools.labels.min-ram` is
           omitted, it must be an exact match. If
           :attr:`providers.[openstack].pools.labels.min-ram` is given,
           ``flavor-name`` will be used to find flavor names that meet
           :attr:`providers.[openstack].pools.labels.min-ram` and also
           contain ``flavor-name``.

           One of :attr:`providers.[openstack].pools.labels.min-ram` or
           :attr:`providers.[openstack].pools.labels.flavor-name` must
           be specified.

        .. attr:: min-ram
           :type: int

           Determine the flavor to use (e.g. ``m1.medium``,
           ``m1.large``, etc).  The smallest flavor that meets the
           ``min-ram`` requirements will be chosen.

           One of :attr:`providers.[openstack].pools.labels.min-ram` or
           :attr:`providers.[openstack].pools.labels.flavor-name` must
           be specified.

        .. attr:: boot-from-volume
           :type: bool
           :default: False

            If given, the label for use in this pool will create a
            volume from the image and boot the node from it.

        .. attr:: host-key-checking
           :type: bool
           :default: True

           Specify custom behavior of validation of SSH host keys.  When set to
           False, nodepool-launcher will not ssh-keyscan nodes after they are
           booted. This might be needed if nodepool-launcher and the nodes it
           launches are on different networks.  The default value is True.

           .. note:: This value will override the value for
                     :attr:`providers.[openstack].pools.host-key-checking`.

        .. attr:: networks
           :type: list

           Specify custom Neutron networks that get attached to each
           node. Specify the name or id of the network as a string.

           .. note:: This value will override the value for
                     :attr:`providers.[openstack].pools.networks`.

        .. attr:: key-name
           :type: string

           If given, is the name of a keypair that will be used when
           booting each server.

        .. attr:: console-log
           :type: bool
           :default: False

           On the failure of the ssh ready check, download the server
           console log to aid in debugging the problem.

        .. attr:: volume-size
           :type: int gigabytes
           :default: 50

           When booting an image from volume, how big should the
           created volume be.

        .. attr:: instance-properties
           :type: dict
           :default: None

           A dictionary of key/value properties to set when booting
           each server.  These properties become available via the
           ``meta-data`` on the active server (e.g. within
           ``config-drive:openstack/latest/meta_data.json``)

        .. attr:: userdata
           :type: str
           :default: None

           A string of userdata for a node. Example usage is to install
           cloud-init package on image which will apply the userdata.
           Additional info about options in cloud-config:
           https://cloudinit.readthedocs.io/en/latest/topics/examples.html

