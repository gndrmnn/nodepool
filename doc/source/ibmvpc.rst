.. _ibmvpc-driver:

.. default-domain:: zuul

IBM VPC Driver
--------------

This driver is for use with the Virtual Private Cloud (VPC) service of
IBM Cloud.

Preparation
~~~~~~~~~~~
Note the following caveats:

* IBM Cloud VPC does not support IPv6 addressing.

* IBM Cloud VPC does not supply quota information via its API (but if
  you know the values, you can supply them in the Nodepool
  configuration).

* Per-instance floating IP addresses are required for in-bound
  connections from the Internet (but are not required for
  outbound-only access).  As of the time of this writing, floating IP
  addresses are billed monthly and are not pro-rated.  Because
  Nodepool allocates and deallocates a floating IP address for each
  instance, this means that every instance will incur at least a
  full-month's charge for floating IP use even if it is used only
  briefly.

* IBM Cloud does not provide a facility for atomically associating
  metadata with instances, which Nodepool generally relies upon for
  detecting and cleaning up leaked resources.  To approximate this
  functionality, the `ibmvpc` driver will create all instances and
  associated resources within resource groups that it will expect to
  control.  These groups begin with either the string ``np_`` or
  ``npimages_``.  Do not create any resource groups matching these
  names, and do not create any resources within these groups.  To do
  so runs the risk of Nodepool mistaking your resource for one that it
  controls and automatically deleting it.

Before using this driver, attend to the following pre-requisites:

* `Create an SSH key <https://cloud.ibm.com/vpc-ext/compute/sshKeys>`_
* `Create a VPC and subnet <https://cloud.ibm.com/vpc-ext/provision/vpc>`_

Authentication
~~~~~~~~~~~~~~

Several authentication methods are available.  The one which will work
in the widest circumstances is using an API key.  The console can be
used to `create an API key <https://cloud.ibm.com/iam/apikeys>`_.

The API key (or other authentication information) may be provided to
Nodepool either through environment variables, or stored in a file.
If a file is used, the content is exactly the same as the environment
variable definition.  To use an API key, the content would be:

.. code-block:: shell

   VPC_AUTH_TYPE=iam
   VPC_APIKEY=api-key-goes-here

Either set these environment variables when running the Nodepool
process, or write them to a file and specify the path with the
:attr:`providers.[ibmvpc].credentials-file` provider attribute.

.. note::

   Several other authentication types are available; their
   configuration is outside the scope of this document.  While
   `nodepool-launcher` should work with any auth type supported by the
   IBM Cloud Python SDK, `nodepool-builder` is only compatible with
   the ``iam`` auth type.

Image Building
~~~~~~~~~~~~~~

Custom images in IBM cloud require the use of `Cloud Object Storage`
which is not integrated with the VPC API.  To use `nodepool-builder`, the following additional steps are required:

`Create a Cloud Object Storage instance
<https://cloud.ibm.com/docs/vpc?topic=vpc-object-storage-prereq&interface=cli>`_
and grant access to the VPC service.

Create a bucket within the `Cloud Object Storage` instance.

Select an `endpoint <https://cloud.ibm.com/docs/cloud-object-storage?topic=cloud-object-storage-endpoints>`_
for use with Nodepool.

Note the name of the `Cloud Object Storage` instance, the endpoint,
and the name of the bucket you created for use when configuring
Nodepool.

Configuration
~~~~~~~~~~~~~

Selecting the ibmvpc driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[ibmvpc]
   :maxdepth: 3

