# Copyright (C) 2020 Red Hat
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

import kazoo.security


class ZKAuth:
    def __init__(self, data):
        self.scheme = 'sasl'
        self.username = data['username']
        self.password = data['password']

    def getACL(self):
        """Create a kazoo ACL for the connect or set_acls functions"""
        return kazoo.security.make_acl(self.scheme, self.username, all=True)

    def getAuthData(self):
        """Create the auth_data for the connect function"""
        return (self.scheme, '%s:%s' % (self.username, self.password))
