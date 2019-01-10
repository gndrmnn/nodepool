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


from typing import Any, Dict, NewType, Optional, Tuple
import kazoo.security

# The user provided configuration
Config = Dict[str, str]
# The internal config object
ZkAuth = NewType('ZkAuth', Tuple[str, str])


schema = dict(
    scheme=str,
    username=str,
    password=str,
)


def validate(config: Config) -> bool:
    """Check if a user provided zookeeper auth config is valid

    >>> validate(dict())
    False

    >>> validate(dict(scheme="sasl", username="nodepool"))
    False

    >>> validate(dict(scheme="sasl", username="nodepool", password="secret"))
    True
    """
    return (
        config.get('scheme') == 'digest' and
        set(config.keys()) == {"scheme", "username", "password"}
    ) or (
        config.get('scheme') == 'sasl' and
        set(config.keys()) == {"scheme", "username", "password"}
    ) or False


def read(config: Config) -> ZkAuth:
    """Read the user provided zookeeper auth"""
    return ZkAuth(
        (config['scheme'], config['username'] + ":" + config['password']))


def acl(zk_auth: ZkAuth) -> kazoo.security.ACL:
    """Create a kazoo ACL for the connect or set_acls functions"""
    username, _ = zk_auth[1].split(':')
    return kazoo.security.make_acl(zk_auth[0], username, all=True)


def kazoo_args(zk_auth: Optional[ZkAuth]) -> Dict[str, Any]:
    """Create the auth_data for the connect function"""
    return dict(auth_data=[zk_auth], default_acl=[acl(zk_auth)]) if zk_auth \
        else dict()