.. attr:: providers.[ibmvpc]
   :type: list

   A provider's resources are partitioned into groups called `pools`,
   and within a pool, the node types which are to be made available
   are listed.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[ibmvpc]`` to disambiguate from other
             drivers, but ``[ibmvpc]`` is not required in the
             configuration (e.g. below
             ``providers.[ibmvpc].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``ibmvpc``
             driver is selected).

   Example:

   .. code-block:: yaml

      providers:
        - name: ibmvpc-us-south
          driver: ibmvpc
          credentials-file: /path/to/creds.env
          vpc: nodepool
          region: us-south
          subnet: sn-nodepool
          cloud-images:
            - name: bionic
              username: zuul
              keys:
                - zuul-ssh-key
              image-name: ibm-ubuntu-20-04-3-minimal-amd64-1
          pools:
            - name: main
              zone: us-south-1
              max-servers: 10
              host-key-checking: false
              labels:
                - name: bionic
                  cloud-image: bionic
                  profile: cx2-2x4

   .. attr:: name
      :required:

      A unique name for this provider configuration.  The name may not
      contain underscores.

   .. attr:: vpc
      :required:

      The name of the VPC for this provider.

   .. attr:: region
      :required:

      Name of the IBM Cloud region to interact with (e.g., ``us-south``).

   .. attr:: zone

      Name of the IBM Cloud region zone to interact with (e.g.,
      ``us-south-1``).  This field may be omitted if
      :attr:`providers.[ibmvpc].pools.zone` is supplied.

   .. attr:: credentials-file

      Path to the file containing the authentication information.
      Required unless the information is supplied via environment
      variables.

   .. attr:: subnet

      The name of the network upon which to create VMs.  This field
      may be omitted if :attr:`providers.[ibmvpc].pools.subnet` is
      supplied.

   .. attr:: public-ipv4
      :type: bool
      :default: false

      Whether to attach a public IPv4 address to instances.

      .. warning::

         As of the time of this writing, floating IP addresses are
         billed monthly and are not pro-rated.  Enabling this option
         can lead to high usage charges.

   .. attr:: use-internal-ip
      :type: bool
      :default: false

      If a public IP is attached but Nodepool should prefer the
      private IP, set this to true.

   .. attr:: host-key-checking
      :type: bool
      :default: true

      Whether to validate SSH host keys.  When true, this helps ensure
      that nodes are ready to receive SSH connections before they are
      supplied to the requestor.  When set to false, nodepool-launcher
      will not attempt to ssh-keyscan nodes after they are booted.
      Disable this if nodepool-launcher and the nodes it launches are
      on different networks, where the launcher is unable to reach the
      nodes directly, or when using Nodepool with non-SSH node
      platforms.  The default value is true.

   .. attr:: rate
      :type: float seconds
      :default: 1.0

      In seconds, amount to wait between operations on the provider.

   .. attr:: boot-timeout
      :type: int seconds
      :default: 120

      Once an instance is active, how long to try connecting to the
      image via SSH.  If the timeout is exceeded, the node launch is
      aborted and the instance deleted.

   .. attr:: launch-timeout
      :type: int seconds
      :default: 3600

      The time to wait from issuing the command to create a new instance
      until that instance is reported as "active".  If the timeout is
      exceeded, the node launch is aborted and the instance deleted.

   .. attr:: launch-retries
      :type: int
      :default: 3

      The number of times to retry launching a server before
      considering the request failed.

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

   .. attr:: object-storage

      In order to use `nodepool-builder`, the information in this
      attribute must be supplied.  The builder will upload images to
      the specified bucket during the image creation process, and
      immediately remove them.  Long-term storage is not required.

      .. attr:: instance-name
         :required:

         The name of the `Cloud Object Storage` instance.

      .. attr:: endpoint

         The `endpoint
         <https://cloud.ibm.com/docs/cloud-object-storage?topic=cloud-object-storage-endpoints>`_
         URL that Nodepool should use.

      .. attr:: bucket-name

         The bucket in which to store image files.

   .. attr:: quota

      The IBM Cloud VPC API does not provide quota information, so
      Nodepool is unable to estimate how much quota is available
      before creating instances.  This may lead to a high failure rate.

      If you know the quota values, you may supply them here.

      .. attr:: instances
         :type: int

         The number of instances available in this region.

      .. attr:: cores
         :type: int

         The number of vCPU cores available in this region.

      .. attr:: ram
         :type: int

         The amount of RAM available in this region (in mebibytes).

   .. attr:: cloud-images
      :type: list

      Each entry in this section must refer to an entry in the
      :attr:`labels` section.

      .. code-block:: yaml

         cloud-images:
           - name: bionic
             username: zuul
             keys:
               - zuul-ssh-key
             image-name: ibm-ubuntu-20-04-3-minimal-amd64-1
           - name: stretch
             username: zuul
             keys:
               - zuul-ssh-key
             image-filter:
               operating-system:
                 family: "Debian GNU/Linux"
                 version: "9.x Stretch/Stable - Minimal Install"

      Each entry is a dictionary with the following keys

      .. attr:: name
         :type: string
         :required:

         Identifier to refer this cloud-image from :attr:`labels`
         section.  Since this name appears elsewhere in the nodepool
         configuration file, you may want to use your own descriptive
         name here.

      .. attr:: username
         :type: str
         :required:

         The username that should be used when connecting to the node.

      .. attr:: keys
         :type: list

         A list of SSH keys (specified by name) to install on the node.

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

         The shell type of the node's default shell executable. Used by Zuul
         to set ``ansible_shell_type``. This setting should only be used

         - For a windows image with the experimental `connection-type` ``ssh``
           in which case ``cmd`` or ``powershell`` should be set
           and reflect the node's ``DefaultShell`` configuration.
         - If the default shell is not Bourne compatible (sh), but instead
           e.g. ``csh`` or ``fish``, and the user is aware that there is a
           long-standing issue with ``ansible_shell_type`` in combination
           with ``become``

      .. attr:: image-filter
         :type: dict

         Specifies a private image to use via filters.  Either this field,
         :attr:`providers.[ibmvpc].cloud-images.image-name`,
         :attr:`providers.[ibmvpc].cloud-images.image-href`, or
         :attr:`providers.[ibmvpc].cloud-images.image-id` must be
         provided.

         If a filter is provided, Nodepool will list all of the images
         in the provider and reduce the list using the supplied
         filter.  All items specified in the filter must match in
         order for an image to match.  If more than one image matches,
         the images are sorted by creation time and the last one
         matches.

         The following filters are available:

         .. attr:: operating-system

            This is a dictionary with any of the following keys:

            .. attr:: architecture
               :type: str

               The architecture (e.g., ``amd64``).

            .. attr:: dedicated-host-only
               :type: bool

               Whether the image requires a dedicated host.

            .. attr:: display-name
               :type: str

               The display name (e.g., ``Debian GNU/Linux 9.x
               Stretch/Stable - Minimal Install (amd64)``)

            .. attr:: family
               :type: str

               The OS family (e.g., ``Debian GNU/Linux``).

            .. attr:: href
               :type: str

               The URL for the operating system record in IBM Cloud VPC (e.g.,
               ``https://us-south.iaas.cloud.ibm.com/v1/operating_systems/debian-9-amd64``).

            .. attr:: name
               :type: str

               The name of the operating system record in IBM Cloud
               VPC (e.g., ``debian-9-amd64``).

            .. attr:: vendor
               :type: str

               The vendor (e.g., ``Debian``).

            .. attr:: version
               :type: str

               The version (e.g., ``9.x Stretch/Stable - Minimal Install``).

         .. attr:: owner-type
            :type: str

            Whether the image is provided by the cloud (``provider``)
            or the user (``user``).

         .. attr:: status
            :type: str

            The status of the image (e.g., ``available``).

      .. attr:: image-id
         :type: str

         Specifies a private image to use by ID.  Either this field,
         :attr:`providers.[ibmvpc].cloud-images.image-name`,
         :attr:`providers.[ibmvpc].cloud-images.image-href`, or
         :attr:`providers.[ibmvpc].cloud-images.image-filter` must be
         provided.

      .. attr:: image-href
         :type: dict

         Specifies a public image to use by href.  Either this field,
         :attr:`providers.[ibmvpc].cloud-images.image-name`,
         :attr:`providers.[ibmvpc].cloud-images.image-id`, or
         :attr:`providers.[ibmvpc].cloud-images.image-filter` must be
         provided.

      .. attr:: image-name
         :type: dict

         Specifies a public image to use by name.  Either this field,
         :attr:`providers.[ibmvpc].cloud-images.image-href`,
         :attr:`providers.[ibmvpc].cloud-images.image-id`, or
         :attr:`providers.[ibmvpc].cloud-images.image-filter` must be
         provided.

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

      Each entry is a dictionary with the following keys

      .. attr:: name
         :type: str
         :required:

         Identifier to refer this image from
         :attr:`providers.[ibmvpc].pools.labels` and
         :attr:`diskimages` sections.

      .. attr:: operating-system
         :type: str
         :required:

         The name of the IBM Cloud VPC operating-system record for
         this image.  IBM Cloud VPC requires that custom images
         correspond with one of the pre-defined operating systems.  If
         your exact OS isn't available, select the closest technology
         match.  E.g., ``debian-9-amd64``.

      .. attr:: pause
         :type: bool
         :default: False

         When set to True, nodepool-builder will not upload the image
         to the provider.

      .. attr:: key
         :type: str

         The SSH public key that should be installed on the node.

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

      .. attr:: python-path
         :type: str
         :default: auto

         The path of the default python interpreter.  Used by Zuul to set
         ``ansible_python_interpreter``.  The special value ``auto`` will
         direct Zuul to use inbuilt Ansible logic to select the
         interpreter on Ansible >=2.8, and default to
         ``/usr/bin/python2`` for earlier versions.

   .. attr:: pools
       :type: list

       A pool defines a group of resources from a provider. Each pool
       has a maximum number of nodes which can be launched from it,
       along with a number of cloud-related attributes used when
       launching nodes.

       .. attr:: name
          :required:

          A unique name within the provider for this pool of
          resources.  The name may not contain underscores.

       .. attr:: priority
          :type: int
          :default: 100

          The priority of this provider pool (a lesser number is a higher
          priority).  Nodepool launchers will yield requests to other
          provider pools with a higher priority as long as they are not
          paused.  This means that in general, higher priority pools will
          reach quota first before lower priority pools begin to be used.

          This setting may be specified at the provider level in order
          to apply to all pools within that provider, or it can be
          overridden here for a specific pool.

       .. attr:: zone

          Name of the IBM Cloud region zone to interact with (e.g.,
          ``us-south-1``).  This field may be omitted if
          :attr:`providers.[ibmvpc].zone` is supplied.  If both are
          supplied, this will override.

       .. attr:: subnet

          The name of the network upon which to create VMs.  This
          field may be omitted if :attr:`providers.[ibmvpc].subnet` is
          supplied.  If both are supplied, this will override.

       .. attr:: node-attributes
          :type: dict

          A dictionary of key-value pairs that will be stored with the node data
          in ZooKeeper. The keys and values can be any arbitrary string.

       .. attr:: public-ipv4
          :type: bool
          :default: false

          Whether to attach a public IPv4 address to instances.

          .. warning::

             As of the time of this writing, floating IP addresses are
             billed monthly and are not pro-rated.  Enabling this
             option can lead to high usage charges.

       .. attr:: use-internal-ip
          :type: bool
          :default: false

          If a public IP is attached but Nodepool should prefer the
          private IP, set this to true.

       .. attr:: host-key-checking
          :type: bool
          :default: true

          Whether to validate SSH host keys.  When true, this helps ensure
          that nodes are ready to receive SSH connections before they are
          supplied to the requestor.  When set to false, nodepool-launcher
          will not attempt to ssh-keyscan nodes after they are booted.
          Disable this if nodepool-launcher and the nodes it launches are
          on different networks, where the launcher is unable to reach the
          nodes directly, or when using Nodepool with non-SSH node
          platforms.  The default value is true.

       .. attr:: labels
          :type: list

          Each entry in a pool's `labels` section indicates that the
          corresponding label is available for use in this pool.  When creating
          nodes for a label, the flavor-related attributes in that label's
          section will be used.

          .. code-block:: yaml

             labels:
               - name: bionic
                 cloud-image: bionic
                 profile: cx2-2x4

          Each entry is a dictionary with the following keys:

          .. attr:: name
             :type: str
             :required:

             Identifier for this label.

          .. attr:: cloud-image
             :type: str
             :required:

             Refers to the name of an externally managed image in the
             cloud that already exists on the provider. The value of
             ``cloud-image`` should match the ``name`` of a previously
             configured entry from the ``cloud-images`` section of the
             provider.

          .. attr:: diskimage
             :type: str
             :required:

             Refers to provider's diskimages, see
             :attr:`providers.[ibmvpc].diskimages`.  Mutually exclusive
             with :attr:`providers.[ibmvpc].pools.labels.cloud-image`

          .. attr:: profile
             :required:

             The name of the IBM Cloud VPC hardware profile for the
             instance.  E.g., ``cx2-2x4``.

          .. attr:: user-data
             :type: str
             :default: None

             The `user data
             <https://cloud.ibm.com/docs/vpc?topic=vpc-user-data>`_
             value for newly created VMs.
