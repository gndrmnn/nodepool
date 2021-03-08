.. _static-driver:

.. default-domain:: zuul

Static Driver
-------------

Selecting the static driver adds the following options to the
:attr:`providers` section of the configuration.

.. attr-overview::
   :prefix: providers.[static]
   :maxdepth: 3

.. attr:: providers.[static]
   :type: list

   The static provider driver is used to define static nodes.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[static]`` to disambiguate from other
             drivers, but ``[static]`` is not required in the
             configuration (e.g. below ``providers.[static].pools``
             refers to the ``pools`` key in the ``providers`` section
             when the ``static`` driver is selected).

   Example:

   .. code-block:: yaml

      providers:
        - name: static-rack
          driver: static
          pools:
            - name: main
              nodes:
                - name: trusty.example.com
                  labels: trusty-static
                  timeout: 13
                  connection-port: 22022
                  host-key: fake-key
                  username: zuul
                  max-parallel-jobs: 1

   .. attr:: pools
      :type: list

      A pool defines a group of statically declared nodes.

      .. note::

         When providing different labels, it is better to have one pool per
         label to avoid requests being queued when one label is at capacity.

      Each entry is a dictionary with entries as follows

      .. attr:: name
         :type: str
         :required:

         Pool name

      .. attr:: node-attributes
         :type: dict

         A dictionary of key-value pairs that will be stored with the node data
         in ZooKeeper. The keys and values can be any arbitrary string.

      .. attr:: nodes
         :type: list
         :required:

         Each entry indicates a static node and it's attributes.

         .. attr:: name
            :type: str
            :required:

            The hostname or ip address of the static node. The combination of
            ``name``, :attr:`providers.[static].pools.nodes.username`, and
            :attr:`providers.[static].pools.nodes.connection-port`
            must be unique across all nodes defined within the configuration
            file.

         .. attr:: labels
            :type: list
            :required:

            The list of labels associated with the node.

         .. attr:: host-key-checking
             :type: bool
             :default: True

             Specify custom behavior of validation of host connection.
             When set to False, nodepool-launcher will not scan the nodes
             before they are registered. This might be needed if
             nodepool-launcher and the static nodes are on isolated
             networks. The default value is True.

         .. attr:: timeout
            :type: int
            :default: 5

            The timeout in second before the ssh ping is considered failed.

         .. attr:: connection-type
            :type: string
            :default: ssh

            The connection type that a consumer should use when connecting
            to the node.

            .. value:: winrm

            .. value:: ssh

         .. attr:: connection-port
            :type: int
            :default: 22 / 5986

            The port that a consumer should use when connecting to the node.
            For most nodes this is not necessary. This defaults to 22 when
            ``connection-type`` is 'ssh' and 5986 when it is 'winrm'.

         .. attr:: host-key
            :type: str

            The ssh host key of the node.

         .. attr:: username
            :type: str
            :default: zuul

            The username nodepool will use to validate it can connect to the
            node.

         .. attr:: python-path
            :type: str
            :default: /usr/bin/python2

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

            - For a windows node with the experimental `connection-type`
              ``ssh``, in which case ``cmd`` or ``powershell`` should be set
              and reflect the node's ``DefaultShell`` configuration.
            - If the default shell is not Bourne compatible (sh), but instead
              e.g. ``csh`` or ``fish``, and the user is aware that there is a
              long-standing issue with ``ansible_shell_type`` in combination
              with ``become``

         .. attr:: max-parallel-jobs
            :type: int
            :default: 1

            The number of jobs that can run in parallel on this node.

