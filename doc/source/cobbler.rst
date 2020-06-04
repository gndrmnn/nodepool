.. _cobbler-driver:

.. default-domain:: zuul

Cobbler Driver
--------------

Selecting the cobbler driver adds the following options to the :attr:`providers`
section of the configuration.

.. attr-overview::
   :prefix: providers.[cobbler]
   :maxdepth: 3

.. attr:: providers.[cobbler]
   :type: list

   An Cobbler provider's resources are partitioned into groups called `pool`
   (see :attr:`providers.[cobbler].pools` for details),
   and within a pool, the node types which are to be made available are listed.
   You should set the ``max-ready-age`` for labels associated with cobbler providers
   to be less than 3600 seconds.  This because a token to access Cobbler is generated
   for each Zuul node and Cobbler token expires in one hour.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[cobbler]`` to disambiguate from other
             drivers, but ``[cobbler]`` is not required in the
             configuration (e.g. below
             ``providers.[cobbler].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``cobbler``
             driver is selected).

   Example:

   .. code-block:: yaml

     providers:
        - name: cobbler.example.org
          driver: cobbler
          api-server-username: cobbler
          api-server-password: cobbler
          pools:
            - name: main
              node-attributes:
                owners: zuul-user-A
              labels:
                - cobbler1
                - cobbler2
            - name: second
              node-attributes:
                owners: zuul-user-B
              labels:
                - fake-label

   .. attr:: name
      :required:

      A unique name for this provider configuration.  The name is also used to
      identify the API endpoint for the Cobbler server you want to drive.  For
      example, if the API endpoint of your Cobbler server is located at
      ``http://cobbler.example.org/cobbler_api``, use ``cobbler.example.org`` as your name.

   .. attr:: api-server-username
      :required:

      Username for accessing the Cobbler server

   .. attr:: api-server-password
      :required:

      Password for accessing the Cobbler server

  .. attr:: pools
      :type: list

      A pool defines a group of resources from an Cobbler provider. Each pool has a
      maximum number of nodes which can be launched from it, along with a number
      of attributes used when launching nodes.

      .. attr:: name
         :required:

         A unique name within the provider for this pool of resources.

      .. attr:: labels
         :type: list

         Each entry in a pool's `labels` section indicates that the
         corresponding label is available for use in this pool.

      .. attr:: node-attributes
         :type: dict

         A dictionary of key-value pairs that will be used to filter the inventory
         of systems in Cobbler as nodes available to the pool. ``owners``, ``name``,
         ``status`` are some of the available keys.  Wildcard can be used in the value.
