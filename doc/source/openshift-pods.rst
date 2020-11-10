.. _openshift-pods-driver:

.. default-domain:: zuul

Openshift Pods Driver
---------------------

Selecting the openshift pods driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr:: providers.[openshiftpods]
   :type: list

   The Openshift Pods driver is similar to the Openshift driver, but it
   only support pod label to be created in a single project. This enable
   using an unprivileged service account that doesn't requires the
   self-provisioner role.

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
      manually installed with self-provisioner (the service account needs to
      be able to create projects) context.
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

         The project's name that will be used to create the pods.

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


