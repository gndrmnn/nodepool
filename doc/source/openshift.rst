.. _openshift-driver:

.. default-domain:: zuul

Openshift Driver
----------------

Selecting the openshift driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr-overview::
   :prefix: providers.[openshift]
   :maxdepth: 3

.. attr:: providers.[openshift]
   :type: list

   An Openshift provider's resources are partitioned into groups called `pool`
   (see :attr:`providers.[openshift].pools` for details), and within a pool,
   the node types which are to be made available are listed
   (see :attr:`providers.[openshift].labels` for details).

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[openshift]`` to disambiguate from other
             drivers, but ``[openshift]`` is not required in the
             configuration (e.g. below
             ``providers.[openshift].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``openshift``
             driver is selected).

   Example:

   .. code-block:: yaml

     providers:
       - name: cluster
         driver: openshift
         context: context-name
         pools:
           - name: main
             labels:
               - name: openshift-project
                 type: project
               - name: openshift-pod
                 type: pod
                 image: docker.io/fedora:28

   .. attr:: context
      :required:

      Name of the context configured in ``kube/config``.

      Before using the driver, Nodepool services need a ``kube/config`` file
      manually installed with self-provisioner (the service account needs to
      be able to create projects) context.
      Make sure the context is present in ``oc config get-contexts`` command
      output.

   .. attr:: launch-retries
      :default: 3

      The number of times to retry launching a node before considering
      the job failed.

   .. attr:: max-projects
      :default: infinite
      :type: int

      Maximum number of projects that can be used.

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an Openshift provider.

      .. attr:: name
         :required:

         Project's name are prefixed with the pool's name.

      .. attr:: node-attributes
         :type: dict

         A dictionary of key-value pairs that will be stored with the node data
         in ZooKeeper. The keys and values can be any arbitrary string.

   .. attr:: labels
      :type: list

      Each entry in a pool`s `labels` section indicates that the
      corresponding label is available for use in this pool.

      Each entry is a dictionary with the following keys

      .. attr:: name
         :required:

         Identifier for this label; references an entry in the
         :attr:`labels` section.

      .. attr:: type

         The Openshift provider supports two types of labels:

         .. value:: project

            Project labels provide an empty project configured
            with a service account that can create pods, services,
            configmaps, etc.

         .. value:: pod

            Pod labels provide a new dedicated project with a single
            pod created using the
            :attr:`providers.[openshift].labels.image` parameter and it
            is configured with a service account that can exec and get
            the logs of the pod.

      .. attr:: image

         Only used by the
         :value:`providers.[openshift].labels.type.pod` label type;
         specifies the image name used by the pod.

      .. attr:: image-pull
         :default: IfNotPresent
         :type: str

         The ImagePullPolicy, can be IfNotPresent, Always or Never.

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

      .. attr:: cpu
         :type: int

         Only used by the
         :value:`providers.[openshift].labels.type.pod` label type;
         specifies the number of cpu to request for the pod.

      .. attr:: memory
         :type: int

         Only used by the
         :value:`providers.[openshift].labels.type.pod` label type;
         specifies the amount of memory in MB to request for the pod.

      .. attr:: env
         :type: list
         :default: []

         Only used by the
         :value:`providers.[openshift].labels.type.pod` label type;
         A list of environment variables to pass to the Pod.

         .. attr:: name
            :type: str
            :required:

            The name of the environment variable passed to the Pod.

         .. attr:: value
            :type: str
            :required:

            The value of the environment variable passed to the Pod.

      .. attr:: node-selector
         :type: dict

         Only used by the
         :value:`providers.[openshift].labels.type.pod` label type;
         A map of key-value pairs to ensure the OpenShift scheduler
         places the Pod on a node with specific node labels.
