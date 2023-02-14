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
   (see :attr:`providers.[openshift].pools.labels` for details).

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

      An alias for `max-servers`.

   .. attr:: max-cores
      :type: int
      :default: unlimited

      Maximum number of cores usable from this provider's pools by
      default. This can be used to limit usage of the openshift
      backend. If not defined nodepool can use all cores up to the
      limit of the backend.

   .. attr:: max-servers
      :type: int
      :default: unlimited

      Maximum number of projects spawnable from this provider's pools by
      default. This can be used to limit the number of projects. If not
      defined nodepool can create as many servers the openshift
      backend allows.

   .. attr:: max-ram
      :type: int
      :default: unlimited

      Maximum ram usable from this provider's pools by default. This
      can be used to limit the amount of ram allocated by nodepool. If
      not defined nodepool can use as much ram as the openshift
      backend allows.

   .. attr:: max-resources
      :type: dict
      :default: unlimited

      A dictionary of other quota resource limits applicable to this
      provider's pools by default.  Arbitrary limits may be supplied
      with the
      :attr:`providers.[openshift].pools.labels.extra-resources`
      attribute.

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an Openshift provider.

      .. attr:: name
         :required:

         Project's name are prefixed with the pool's name.

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

      .. attr:: max-cores
         :type: int

         Maximum number of cores usable from this pool. This can be used
         to limit usage of the kubernetes backend. If not defined nodepool can
         use all cores up to the limit of the backend.

      .. attr:: max-servers
         :type: int

         Maximum number of pods spawnable from this pool. This can
         be used to limit the number of pods. If not defined
         nodepool can create as many servers the kubernetes backend allows.

      .. attr:: max-ram
         :type: int

         Maximum ram usable from this pool. This can be used to limit
         the amount of ram allocated by nodepool. If not defined
         nodepool can use as much ram as the kubernetes backend allows.

      .. attr:: max-resources
         :type: dict
         :default: unlimited

         A dictionary of other quota resource limits applicable to
         this pool.  Arbitrary limits may be supplied with the
         :attr:`providers.[openshift].pools.labels.extra-resources` attribute.

      .. attr:: default-label-cpu
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies specifies a default value for
         :attr:`providers.[openshift].pools.labels.cpu` for all labels of
         this pool that do not set their own value.

      .. attr:: default-label-memory
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies a default value in MiB for
         :attr:`providers.[openshift].pools.labels.memory` for all labels of
         this pool that do not set their own value.

      .. attr:: default-label-storage
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies a default value in MB for
         :attr:`providers.[openshift].pools.labels.storage` for all labels of
         this pool that do not set their own value.

      .. attr:: default-label-cpu-limit
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies specifies a default value for
         :attr:`providers.[openshift].pools.labels.cpu-limit` for all labels of
         this pool that do not set their own value.

      .. attr:: default-label-memory-limit
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies a default value in MiB for
         :attr:`providers.[openshift].pools.labels.memory-limit` for all labels of
         this pool that do not set their own value.

      .. attr:: default-label-storage-limit
         :type: int

         Only used by the
         :value:`providers.[openshift].pools.labels.type.pod` label type;
         specifies a default value in MB for
         :attr:`providers.[openshift].pools.labels.storage-limit` for all labels of
         this pool that do not set their own value.

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
               :attr:`providers.[openshift].pools.labels.image` parameter and it
               is configured with a service account that can exec and get
               the logs of the pod.

         .. attr:: image

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod` label type;
            specifies the image name used by the pod.

         .. attr:: image-pull
            :default: IfNotPresent
            :type: str

            The ImagePullPolicy, can be IfNotPresent, Always or Never.

         .. attr:: image-pull-secrets
            :default: []
            :type: list

            The imagePullSecrets needed to pull container images from a private
            registry.

            Example:

            .. code-block:: yaml

               labels:
                 - name: openshift-pod
                   image: docker.io/fedora:28
                   image-pull-secrets:
                     - name: registry-secret

         .. attr:: labels
            :type: dict

            A dictionary of additional values to be added to the
            namespace or pod metadata.  The value of this field is
            added to the `metadata.labels` field in OpenShift.  Note
            that this field contains arbitrary key/value pairs and is
            unrelated to the concept of labels in Nodepool.

         .. attr:: annotations
            :type: dict

            A dictionary of additional values to be added to the
            pod metadata.  The value of this field is
            added to the `metadata.annotations` field in OpenShift.
            This field contains arbitrary key/value pairs that can be accessed
            by tools and libraries. E.g custom schedulers can make use of this
            metadata.

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
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the number of cpu to request for the
            pod.  If no limit is specified, this will also be used as
            the limit.

         .. attr:: memory
            :type: int

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the amount of memory in MiB to
            request for the pod.  If no limit is specified, this will
            also be used as the limit.

         .. attr:: storage
            :type: int

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the amount of ephemeral-storage in
            MB to request for the pod.  If no limit is specified, this
            will also be used as the limit.

         .. attr:: extra-resources
            :type: dict

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies any extra resources that Nodepool
            should consider in its quota calculation other than the
            resources described above (cpu, memory, storage).

         .. attr:: cpu-limit
            :type: int

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the cpu limit for the pod.

         .. attr:: memory-limit
            :type: int

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the memory limit in MiB for the pod.

         .. attr:: storage-limit
            :type: int

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the ephemeral-storage limit in
            MB for the pod.

         .. attr:: gpu
            :type: float

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the amount of gpu allocated to the pod.
            This will be used to set both requests and limits to the same
            value, based on how kubernetes assigns gpu resources:
            https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/.

         .. attr:: gpu-resource
            :type: str

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type; specifies the custom schedulable resource
            associated with the installed gpu that is available
            in the cluster.

         .. attr:: env
            :type: list
            :default: []

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod` label type;
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
            :value:`providers.[openshift].pools.labels.type.pod` label type;
            A map of key-value pairs to ensure the OpenShift scheduler
            places the Pod on a node with specific node labels.

         .. attr:: scheduler-name
            :type: str

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type.  Sets the `schedulerName` field on the
            container.  Normally left unset for the OpenShift
            default.

         .. attr:: privileged
            :type: bool

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type.  Sets the `securityContext.privileged` flag on
            the container.  Normally left unset for the OpenShift default.

         .. attr:: volumes
            :type: list

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type.  Sets the `volumes` field on the pod.  If
            supplied, this should be a list of OpenShift Pod Volume
            definitions.

         .. attr:: volume-mounts
            :type: list

            Only used by the
            :value:`providers.[openshift].pools.labels.type.pod`
            label type.  Sets the `volumeMounts` flag on the
            container.  If supplied, this should be a list of
            OpenShift Container VolumeMount definitions.
