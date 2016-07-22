.. _scripts:

Node Preparation Scripts
========================

Nodepool requires the specification of a script directory
(`script-dir`) in its configuration.  Scripts in this directory will
be available on the built node.  Nodepool may invoke scripts in this
directory at various points described below.

Image Preparation
-----------------

Snapshot images
~~~~~~~~~~~~~~~

When Nodepool starts a virtual machine for the purpose of creating a
snapshot image, all of the files within the `script-dir` directory
will be copied to the virtual machine and placed in
`/opt/nodepool-scripts` so they are available for use.

Nodepool will have deployed a temporary key and will try common cloud
logins (``cloud-user``, ``ubuntu``, ``centos``) to establish a ssh
connection and copy the files from ``script-dir``.

Build script
++++++++++++

After copying, nodepool will run the preparation script specified in
the image configuration key ``script``.  The host name is passed as
the first argument to this script.  Any environment variables present
in the nodepool daemon environment that begin with ``NODEPOOL_`` will
be also set in the calling environment for the script -- this is
useful during testing to alter script behavior, for instance, to add a
local ssh key that would not otherwise be set in production.

Note further interaction will happen via the ``username`` and and
``private-key`` specified in the image, see :ref:`images`).  Thus it
is most likely that you would use this script to setup the `username`
and authentication for `private-key` as mentioned above.

diskimage-builder
~~~~~~~~~~~~~~~~~

When using images built by ``diskimage-builder``, one of the elements
included within the image-build process must deploy the scripts.  The
directory specified in ``script-dir`` will be exported to the
diskimage-builder process as ``NODEPOOL_SCRIPTDIR`` and one of the
build elements should make it available within the built image at
``/opt/nodepool-scripts``.

As an example, the `nodepool-base
<http://git.openstack.org/cgit/openstack-infra/project-config/tree/nodepool/elements/nodepool-base>`__
element is responsible for the deployment of scripts in the OpenStack
Infrastructure environment.

Note that in this case, the generic preparation script described above
(``script``, :ref:`images`) will not be invoked.  Thus one of the
elements should also setup the user specified in ``username`` and
allow authentication with the key in ``private-key`` (i.e. specify the
public portion in the user's ``.ssh/authorized_keys``).

Ready script
------------

Each label can specify a ready script with ``ready-script``.  This script can be
used to perform any last minute changes to a node after it has been launched
but before it is put in the READY state to receive jobs.  In particular, it
can read the files in /etc/nodepool to perform multi-node related setup.

Those files include:

**/etc/nodepool/role**
  Either the string ``primary`` or ``sub`` indicating whether this
  node is the primary (the node added to the target and which will run
  the job), or a sub-node.
**/etc/nodepool/node**
  The IP address of this node.
**/etc/nodepool/node_private**
  The private IP address of this node.
**/etc/nodepool/primary_node**
  The IP address of the primary node, usable for external access.
**/etc/nodepool/primary_node_private**
  The Private IP address of the primary node, for internal communication.
**/etc/nodepool/sub_nodes**
  The IP addresses of the sub nodes, one on each line,
  usable for external access.
**/etc/nodepool/sub_nodes_private**
  The Private IP addresses of the sub nodes, one on each line.
**/etc/nodepool/id_rsa**
  An OpenSSH private key generated specifically for this node group.
**/etc/nodepool/id_rsa.pub**
  The corresponding public key.
**/etc/nodepool/provider**
  Information about the provider in a shell-usable form.  This
  includes the following information:

  **NODEPOOL_PROVIDER**
    The name of the provider
  **NODEPOOL_CLOUD**
    The name of the cloud
  **NODEPOOL_REGION**
    The name of the region
  **NODEPOOL_AZ**
    The name of the availability zone (if available)
