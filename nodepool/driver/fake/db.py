# Copyright (C) 2019 Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import jsonpickle
import logging
import munch


class Database:
    '''
    A class to represent a provider data set that can be shared among threads.
    '''

    log = logging.getLogger("fake.Database")

    FLAVOR_PATH = "/flavors/"

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            raise Exception("Database has no ZooKeeper client")
        else:
            return self._client

    def setZK(self, client):
        self.log.debug("Starting fake db with ZK session ID %s at chroot %s",
                       client.client_id[0], client.chroot)
        self._client = client

    # ----------------------------------------------------------------------
    # create* Methods
    # ----------------------------------------------------------------------
    def createFlavor(self, flavor):
        """
        Create a new flavor.

        :param dict flavor: Provider flavor representation.
        """
        frozen = jsonpickle.encode(flavor)
        self.client.create(self.FLAVOR_PATH,
                           value=json.dumps(frozen).encode('utf8'),
                           makepath=True,
                           sequence=True)

    def createImage(self):
        pass

    def createInstance(self):
        pass

    def createPort(self):
        pass

    # ----------------------------------------------------------------------
    # list* Methods
    # ----------------------------------------------------------------------
    def listFlavors(self):
        """
        List all recorded flavors.

        :returns: A list of flavor Munch objects.
        """
        flavors = []
        for znode in self.client.get_children(self.FLAVOR_PATH):
            self.log.debug("Flavor node %s", znode)
            data, _ = self.client.get(self.FLAVOR_PATH + znode)
            if data:
                flavor = jsonpickle.decode(json.loads(data.decode('utf8')))
                flavors.append(munch.Munch(flavor))
        return flavors

    def listImages(self):
        pass

    def listInstances(self):
        pass

    def listPorts(self):
        pass

    # ----------------------------------------------------------------------
    # get* Methods
    # ----------------------------------------------------------------------
    def getFlavor(self):
        pass

    def getImage(self):
        pass

    def getInstance(self):
        pass

    def getPort(self):
        pass
