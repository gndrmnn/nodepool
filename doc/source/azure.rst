.. _azure-driver:

.. default-domain:: zuul

Azure Compute Driver
--------------------

Before using the Azure driver, make sure you have created a service
principal and saved the credential information in a JSON file.  Follow
the instructions at `Azure CLI`_ and use the ``--sdk-auth`` flag::

  az ad sp create-for-rbac --name nodepool --sdk-auth

You must also have created a network for Nodepool to use.  Be sure to
enable IPv6 on the network if you plan to use it.

You may also need to register the `Microsoft.Compute` resource
provider with your subscription.

Selecting the azure driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[azure]
   :maxdepth: 3

.. attr:: providers.[azure]
   :type: list

   An Azure provider's resources are partitioned into groups called `pool`,
   and within a pool, the node types which are to be made available are listed

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[azure]`` to disambiguate from other
             drivers, but ``[azure]`` is not required in the
             configuration (e.g. below
             ``providers.[azure].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``azure``
             driver is selected).

   Example:

   .. code-block:: yaml

      providers:
        - name: azure-central-us
          driver: azure
          location: centralus
          resource-group: nodepool
          resource-group-location: centralus
          auth-path: /path/to/nodepoolCreds.json
          network: nodepool
          cloud-images:
            - name: bionic
              username: zuul
              key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAA...
              image-reference:
                sku: 18.04-LTS
                publisher: Canonical
                version: latest
                offer: UbuntuServer
          pools:
            - name: main
              max-servers: 10
              labels:
                - name: bionic
                  cloud-image: bionic
                  hardware-profile:
                    vm-size: Standard_D1_v2

   .. attr:: name
      :required:

      A unique name for this provider configuration.

   .. attr:: location
      :required:

      Name of the Azure region to interact with.

   .. attr:: resource-group
      :required:

      Name of the Resource Group in which to place the Nodepool nodes.

   .. attr:: resource-group-location
      :required:

      Name of the Azure region where the home Resource Group is or
      should be created.

   .. attr:: auth-path
      :required:

      Path to the JSON file containing the service principal credentials.
      Create with the `Azure CLI`_ and the ``--sdk-auth`` flag

   .. attr:: network
      :required:

      Network upon which to create VMs.  This can either be a string,
      in which case it must be the name of a network in the provider's
      resource group and Nodepool will use the subnet named
      ``default``, or it can be a dictionary with these keys:

      .. attr:: resource-group
         :default: The provider's resource group

         The resource group containing the network.

      .. attr:: network
         :required:

         The name of the network.

      .. attr:: subnet
         :default: default

         The name of the subnet within the network.

   .. attr:: ipv4
      :type: bool

      Whether to enable IPv4 networking.  Defaults to true unless ipv6
      is enabled.  Enabling this will attach a private IP address.

   .. attr:: ipv6
      :type: bool
      :default: false

      Whether to enable IPv6 networking.  Enabling this will attach a
      private IP address.

   .. attr:: public-ipv4
      :type: bool

      Whether to attach a public IPv4 address to instances.  Defaults
      to true, but will change to false in a future release.  Implies
      ``ipv4``.

   .. attr:: public-ipv6
      :type: bool
      :default: false

      Whether to attach a public IPv4 address to instances.  Defaults
      to true, but will change to false in a future release.  Implies
      ``ipv6``.

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

   .. attr:: cloud-images
      :type: list

      Each entry in this section must refer to an entry in the
      :attr:`labels` section.

      .. code-block:: yaml

         cloud-images:
           - name: bionic
             username: zuul
             image-reference:
               sku: 18.04-LTS
               publisher: Canonical
               version: latest
               offer: UbuntuServer
           - name: windows-server-2016
             username: zuul
             image-reference:
                sku: 2016-Datacenter
                publisher: MicrosoftWindowsServer
                version: latest
                offer: WindowsServer


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

      .. attr:: password
         :type: str

         If booting a Windows image, an administrative password is
         required.  Either supply it here, or set
         :attr:`providers.[azure].cloud-images.generate-password`.
         Nodepool does not provide the password to requesting clients;
         to be used it must be provided in some other manner.

      .. attr:: generate-password
         :type: bool

         If booting a Windows image, an administrative password is
         required.  If the password is not actually used (e.g., the
         image has key-based authentication enabled), a random
         password can be provided by enabling this option.  The
         password is not stored anywhere and is not retrievable.

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
         :attr:`providers.[azure].cloud-images.image-reference`, or
         :attr:`providers.[azure].cloud-images.image-id` must be
         provided.

         If a filter is provided, Nodepool will list all of the images
         in the provider's resource group and reduce the list using
         the supplied filter.  All items specified in the filter must
         match in order for an image to match.  If more than one image
         matches, the images are sorted by name and the last one
         matches.

         Example:

         .. code-block:: yaml

            cloud-images:
              - name: image-by-name
                image-filter:
                  name: test-image
              - name: image-by-tag
                image-filter:
                  tags:
                    foo: bar

         The following filters are available:

         .. attr:: name
            :type: str

            The name of the image.

         .. attr:: location
            :type: str

            The location of the image.

         .. attr:: tags
            :type: dict

            The image tags.

      .. attr:: image-id
         :type: str

         Specifies a private image to use by ID.  Either this field,
         :attr:`providers.[azure].cloud-images.image-reference`, or
         :attr:`providers.[azure].cloud-images.image-filter` must be
         provided.

      .. attr:: image-reference
         :type: dict

         Specifies a public image to use.  Either this field,
         :attr:`providers.[azure].cloud-images.image-id`, or
         :attr:`providers.[azure].cloud-images.image-filter` must be
         provided.

         .. attr:: sku
            :type: str
            :required:

            Image SKU

         .. attr:: publisher
            :type: str
            :required:

            Image Publisher

         .. attr:: offer
            :type: str
            :required:

            Image offers

         .. attr:: version
            :type: str
            :required:

            Image version

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
           - name: bionic
             pause: False
           - name: windows
             connection-type: winrm
             connection-port: 5986


      Each entry is a dictionary with the following keys

      .. attr:: name
         :type: string
         :required:

         Identifier to refer this image from
         :attr:`providers.[azure].pools.labels` and
         :attr:`diskimages` sections.

      .. attr:: pause
         :type: bool
         :default: False

         When set to True, nodepool-builder will not upload the image
         to the provider.

      .. attr:: username
         :type: str

         The username that should be used when connecting to the node.

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

       A pool defines a group of resources from an Azure provider. Each pool has a
       maximum number of nodes which can be launched from it, along with a number
       of cloud-related attributes used when launching nodes.

       .. attr:: name
          :required:

          A unique name within the provider for this pool of resources.

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

       .. attr:: node-attributes
          :type: dict

          A dictionary of key-value pairs that will be stored with the node data
          in ZooKeeper. The keys and values can be any arbitrary string.

       .. attr:: ipv4
          :type: bool

          Whether to enable IPv4 networking.  Defaults to true unless ipv6
          is enabled.  Enabling this will attach a private IP address.

       .. attr:: ipv6
          :type: bool
          :default: false

          Whether to enable IPv6 networking.  Enabling this will attach a
          private IP address.

       .. attr:: public-ipv4
          :type: bool

          Whether to attach a public IPv4 address to instances.  Defaults
          to true, but will change to false in a future release.  Implies
          ``ipv4``.

       .. attr:: public-ipv6
          :type: bool
          :default: false

          Whether to attach a public IPv4 address to instances.  Defaults
          to true, but will change to false in a future release.  Implies
          ``ipv6``.

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
                 hardware-profile:
                   vm-size: Standard_D1_v2

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
             :attr:`providers.[azure].diskimages`.  Mutually exclusive
             with :attr:`providers.[azure].pools.labels.cloud-image`

          .. attr:: hardware-profile
             :required:

             .. attr:: vm-size
                :required:
                :type: str

                VM Size of the VMs to use in Azure. See the VM size
                list on `azure.microsoft.com`_ for the list of sizes
                availabile in each region.

          .. attr:: tags
             :type: dict
             :default: None

             A dictionary of tags to add to newly created VMs.

          .. attr:: user-data
             :type: str
             :default: None

             The `Azure User Data`_ value for newly created VMs.

          .. attr:: custom-data
             :type: str
             :default: None

             The `Azure Custom Data`_ value for newly created VMs.


.. _`Azure CLI`: https://docs.microsoft.com/en-us/cli/azure/create-an-azure-service-principal-azure-cli?view=azure-cli-latest

.. _azure.microsoft.com: https://azure.microsoft.com/en-us/global-infrastructure/services/?products=virtual-machines

.. _`Azure User Data`: https://docs.microsoft.com/en-us/azure/virtual-machines/user-data

.. _`Azure Custom Data`: https://docs.microsoft.com/en-us/azure/virtual-machines/custom-data
