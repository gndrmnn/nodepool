.. _api:

===============
Nodepool API
===============

The Nodepool API exposes RESTful web services by using the Pecan framework.

The usual CRUD operations that can be performed with the *nodepool* command can also be done with this API.

The output format for all API methods is JSON.

For authentication, it currently supports HTTP basic authentication scheme.

The ``config.py`` file of Pecan needs the following entries to specify the nodepool config file and API credentials::

    nodepool_conf_file = '/path/to/nodepool.yaml'
    api_user = 'username'
    api_password = 'password'

Nodes
-----

**GET /v1/nodes**
    Returns a list of nodes

    ``Parameters:`` None

    ``Return type:`` JSON list of *Node* objects

**GET /v1/nodes/(node_id)**
    Returns a node

    ``Parameters:``

      - ``node_id(int)`` - Node id

    ``Return type:`` JSON representation of *Node* object

**DELETE /v1/nodes/(node_id)**
    Deletes a node **(Authentication required)**

    ``Parameters:``

      - ``node_id(int)`` - Node id

    ``Return type:`` Empty response

**PUT /v1/nodes/(node_id)**
    Updates a field of the node. Only *state* field can be changed (*ready* or *hold* values). **(Authentication required)**

    ``Parameters:``

      - ``node_id(int)`` - Node id
      - ``state(string)`` - State within the request body (*ready* or *hold* values)

    ``Return type:`` Empty response

Images
------

**GET /v1/images**
    Returns a list of images

    ``Parameters:`` None

    ``Return type:`` JSON list of *SnapshotImage* objects

**GET /v1/images/(image_id)**
    Returns an image

    ``Parameters:``

      - ``image_id(int)`` - Image id

    ``Return type:`` JSON representation of *SnapshotImage* object

**DELETE /v1/images/(image_id)**
    Deletes an image **(Authentication required)**


    ``Parameters:``

      - ``image_id(int)`` - Image id

    ``Return type:`` Empty response

**POST /v1/images/(image_id)/update**
    Updates an image on provider specified by *provider_name* **(Authentication required)**


    ``Parameters:``

      - ``image_id(int)`` - Image id
      - ``provider_name(string)`` - Provider name within the request body

    ``Return type:`` Empty response

Dib images
----------

**GET /v1/dib_images**
    Returns a list of dib images

    ``Parameters:`` None

    ``Return type:`` JSON list of *DibImage* objects

**GET /v1/dib_images/(dib_image_id)**
    Returns a dib image

    ``Parameters: dib_image_id(int)`` - A dib image id

    ``Return type:`` JSON representation of *DibImage* object

**DELETE /v1/dib_images/(dib_image_id)**
    Deletes a dib image **(Authentication required)**


    ``Parameters:``

      - ``dib_image_id(int)`` - Dib image id

    ``Return type:`` Empty response

**POST /v1/dib_images/(dib_image_name)/build**
    Builds a dib image **(Authentication required)**


    ``Parameters:``

      - ``dib_image_name(string)`` - Dib image name

    ``Return type:`` Empty response

**POST /v1/dib_images/(dib_image_name)/upload**
    Upload a dib image on provider specified by *provider_name* **(Authentication required)**


    ``Parameters:``

      - ``dib_image_name(string)`` - Dib image name
      - ``provider_name(string)`` - Provider name within the request body

    ``Return type:`` Empty response
