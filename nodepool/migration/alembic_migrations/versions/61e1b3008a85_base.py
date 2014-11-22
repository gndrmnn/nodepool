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
#

"""Base Migration

Revision ID: 61e1b3008a85
Revises: None
Create Date: 2016-06-24 11:35:59.002749

NOTE(notmorgan): Used for testing purposes.
"""

MYSQL_ENGINE = 'InnoDB'
MYSQL_CHARSET = 'utf8'

# revision identifiers, used by Alembic.
revision = '61e1b3008a85'
down_revision = None


def upgrade(active_plugins=None, options=None):
    # Do Nothing, This is an initial migration used
    # to help validate nodepool's automatice migrations work.
    pass


def downgrade(active_plugins=None, options=None):
    # Do Nothing, This is an initial migration used
    # to help validate nodepool's automatice migrations work.
    pass