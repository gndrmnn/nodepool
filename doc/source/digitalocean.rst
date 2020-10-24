.. _digitalocean-driver:

.. default-domain:: zuul

Digital Ocean Driver
----------------------------------------

Selecting the digitalocean driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[digitalocean]
   :maxdepth: 3

.. attr:: providers.[digitalocean]
   :type: list

   A digitalocean provider's resources are partitioned into groups called `pool`
   (see :attr:`providers.[digitalocean].pools` for details), and within a pool,
   the node types which are to be made available are listed
   (see :attr:`providers.[digitalocean].pools.labels` for details).

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[digitalocean]`` to disambiguate from other
             drivers, but ``[digitalocean]`` is not required in the
             configuration (e.g. below
             ``providers.[digitalocean].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``digitalocean``
             driver is selected).

   Example:

   .. code-block:: yaml

      - name: digitalocean-fra1
        driver: digitalocean
        region: fra1
        cloud-images:
          - name: ubuntu-groovy
            image: ubuntu-20-10-x64
            username: zuul
            ssh-keys:
              - 28913272
              - 94:97:a4:74:97:c1:24:e0:1e:24:21:a5:9c:87:1e:fe
        pools:
          - name: main
            max-servers: 8
            labels:
              - name: ubuntu-groovy
                size: s-1vcpu-1gb
                cloud-image: ubuntu-groovy

   .. attr:: name
      :required:

      A unique name for this provider configuration.

   .. attr:: region
      :required:

      Name of the region to use; see `Digital Ocean Regional Availability Matrix`_.

   .. attr:: boot-timeout
      :type: int seconds
      :default: 60

      Once an instance is active, how long to try connecting to the
      image via SSH.  If the timeout is exceeded, the node launch is
      aborted and the instance deleted.

   .. attr:: launch-retries
      :default: 3

      The number of times to retry launching a node before considering
      the job failed.

   .. attr:: cloud-images
      :type: list

      Each entry in this section must refer to an entry in the
      :attr:`labels` section.

      .. code-block:: yaml

         cloud-images:
           - name: ubuntu-groovy
             username: zuul
             image: ubuntu-20-10-x64
             username: root
             ssh-keys:
               - 28913272
               - 94:97:a4:74:97:c1:24:e0:1e:24:21:a5:9c:87:1e:fe

      Each entry is a dictionary with the following keys:

      .. attr:: name
         :type: string
         :required:

         Identifier to refer this cloud-image from
         :attr:`providers.[digitalocean].pools.labels` section.

      .. attr:: image
         :type: str

         If this is provided, it is used to select the image from the cloud
         provider by ID.

      .. attr:: username
         :type: str

         The username that a consumer should use when connecting to the node.

      .. attr:: ssh-keys
         :type: str

         An SSH public key to add to the instances root account.

      .. attr:: python-path
         :type: str
         :default: auto

         The path of the default python interpreter.  Used by Zuul to set
         ``ansible_python_interpreter``.  The special value ``auto`` will
         direct Zuul to use inbuilt Ansible logic to select the
         interpreter on Ansible >=2.8, and default to
         ``/usr/bin/python2`` for earlier versions.

      .. attr:: connection-type
         :type: str

         The connection type that a consumer should use when connecting to the
         node. For most images this is not necessary. However when creating
         Windows images this could be 'winrm' to enable access via ansible.

      .. attr:: connection-port
         :type: int
         :default: 22/ 5986

         The port that a consumer should use when connecting to the node. For
         most diskimages this is not necessary. This defaults to 22 for ssh and
         5986 for winrm.

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an  Digital Ocean provider. Each pool has a
      maximum number of nodes which can be launched from it, along with a number
      of cloud-related attributes used when launching nodes.

      .. attr:: name
         :required:

         A unique name within the provider for this pool of resources.

      .. attr:: node-attributes
         :type: dict

         A dictionary of key-value pairs that will be stored with the node data
         in ZooKeeper. The keys and values can be any arbitrary string.

      .. attr:: host-key-checking
         :type: bool
         :default: True

         Specify custom behavior of validation of SSH host keys.  When set to
         False, nodepool-launcher will not ssh-keyscan nodes after they are
         booted. This might be needed if nodepool-launcher and the nodes it
         launches are on different networks.  The default value is True.

      .. attr:: use-internal-ip
         :default: False

         Whether to access the instance with the internal or external IP
         address.

      .. attr:: labels
         :type: list

         Each entry in a pool's `labels` section indicates that the
         corresponding label is available for use in this pool.  When creating
         nodes for a label, the flavor-related attributes in that label's
         section will be used.

         .. code-block:: yaml

            labels:
              - name: ubuntu-groovy
                size: s-1vcpu-1gb-x64
                cloud-image: ubuntu-groovy

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
              provider. See :attr:`providers.[digitalocean].cloud-images`.

           .. attr:: size
              :type: str
              :required:

              Size slug of the instance to use.  See `doctl compute size list`.


.. _`Digital Ocean Regional Availability Matrix`: https://www.digitalocean.com/docs/platform/availability-matrix/

