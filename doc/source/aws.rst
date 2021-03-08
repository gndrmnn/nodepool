.. _aws-driver:

.. default-domain:: zuul

AWS EC2 Driver
--------------

Selecting the aws driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[aws]
   :maxdepth: 3

.. attr:: providers.[aws]
   :type: list

   An AWS provider's resources are partitioned into groups called `pool`
   (see :attr:`providers.[aws].pools` for details), and within a pool,
   the node types which are to be made available are listed
   (see :attr:`providers.[aws].pools.labels` for details).

   See `Boto Configuration`_ for information on how to configure credentials
   and other settings for AWS access in Nodepool's runtime environment.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[aws]`` to disambiguate from other
             drivers, but ``[aws]`` is not required in the
             configuration (e.g. below
             ``providers.[aws].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``aws``
             driver is selected).

   Example:

   .. code-block:: yaml

     providers:
       - name: ec2-us-west-2
         driver: aws
         region-name: us-west-2
         cloud-images:
           - name: debian9
             image-id: ami-09c308526d9534717
             username: admin
         pools:
           - name: main
             max-servers: 5
             subnet-id: subnet-0123456789abcdef0
             security-group-id: sg-01234567890abcdef
             labels:
               - name: debian9
                 cloud-image: debian9
                 instance-type: t3.medium
                 iam-instance-profile:
                   arn: arn:aws:iam::123456789012:instance-profile/s3-read-only
                 key-name: zuul
                 tags:
                   key1: value1
               - name: debian9-large
                 cloud-image: debian9
                 instance-type: t3.large
                 key-name: zuul
                 tags:
                   key1: value1
                   key2: value2

   .. attr:: name
      :required:

      A unique name for this provider configuration.

   .. attr:: region-name
      :required:

      Name of the `AWS region`_ to interact with.

   .. attr:: profile-name

      The AWS credentials profile to load for this provider. If unspecified the
      `boto3` library will select a profile.

      See `Boto Configuration`_ for more information.

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
           - name: ubuntu1804
             image-id: ami-082fd9a18128c9e8c
             username: ubuntu
           - name: ubuntu1804-by-filters
             image-filters:
               - name: name
                 values:
                  - named-ami
             username: ubuntu
           - name: my-custom-win2k3
             connection-type: winrm
             username: admin

      Each entry is a dictionary with the following keys

      .. attr:: name
         :type: string
         :required:

         Identifier to refer this cloud-image from :attr:`providers.[aws].pools.labels` section.
         Since this name appears elsewhere in the nodepool configuration file,
         you may want to use your own descriptive name here and use
         ``image-id`` to specify the cloud image so that if
         the image id changes on the cloud, the impact to your Nodepool
         configuration will be minimal. However, if ``image-id`` is not
         provided, this is assumed to be the image id in the cloud.

      .. attr:: image-id
         :type: str

         If this is provided, it is used to select the image from the cloud
         provider by ID.

      .. attr:: image-filters
         :type: list

         If provided, this is used to select an AMI by filters.  If the filters
         provided match more than one image, the most recent will be returned.
         `image-filters` are not valid if `image-id` is also specified.

         Each entry is a dictionary with the following keys

         .. attr:: name
            :type: str
            :required:

            The filter name. See `Boto describe images`_ for a list of valid filters.

         .. attr:: values
            :type: list
            :required:

            A list of str values to filter on

      .. attr:: username
         :type: str

         The username that a consumer should use when connecting to the node.

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

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an AWS provider. Each pool has a
      maximum number of nodes which can be launched from it, along with a number
      of cloud-related attributes used when launching nodes.

      .. attr:: name
         :required:

         A unique name within the provider for this pool of resources.

      .. attr:: node-attributes
         :type: dict

         A dictionary of key-value pairs that will be stored with the node data
         in ZooKeeper. The keys and values can be any arbitrary string.

      .. attr:: subnet-id

         If provided, specifies the subnet to assign to the primary network
         interface of nodes.

      .. attr:: security-group-id

         If provided, specifies the security group ID to assign to the primary
         network interface of nodes.

      .. attr:: public-ip-address
         :type: bool
         :default: True

         Specify if a public ip address shall be attached to nodes.

      .. attr:: host-key-checking
         :type: bool
         :default: True

         Specify custom behavior of validation of SSH host keys.  When set to
         False, nodepool-launcher will not ssh-keyscan nodes after they are
         booted. This might be needed if nodepool-launcher and the nodes it
         launches are on different networks.  The default value is True.

      .. attr:: labels
         :type: list

         Each entry in a pool's `labels` section indicates that the
         corresponding label is available for use in this pool.  When creating
         nodes for a label, the flavor-related attributes in that label's
         section will be used.

         .. code-block:: yaml

            labels:
              - name: bionic
                instance-type: m5a.large

         Each entry is a dictionary with the following keys

           .. attr:: name
              :type: str
              :required:

              Identifier to refer this label.
              Nodepool will use this to set the name of the instance unless
              the name is specified as a tag.

           .. attr:: cloud-image
              :type: str
              :required:

              Refers to the name of an externally managed image in the
              cloud that already exists on the provider. The value of
              ``cloud-image`` should match the ``name`` of a previously
              configured entry from the ``cloud-images`` section of the
              provider. See :attr:`providers.[aws].cloud-images`.

           .. attr:: ebs-optimized
              :type: bool
              :default: False

              Indicates whether EBS optimization
              (additional, dedicated throughput between Amazon EC2 and Amazon EBS,)
              has been enabled for the instance.

           .. attr:: instance-type
              :type: str
              :required:

              Name of the flavor to use.

           .. attr:: iam-instance-profile
              :type: dict

              Used to attach an iam instance profile.
              Useful for giving access to services without needing any secrets.

              .. attr:: name

                 Name of the instance profile.
                 Mutually exclusive with :attr:`providers.[aws].pools.labels.iam-instance-profile.arn`

              .. attr:: arn

                 ARN identifier of the profile.
                 Mutually exclusive with :attr:`providers.[aws].pools.labels.iam-instance-profile.name`

           .. attr:: key-name
              :type: string
              :required:

              The name of a keypair that will be used when
              booting each server.

           .. attr:: volume-type
              :type: string

              If given, the root `EBS volume type`_

           .. attr:: volume-size
              :type: int

              If given, the size of the root EBS volume, in GiB.

           .. attr:: userdata
              :type: str
              :default: None

              A string of userdata for a node. Example usage is to install
              cloud-init package on image which will apply the userdata.
              Additional info about options in cloud-config:
              https://cloudinit.readthedocs.io/en/latest/topics/examples.html

           .. attr:: tags
              :type: dict
              :default: None

              A dictionary of tags to add to the EC2 instances

.. _`EBS volume type`: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSVolumeTypes.html
.. _`AWS region`: https://docs.aws.amazon.com/general/latest/gr/rande.html
.. _`Boto configuration`: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/configuration.html
.. _`Boto describe images`: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.describe_images
