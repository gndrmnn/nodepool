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

"""Initial Revision

Revision ID: 001
Revises: None
Create Date: 2014-11-22 15:16:31.462470

"""

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None


from alembic import op
import sqlalchemy as sa

MYSQL_ENGINE = 'InnoDB'
MYSQL_CHARSET = 'utf8'


def upgrade(active_plugins=None, options=None):

    op.create_table(
        'subnode',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=255), nullable=True),
        sa.Column('ip_private', sa.String(length=255), nullable=True),
        sa.Column('state', sa.Integer(), nullable=True),
        sa.Column('state_time', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine=MYSQL_ENGINE,
        mysql_charset=MYSQL_CHARSET,
    )
    op.create_index(
        u'ix_subnode_hostname', 'subnode', ['hostname'], unique=False)
    op.create_index(
        u'ix_subnode_node_id', 'subnode', ['node_id'], unique=False)

    op.create_table(
        'snapshot_image',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider_name', sa.String(length=255), nullable=False),
        sa.Column('image_name', sa.String(length=255), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('server_external_id', sa.String(length=255), nullable=True),
        sa.Column('state', sa.Integer(), nullable=True),
        sa.Column('state_time', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine=MYSQL_ENGINE,
        mysql_charset=MYSQL_CHARSET,
    )
    op.create_index(
        u'ix_snapshot_image_image_name', 'snapshot_image', ['image_name'],
        unique=False)
    op.create_index(
        u'ix_snapshot_image_provider_name',
        'snapshot_image', ['provider_name'], unique=False)

    op.create_table(
        'dib_image',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('image_name', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('state', sa.Integer(), nullable=True),
        sa.Column('state_time', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine=MYSQL_ENGINE,
        mysql_charset=MYSQL_CHARSET,
    )
    op.create_index(
        u'ix_dib_image_image_name', 'dib_image', ['image_name'], unique=False)

    op.create_table(
        'node',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider_name', sa.String(length=255), nullable=False),
        sa.Column('label_name', sa.String(length=255), nullable=False),
        sa.Column('target_name', sa.String(length=255), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=True),
        sa.Column('nodename', sa.String(length=255), nullable=True),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('az', sa.String(length=255), nullable=True),
        sa.Column('ip', sa.String(length=255), nullable=True),
        sa.Column('ip_private', sa.String(length=255), nullable=True),
        sa.Column('state', sa.Integer(), nullable=True),
        sa.Column('state_time', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine=MYSQL_ENGINE,
        mysql_charset=MYSQL_CHARSET,
    )
    op.create_index(u'ix_node_hostname', 'node', ['hostname'], unique=False)
    op.create_index(
        u'ix_node_label_name', 'node', ['label_name'], unique=False)
    op.create_index(u'ix_node_nodename', 'node', ['nodename'], unique=False)
    op.create_index(
        u'ix_node_provider_name', 'node', ['provider_name'], unique=False)
    op.create_index(
        u'ix_node_target_name', 'node', ['target_name'], unique=False)


def downgrade(active_plugins=None, options=None):

    op.drop_index(u'ix_node_target_name', table_name='node')
    op.drop_index(u'ix_node_provider_name', table_name='node')
    op.drop_index(u'ix_node_nodename', table_name='node')
    op.drop_index(u'ix_node_label_name', table_name='node')
    op.drop_index(u'ix_node_hostname', table_name='node')
    op.drop_table('node')
    op.drop_index(u'ix_dib_image_image_name', table_name='dib_image')
    op.drop_table('dib_image')
    op.drop_index(
        u'ix_snapshot_image_provider_name', table_name='snapshot_image')
    op.drop_index(
        u'ix_snapshot_image_image_name', table_name='snapshot_image')
    op.drop_table('snapshot_image')
    op.drop_index(u'ix_subnode_node_id', table_name='subnode')
    op.drop_index(u'ix_subnode_hostname', table_name='subnode')
    op.drop_table('subnode')
