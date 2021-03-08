.. _gce-driver:

.. default-domain:: zuul

Google Cloud Compute Engine (GCE) Driver
----------------------------------------

Selecting the gce driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[gce]
   :maxdepth: 3

.. attr:: providers.[gce]
   :type: list

   A GCE provider's resources are partitioned into groups called `pool`
   (see :attr:`providers.[gce].pools` for details), and within a pool,
   the node types which are to be made available are listed
   (see :attr:`providers.[gce].pools.labels` for details).

   See `Application Default Credentials`_ for information on how to
   configure credentials and other settings for GCE access in
   Nodepool's runtime environment.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[gce]`` to disambiguate from other
             drivers, but ``[gce]`` is not required in the
             configuration (e.g. below
             ``providers.[gce].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``gce``
             driver is selected).

   Example:

   .. code-block:: yaml

      - name: gce-uscentral1
        driver: gce
        project: nodepool-123456
        region: us-central1
        zone: us-central1-a
        cloud-images:
          - name: debian-stretch
            image-project: debian-cloud
            image-family: debian-9
            username: zuul
            key: ssh-rsa ...
        pools:
          - name: main
            max-servers: 8
            labels:
              - name: debian-stretch
                instance-type: f1-micro
                cloud-image: debian-stretch
                volume-type: standard
                volume-size: 10

   .. attr:: name
      :required:

      A unique name for this provider configuration.

   .. attr:: region
      :required:

      Name of the region to use; see `GCE regions and zones`_.

   .. attr:: zone
      :required:

      Name of the zone to use; see `GCE regions and zones`_.

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
           - name: debian-stretch
             image-project: debian-cloud
             image-family: debian-9
             username: zuul
             key: ssh-rsa ...

      Each entry is a dictionary with the following keys:

      .. attr:: name
         :type: string
         :required:

         Identifier to refer this cloud-image from
         :attr:`providers.[gce].pools.labels` section.

      .. attr:: image-id
         :type: str

         If this is provided, it is used to select the image from the cloud
         provider by ID.

      .. attr:: image-project
         :type: str

         If :attr:`providers.[gce].cloud-images.image-id` is not
         provided, this is used along with
         :attr:`providers.[gce].cloud-images.image-family` to find an
         image.

      .. attr:: image-family
         :type: str

         If :attr:`providers.[gce].cloud-images.image-id` is not
         provided, this is used along with
         :attr:`providers.[gce].cloud-images.image-project` to find an
         image.

      .. attr:: username
         :type: str

         The username that a consumer should use when connecting to the node.

      .. attr:: key
         :type: str

         An SSH public key to add to the instance (project global keys
         are added automatically).

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

         - For a windows image with the experimental `connection-type` ``ssh``
           in which case ``cmd`` or ``powershell`` should be set
           and reflect the node's ``DefaultShell`` configuration.
         - If the default shell is not Bourne compatible (sh), but instead
           e.g. ``csh`` or ``fish``, and the user is aware that there is a
           long-standing issue with ``ansible_shell_type`` in combination
           with ``become``

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

      A pool defines a group of resources from an GCE provider. Each pool has a
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
              - name: debian
                instance-type: f1-micro
                cloud-image: debian-stretch

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
              provider. See :attr:`providers.[gce].cloud-images`.

           .. attr:: instance-type
              :type: str
              :required:

              Name of the flavor to use.  See `GCE machine types`_.

           .. attr:: volume-type
              :type: string

              If given, the root volume type (``pd-standard`` or
              ``pd-ssd``).

           .. attr:: volume-size
              :type: int

              If given, the size of the root volume, in GiB.


.. _`Application Default Credentials`: https://cloud.google.com/docs/authentication/production
.. _`GCE regions and zones`: https://cloud.google.com/compute/docs/regions-zones/
.. _`GCE machine types`: https://cloud.google.com/compute/docs/machine-types


