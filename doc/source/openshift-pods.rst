.. _openshift-pods-driver:

.. default-domain:: zuul

Openshift Pods Driver
---------------------

Selecting the openshift pods driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr:: providers.[openshiftpods]
   :type: list

   The Openshift Pods driver is similar to the Openshift driver, but it
   only supports pod label. This enables using an unprivileged service account
   that doesn't require the self-provisioner role.

   Example:

   .. code-block:: yaml

     providers:
       - name: cluster
         driver: openshiftpods
         context: unprivileged-context-name
         pools:
           - name: main
             labels:
               - name: openshift-pod
                 image: docker.io/fedora:28

   .. attr:: context
      :required:

      Name of the context configured in ``kube/config``.

      Before using the driver, Nodepool services need a ``kube/config`` file
      manually installed.
      Make sure the context is present in ``oc config get-contexts`` command
      output.

   .. attr:: launch-retries
      :default: 3

      The number of times to retry launching a pod before considering
      the job failed.

   .. attr:: max-pods
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

      Maximum number of pods spawnable from this provider's pools by
      default. This can be used to limit the number of pods. If not
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
      :attr:`providers.[openshiftpods].pools.labels.extra-resources`
      attribute.

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an Openshift provider.

      .. attr:: name
         :required:

         The project's (namespace) name that will be used to create the pods.

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
         :attr:`providers.[openshiftpods].pools.labels.extra-resources` attribute.

      .. attr:: default-label-cpu
         :type: int

         Specifies specifies a default value for
         :attr:`providers.[openshiftpods].pools.labels.cpu` for all
         labels of this pool that do not set their own value.

      .. attr:: default-label-memory
         :type: int

         Specifies a default value in MiB for
         :attr:`providers.[openshiftpods].pools.labels.memory` for all
         labels of this pool that do not set their own value.

      .. attr:: default-label-storage
         :type: int

         Specifies a default value in MB for
         :attr:`providers.[openshiftpods].pools.labels.storage` for all
         labels of this pool that do not set their own value.

      .. attr:: default-label-cpu-limit
         :type: int

         Specifies specifies a default value for
         :attr:`providers.[openshiftpods].pools.labels.cpu-limit` for all
         labels of this pool that do not set their own value.

      .. attr:: default-label-memory-limit
         :type: int

         Specifies a default value in MiB for
         :attr:`providers.[openshiftpods].pools.labels.memory-limit` for
         all labels of this pool that do not set their own value.

      .. attr:: default-label-storage-limit
         :type: int

         Specifies a default value in MB for
         :attr:`providers.[openshiftpods].pools.labels.storage-limit` for
         all labels of this pool that do not set their own value.

      .. attr:: labels
         :type: list

         Each entry in a pool`s `labels` section indicates that the
         corresponding label is available for use in this pool.

         Each entry is a dictionary with the following keys

         .. attr:: name
            :required:

            Identifier for this label; references an entry in the
            :attr:`labels` section.

         .. attr:: image

            The image name.

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
                   type: pod
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

         .. attr:: dynamic-labels
            :type: dict
            :default: None

            Similar to
            :attr:`providers.[openshiftpods].pools.labels.labels`,
            but is interpreted as a format string with the following
            values available:

            * request: Information about the request which prompted the
              creation of this node (note that the node may ultimately
              be used for a different request and in that case this
              information will not be updated).

              * id: The request ID.

              * labels: The list of labels in the request.

              * requestor: The name of the requestor.

              * requestor_data: Key/value information from the requestor.

              * relative_priority: The relative priority of the request.

              * event_id: The external event ID of the request.

              * created_time: The creation time of the request.

              * tenant_name: The name of the tenant associated with the
                request.

            For example:

            .. code-block:: yaml

               labels:
                 - name: pod-fedora
                   dynamic-labels:
                     request_info: "{request.id}"

         .. attr:: annotations
            :type: dict

            A dictionary of additional values to be added to the
            pod metadata.  The value of this field is
            added to the `metadata.annotations` field in OpenShift.
            This field contains arbitrary key/value pairs that can be accessed
            by tools and libraries. E.g custom schedulers can make use of this
            metadata.

         .. attr:: cpu
            :type: int

            Specifies the number of cpu to request for the pod.  If no
            limit is specified, this will also be used as the limit.

         .. attr:: memory
            :type: int

            Specifies the amount of memory in MiB to request for the
            pod.  If no limit is specified, this will also be used as
            the limit.

         .. attr:: storage
            :type: int

            Specifies the amount of ephemeral-storage in MB to request
            for the pod.  If no limit is specified, this will also be
            used as the limit.

         .. attr:: extra-resources
            :type: dict

            Specifies any extra resources that Nodepool should
            consider in its quota calculation other than the resources
            described above (cpu, memory, storage).

         .. attr:: cpu-limit
            :type: int

            Specifies the cpu limit for the pod.

         .. attr:: memory-limit
            :type: int

            Specifies the memory limit in MiB for the pod.

         .. attr:: storage-limit
            :type: int

            Specifies the ephemeral-storage limit in MB for the pod.

         .. attr:: gpu
            :type: float

            Specifies the amount of gpu allocated to the pod.
            This will be used to set both requests and limits to the same
            value, based on how kubernetes assigns gpu resources:
            https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/.

         .. attr:: gpu-resource
            :type: str

            Specifies the custom schedulable resource
            associated with the installed gpu that is available
            in the cluster.

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

         .. attr:: env
            :type: list
            :default: []

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

            A map of key-value pairs to ensure the OpenShift scheduler
            places the Pod on a node with specific node labels.

         .. attr:: scheduler-name
            :type: str

            Sets the `schedulerName` field on the container.  Normally
            left unset for the OpenShift default.

         .. attr:: privileged
            :type: bool

            Sets the `securityContext.privileged` flag on the
            container.  Normally left unset for the OpenShift default.

         .. attr:: volumes
            :type: list

            Sets the `volumes` field on the pod.  If supplied, this
            should be a list of OpenShift Pod Volume definitions.

         .. attr:: volume-mounts
            :type: list

            Sets the `volumeMounts` flag on the container.  If
            supplied, this should be a list of OpenShift Container
            VolumeMount definitions.

         .. attr:: spec
            :type: dict

            This attribute is exclusive with all other label
            attributes except
            :attr:`providers.[openshiftpods].pools.labels.name`
            :attr:`providers.[openshiftpods].pools.labels.annotations`,
            :attr:`providers.[openshiftpods].pools.labels.labels` and
            :attr:`providers.[openshiftpods].pools.labels.dynamic-labels`.
            If a `spec` is provided, then Nodepool will supply the
            contents of this value verbatim to OpenShift as the
            ``spec`` attribute of the OpenShift ``Pod`` definition.
            No other Nodepool attributes are used, including any
            default values set at the provider level (such as
            `default-label-cpu` and similar).

            This attribute allows for the creation of arbitrary
            complex pod definitions but the user is responsible for
            ensuring that they are suitable.  The first container in
            the pod is expected to be a long-running container that
            hosts a shell environment for running commands.  The
            following minimal definition matches what Nodepool itself
            normally creates and is recommended as a starting point:

            .. code-block:: yaml

               labels:
                 - name: custom-pod
                   spec:
                     containers:
                       - name: custom-pod
                         image: ubuntu:jammy
                         imagePullPolicy: IfNotPresent
                         command: ["/bin/sh", "-c"]
                         args: ["while true; do sleep 30; done;"]
