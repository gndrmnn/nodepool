.. _kubernetes-driver:

.. default-domain:: zuul

Kubernetes Driver
-----------------

Selecting the kubernetes driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr-overview::
   :prefix: providers.[kubernetes]
   :maxdepth: 3

.. attr:: providers.[kubernetes]
   :type: list

   A Kubernetes provider's resources are partitioned into groups
   called `pools` (see :attr:`providers.[kubernetes].pools` for
   details), and within a pool, the node types which are to be made
   available are listed (see :attr:`providers.[kubernetes].pools.labels` for
   details).

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[kubernetes]`` to disambiguate from other
             drivers, but ``[kubernetes]`` is not required in the
             configuration (e.g. below
             ``providers.[kubernetes].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``kubernetes``
             driver is selected).

   Example:

   .. code-block:: yaml

     providers:
       - name: kubespray
         driver: kubernetes
         context: admin-cluster.local
         pools:
           - name: main
             labels:
               - name: kubernetes-namespace
                 type: namespace
               - name: pod-fedora
                 type: pod
                 image: docker.io/fedora:28


   .. attr:: context

      Name of the context configured in ``kube/config``.

      Before using the driver, Nodepool either needs a ``kube/config``
      file installed with a cluster admin context, in which case this
      setting is required, or if Nodepool is running inside
      Kubernetes, this setting and the ``kube/config`` file may be
      omitted and Nodepool will use a service account loaded from the
      in-cluster configuration path.

   .. attr:: launch-retries
      :default: 3

      The number of times to retry launching a node before considering
      the job failed.


   .. attr:: pools
      :type: list

      A pool defines a group of resources from a Kubernetes
      provider.

      .. attr:: name
         :required:

         Namespaces are prefixed with the pool's name.

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

            The Kubernetes provider supports two types of labels:

            .. value:: namespace

               Namespace labels provide an empty namespace configured
               with a service account that can create pods, services,
               configmaps, etc.

            .. value:: pod

               Pod labels provide a dedicated namespace with a single pod
               created using the
               :attr:`providers.[kubernetes].pools.labels.image` parameter and it
               is configured with a service account that can exec and get
               the logs of the pod.

         .. attr:: image

            Only used by the
            :value:`providers.[kubernetes].pools.labels.type.pod` label type;
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

            - For a windows pod with the experimental `connection-type`
              ``ssh``, in which case ``cmd`` or ``powershell`` should be set
              and reflect the node's ``DefaultShell`` configuration.
            - If the default shell is not Bourne compatible (sh), but instead
              e.g. ``csh`` or ``fish``, and the user is aware that there is a
              long-standing issue with ``ansible_shell_type`` in combination
              with ``become``

         .. attr:: cpu
            :type: int

            Only used by the
            :value:`providers.[kubernetes].pools.labels.type.pod` label type;
            specifies the number of cpu to request for the pod.

         .. attr:: memory
            :type: int

            Only used by the
            :value:`providers.[kubernetes].pools.labels.type.pod` label type;
            specifies the amount of memory in MB to request for the pod.

         .. attr:: env
            :type: list
            :default: []

            Only used by the
            :value:`providers.[kubernetes].pools.labels.type.pod` label type;
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
            :value:`providers.[kubernetes].pools.labels.type.pod` label type;
            A map of key-value pairs to ensure the Kubernetes scheduler
            places the Pod on a node with specific node labels.

