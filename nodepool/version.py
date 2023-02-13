# Copyright 2020 Red Hat, inc
#
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

import json

from importlib import metadata as importlib_metadata
import pkg_resources

release_string = importlib_metadata.distribution('nodepool').version

is_release = None
git_version = None
try:
    _metadata = json.loads(
        pkg_resources.get_distribution('nodepool').get_metadata('pbr.json'))
    if _metadata:
        is_release = _metadata['is_release']
        git_version = _metadata['git_version']
except Exception:
    pass


def get_version_string():
    if is_release:
        return release_string
    return "{} {}".format(release_string, git_version)
