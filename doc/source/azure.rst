.. _azure-driver:

.. default-domain:: zuul

Azure Compute Driver
--------------------

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
          resource-group-location: centralus
          location: centralus
          resource-group: nodepool
          auth-path: /Users/grhayes/.azure/nodepoolCreds.json
          subnet-id: /subscriptions/<subscription-id>/resourceGroups/nodepool/providers/Microsoft.Network/virtualNetworks/NodePool/subnets/default
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
                  tags:
                    department: R&D
                    purpose: CI/CD

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

   .. attr:: subnet-id
      :required:

      Subnet to create VMs on

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

         The username that a consumer should use when connecting to the
         node.

      .. attr:: key
         :type: str

         The SSH public key that should be installed on the node.

      .. attr:: image-reference
         :type: dict
         :required:

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


   .. attr:: pools
       :type: list

       A pool defines a group of resources from an Azure provider. Each pool has a
       maximum number of nodes which can be launched from it, along with a number
       of cloud-related attributes used when launching nodes.

       .. attr:: name
          :required:

          A unique name within the provider for this pool of resources.

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

          Each entry is a dictionary with the following keys

            .. attr:: name
               :type: str
               :required:

               Identifier to refer this label.

            .. attr:: cloud-image
              :type: str
              :required:

              Refers to the name of an externally managed image in the
              cloud that already exists on the provider. The value of
              ``cloud-image`` should match the ``name`` of a previously
              configured entry from the ``cloud-images`` section of the
              provider.

            .. attr:: hardware-profile
              :required:

              .. attr:: vm-size
                 :required:
                 :type: str

                 VM Size of the VMs to use in Azure. See the VM size list on `azure.microsoft.com`_
                 for the list of sizes availabile in each region.


.. _`Azure CLI`: https://docs.microsoft.com/en-us/cli/azure/create-an-azure-service-principal-azure-cli?view=azure-cli-latest

.. _azure.microsoft.com: https://azure.microsoft.com/en-us/global-infrastructure/services/?products=virtual-machines

