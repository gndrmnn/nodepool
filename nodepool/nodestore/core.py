# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Main jump off module to handle different node storage backends."""

from nodepool.nodestore import nodedb


class NodeStore(object):
    """Default pivot point for the Node Storage backend.

    Extra business logic can be layered above the storage backend specifics
    to normalize the interfaces if required. It is assumed any driver method
    undefined on this object will be passed through directly.
    """

    def __init__(self, uri):
        """NodeStore Object.

        :param uri: The connection uri to use. The NodeStore pivot point
                    understands the standard connection string URI convention.
                    The connection strings understood by this pivot point are:
                      - SQL e.g. mysql+pymsql://<user>:<pass>@<host>/<dbname>
        """
        self._configure_driver(uri)

    def _configure_driver(self, uri):
        # TODO(notmorgan): Once more than SQL-connection strings are supported
        # add early detection for non-SQL-connection-string types (e.g.
        # ``zookeeper://...`` and load the correct backend for that. Until
        # more than SQL options are supported, pass through straight to the
        # NodeDatabase specific backend.
        self.driver = nodedb.NodeDatabase(dburi=uri)


    def __getattr__(self, name):
        """Forward calls to the underlying driver."""
        f = getattr(self.driver, name)
        # NOTE(notmorgan): We setattr here since there is no reason to do
        # the hard lookup every time.
        setattr(self, name, f)
        return f
