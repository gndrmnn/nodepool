.. _metastatic-driver:

.. default-domain:: zuul

Metastatic Driver
-----------------

This driver uses NodePool nodes (from any driver) as backing nodes to
further allocate "static-like" nodes for end use.

A typical use case is to be able to request a large node (a `backing
node`) from a cloud provider, and then divide that node up into
smaller nodes that are actually used (`requested nodes`).  A backing
node can support one or more requested nodes, and backing nodes are
scaled up or down as necessary based on the number of requested
nodes.

The name is derived from the nodes it provides (which are like
"static" nodes) and the fact that the backing nodes come from NodePool
itself, which is "meta".

.. attr-overview::
   :prefix: providers.[metastatic]
   :maxdepth: 3

.. attr:: providers.[metastatic]
   :type: list

   A metastatic provider's resources are partitioned into groups
   called `pools`, and within a pool, the node types which are to be
   made available are listed.

   .. note:: For documentation purposes the option names are prefixed
             ``providers.[metastatic]`` to disambiguate from other
             drivers, but ``[metastatic]`` is not required in the
             configuration (e.g. below
             ``providers.[metastatic].pools`` refers to the ``pools``
             key in the ``providers`` section when the ``metastatic``
             driver is selected).

   Example:

   .. code-block:: yaml

      providers:
        - name: meta-provider
          driver: metastatic
          pools:
            - name: main
              max-servers: 10
              labels:
                - name: small-node
                  backing-label: large-node
                  max-parallel-jobs: 2
                  grace-time: 600

   .. attr:: name
      :required:

      A unique name for this provider configuration.

   .. attr:: boot-timeout
      :type: int seconds
      :default: 60

      Once an instance is active, how long to try connecting to the
      image via SSH.  If the timeout is exceeded, the node launch is
      aborted and the instance deleted.

   .. attr:: launch-timeout
      :type: int seconds
      :default: 3600

      The time to wait from issuing the command to create a new instance
      until that instance is reported as "active".  If the timeout is
      exceeded, the node launch is aborted and the instance deleted.

   .. attr:: launch-retries
      :default: 3

      The number of times to retry launching a node before considering
      the job failed.

   .. attr:: pools
       :type: list

       A pool defines a group of resources from the provider. Each pool has a
       maximum number of nodes which can be launched from it, along with a number
       of attributes that characterize the use of the backing nodes.

       .. attr:: name
          :required:

          A unique name within the provider for this pool of resources.

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

       .. attr:: host-key-checking
          :type: bool
          :default: False

          Whether to validate SSH host keys.  When true, this helps
          ensure that nodes are ready to receive SSH connections
          before they are supplied to the requestor.  When set to
          false, nodepool-launcher will not attempt to ssh-keyscan
          nodes after they are booted.  Unlike other drivers, this
          defaults to false here because it is presumed that the
          backing node has already been checked for connectivity.
          Enabling it here will cause the launcher to check
          connectivity each time it allocates a new slot on the
          backing node, and if a check fails, it will mark the backing
          node as failed and stop allocating any more slots on that
          node.

       .. attr:: node-attributes
          :type: dict

          A dictionary of key-value pairs that will be stored with the
          node data in ZooKeeper. The keys and values can be any
          arbitrary string.

          The metastatic driver will automatically use the values
          supplied by the backing node as default values.  Any values
          specified here for top-level dictionary keys will override
          those supplied by the backing node.

       .. attr:: max-servers
          :type: int

          Maximum number of servers spawnable from this pool. This can
          be used to limit the number of servers. If not defined
          nodepool can create as many servers that the backing node
          providers support.

       .. attr:: labels
          :type: list

          Each entry in a pool's `labels` section indicates that the
          corresponding label is available for use in this pool.

          .. code-block:: yaml

             labels:
               - name: small-node
                 backing-label: large-node
                 max-parallel-jobs: 2
                 grace-time: 600

          Each entry is a dictionary with the following keys:

          .. attr:: name
             :type: str
             :required:

             Identifier for this label.

          .. attr:: backing-label
             :type: str
             :required:

             Refers to the name of a different label in Nodepool which
             will be used to supply the backing nodes for requests of
             this label.

          .. attr:: max-parallel-jobs
             :type: int
             :default: 1

             The number of jobs that can run in parallel on a single
             backing node.

          .. attr:: grace-time
             :type: int
             :default: 60

             When all requested nodes which were assigned to a backing
             node have been deleted, the backing node itself is
             eligible for deletion.  In order to reduce churn,
             NodePool will wait a certain amount of time after the
             last requested node is deleted to see if new requests
             arrive for this label before deleting the backing node.
             Set this value to the amount of time in seconds to wait.

          .. attr:: host-key-checking
             :type: bool
             :default: False

             Whether to validate SSH host keys.  When true, this helps
             ensure that nodes are ready to receive SSH connections
             before they are supplied to the requestor.  When set to
             false, nodepool-launcher will not attempt to ssh-keyscan
             nodes after they are booted.  Unlike other drivers, this
             defaults to false here because it is presumed that the
             backing node has already been checked for connectivity.
             Enabling it here will cause the launcher to check
             connectivity each time it allocates a new slot on the
             backing node, and if a check fails, it will mark the backing
             node as failed and stop allocating any more slots on that
             node.

             .. note:: This value will override the value for
                       :attr:`providers.[metastatic].pools.host-key-checking`.
