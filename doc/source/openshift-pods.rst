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

      Maximum number of pods that can be used.

   .. attr:: pools
      :type: list

      A pool defines a group of resources from an Openshift provider.

      .. attr:: name
         :required:

         The project's (namespace) name that will be used to create the pods.

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

      .. attr:: image

         The image name.

      .. attr:: image-pull
         :default: IfNotPresent
         :type: str

         The ImagePullPolicy, can be IfNotPresent, Always or Never.

      .. attr:: cpu
         :type: int

         The number of cpu to request for the pod.

      .. attr:: memory
         :type: int

         The amount of memory in MB to request for the pod.

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


