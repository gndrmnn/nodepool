#!/bin/bash
# Copyright 2020 Red Hat, Inc.
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
# See the License for the specific language governing permissions and
# limitations under the License.

# This is a barebone version of the tools/pip.sh that is found in zuul/zuul
# and used to pre-install zuul and ansible before tests.

set -e

pip install $*

# Fail-fast if pip detects conflicts
pip check --use-feature=2020-resolver

# Install Ansible
zuul-manage-ansible -v
