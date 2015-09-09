# Copyright 2015 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import os
import yaml


class YamlParser(object):

    def __init__(self):
        yaml.add_constructor('!include', self._include_tag)

    def _include_tag(self, loader, node):
        filename = os.path.join(os.path.dirname(loader.name), node.value)

        with file(filename) as inputfile:
            return yaml.load(inputfile)

    def load(self, path):
        return yaml.load(open(path))
