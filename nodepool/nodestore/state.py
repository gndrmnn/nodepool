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

# States:
# The cloud provider is building this machine.  We have an ID, but it's
# not ready for use.
BUILDING = 1
# The machine is ready for use.
READY = 2
# This can mean in-use, or used but complete.
USED = 3
# Delete this machine immediately.
DELETE = 4
# Keep this machine indefinitely.
HOLD = 5
# Acceptance testing (pre-ready)
TEST = 6


STATE_NAMES = {
    BUILDING: 'building',
    READY: 'ready',
    USED: 'used',
    DELETE: 'delete',
    HOLD: 'hold',
    TEST: 'test',
    }

