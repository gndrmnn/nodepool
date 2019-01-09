
Nodepool is the underlying process within Zuul that handles the provisioning of compute resources for CI jobs. Primarily, Nodepool managed OpenStack virtual machines but does have the ability, through a driver interface, to support alternate compute sources. This repo is a proof of concept showcasing a Nodepool driver written to support management of bare metal hosts via the Packet Host API.

The Packet Python library is required for this driver:

pip install packet-python

A Packet Nodepool configuration entry within nodepool.yaml must include:

project_id - The project ID to deploy the bare metal hosts
auth_token - The authentication token associated with the project which is used authenticate requests
facility - The facility (data center) to deploy the bare metal hosts

For more information about the project_id and auth_token, please see: https://support.packet.com/kb/articles/api-integrations
For a current list of facilities, please see: https://www.packet.com/developers/api/#facilities

A Packet Host API key (account) is required and must be configured in the nodepool.yaml file. All the attributes of the Packet bare metal cloud should be defined in nodepool.yaml. This driver does not use clouds.yaml which is an OpenStack specific file.

More information about the Nodepool driver interface:

https://specs.openstack.org/openstack-infra/infra-specs/specs/nodepool-drivers.html 

More information about the Packet Host Python library and the underlying Packet Host REST API:

https://pypi.org/project/packet-python/
https://www.packet.com/developers/api/

