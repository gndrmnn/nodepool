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

import collections
import pprint

import alembic
import alembic.autogenerate
import alembic.migration
import sqlalchemy
import sqlalchemy.engine
import sqlalchemy.exc
import sqlalchemy.sql.expression as expr
import sqlalchemy.types

# NOTE(notmorgan): This helper class has been taken from oslo_db and modified
# for the use in nodepool. This is done since nodepool does not (and will not)
# include oslo.* dependencies. This should be removed once the DB usage has
# been replaced with ZooKeeper as planned.
class ModelsMigrationsSync(object):
    """A helper class for comparison of DB migration scripts and models.
    It's intended to be inherited by test cases in target projects. They have
    to provide implementations for methods used internally in the test (as
    we have no way to implement them here).
    test_model_sync() will run migration scripts for the engine provided and
    then compare the given metadata to the one reflected from the database.
    The difference between MODELS and MIGRATION scripts will be printed and
    the test will fail, if the difference is not empty. The return value is
    really a list of actions, that should be performed in order to make the
    current database schema state (i.e. migration scripts) consistent with
    models definitions. It's left up to developers to analyze the output and
    decide whether the models definitions or the migration scripts should be
    modified to make them consistent.
    Output::
        [(
            'add_table',
            description of the table from models
        ),
        (
            'remove_table',
            description of the table from database
        ),
        (
            'add_column',
            schema,
            table name,
            column description from models
        ),
        (
            'remove_column',
            schema,
            table name,
            column description from database
        ),
        (
            'add_index',
            description of the index from models
        ),
        (
            'remove_index',
            description of the index from database
        ),
        (
            'add_constraint',
            description of constraint from models
        ),
        (
            'remove_constraint,
            description of constraint from database
        ),
        (
            'modify_nullable',
            schema,
            table name,
            column name,
            {
                'existing_type': type of the column from database,
                'existing_server_default': default value from database
            },
            nullable from database,
            nullable from models
        ),
        (
            'modify_type',
            schema,
            table name,
            column name,
            {
                'existing_nullable': database nullable,
                'existing_server_default': default value from database
            },
            database column type,
            type of the column from models
        ),
        (
            'modify_default',
            schema,
            table name,
            column name,
            {
                'existing_nullable': database nullable,
                'existing_type': type of the column from database
            },
            connection column default value,
            default from models
        )]
    Method include_object() can be overridden to exclude some tables from
    comparison (e.g. migrate_repo).
    """

    def include_object(self, object_, name, type_, reflected, compare_to):
        """Return True for objects that should be compared.
        :param object_: a SchemaItem object such as a Table or Column object
        :param name: the name of the object
        :param type_: a string describing the type of object (e.g. "table")
        :param reflected: True if the given object was produced based on
                          table reflection, False if it's from a local
                          MetaData object
        :param compare_to: the object being compared against, if available,
                           else None
        """

        return True

    def compare_type(self, ctxt, insp_col, meta_col, insp_type, meta_type):
        """Return True if types are different, False if not.
        Return None to allow the default implementation to compare these types.
        :param ctxt: alembic MigrationContext instance
        :param insp_col: reflected column
        :param meta_col: column from model
        :param insp_type: reflected column type
        :param meta_type: column type from model
        """

        # some backends (e.g. mysql) don't provide native boolean type
        BOOLEAN_METADATA = (sqlalchemy.types.BOOLEAN,
                            sqlalchemy.types.Boolean)
        BOOLEAN_SQL = BOOLEAN_METADATA + (sqlalchemy.types.INTEGER,
                                          sqlalchemy.types.Integer)

        if issubclass(type(meta_type), BOOLEAN_METADATA):
            return not issubclass(type(insp_type), BOOLEAN_SQL)

        # Alembic <=0.8.4 do not contain logic of comparing Variant type with
        # others.
        if isinstance(meta_type, sqlalchemy.types.Variant):
            orig_type = meta_col.type
            impl_type = meta_type.load_dialect_impl(ctxt.dialect)
            meta_col.type = impl_type
            try:
                return self.compare_type(ctxt, insp_col, meta_col, insp_type,
                                         impl_type)
            finally:
                meta_col.type = orig_type

        return ctxt.impl.compare_type(insp_col, meta_col)

    def compare_server_default(self, ctxt, ins_col, meta_col,
                               insp_def, meta_def, rendered_meta_def):
        """Compare default values between model and db table.
        Return True if the defaults are different, False if not, or None to
        allow the default implementation to compare these defaults.
        :param ctxt: alembic MigrationContext instance
        :param insp_col: reflected column
        :param meta_col: column from model
        :param insp_def: reflected column default value
        :param meta_def: column default value from model
        :param rendered_meta_def: rendered column default value (from model)
        """
        return self._compare_server_default(ctxt.bind, meta_col, insp_def,
                                            meta_def)

    def _compare_server_default(self, bind, meta_col, insp_def, meta_def):
        if isinstance(meta_col.type, sqlalchemy.Boolean):
            if meta_def is None or insp_def is None:
                return meta_def != insp_def
            return not (
                isinstance(meta_def.arg, expr.True_) and insp_def == "'1'" or
                isinstance(meta_def.arg, expr.False_) and insp_def == "'0'"
            )

        impl_type = meta_col.type
        if isinstance(impl_type, sqlalchemy.types.Variant):
            impl_type = impl_type.load_dialect_impl(bind.dialect)
        if isinstance(impl_type, (sqlalchemy.Integer, sqlalchemy.BigInteger)):
            if meta_def is None or insp_def is None:
                return meta_def != insp_def
            return meta_def.arg != insp_def.split("'")[1]

    FKInfo = collections.namedtuple('fk_info', ['constrained_columns',
                                                'referred_table',
                                                'referred_columns'])

    def check_foreign_keys(self, metadata, bind):
        """Compare foreign keys between model and db table.
        :returns: a list that contains information about:
         * should be a new key added or removed existing,
         * name of that key,
         * source table,
         * referred table,
         * constrained columns,
         * referred columns
         Output::
             [('drop_key',
               'testtbl_fk_check_fkey',
               'testtbl',
               fk_info(constrained_columns=(u'fk_check',),
                       referred_table=u'table',
                       referred_columns=(u'fk_check',)))]
        DEPRECATED: this function is deprecated and will be removed from
        oslo.db in a few releases. Alembic autogenerate.compare_metadata()
        now includes foreign key comparison directly.
        """

        diff = []
        insp = sqlalchemy.engine.reflection.Inspector.from_engine(bind)
        # Get all tables from db
        db_tables = insp.get_table_names()
        # Get all tables from models
        model_tables = metadata.tables
        for table in db_tables:
            if table not in model_tables:
                continue
            # Get all necessary information about key of current table from db
            fk_db = dict((self._get_fk_info_from_db(i), i['name'])
                         for i in insp.get_foreign_keys(table))
            fk_db_set = set(fk_db.keys())
            # Get all necessary information about key of current table from
            # models
            fk_models = dict((self._get_fk_info_from_model(fk), fk)
                             for fk in model_tables[table].foreign_keys)
            fk_models_set = set(fk_models.keys())
            for key in (fk_db_set - fk_models_set):
                diff.append(('drop_key', fk_db[key], table, key))

            for key in (fk_models_set - fk_db_set):
                diff.append(('add_key', fk_models[key], table, key))

        return diff

    def _get_fk_info_from_db(self, fk):
        return self.FKInfo(tuple(fk['constrained_columns']),
                           fk['referred_table'],
                           tuple(fk['referred_columns']))

    def _get_fk_info_from_model(self, fk):
        return self.FKInfo((fk.parent.name,), fk.column.table.name,
                           (fk.column.name,))

    def filter_metadata_diff(self, diff):
        """Filter changes before assert in test_models_sync().
        Allow subclasses to whitelist/blacklist changes. By default, no
        filtering is performed, changes are returned as is.
        :param diff: a list of differences (see `compare_metadata()` docs for
                     details on format)
        :returns: a list of differences
        """

        return diff

    def assertDBSchemaMatchesModels(self, engine, metadata):


        with engine.connect() as conn:
            opts = {
                'include_object': self.include_object,
                'compare_type': self.compare_type,
                'compare_server_default': self.compare_server_default,
            }
            mc = alembic.migration.MigrationContext.configure(conn, opts=opts)

            # compare schemas and fail with diff, if it's not empty
            diff = self.filter_metadata_diff(
                alembic.autogenerate.compare_metadata(mc, metadata))

            if diff:
                msg = pprint.pformat(diff, indent=2, width=20)
                self.fail(
                    "Models and migration scripts aren't in sync:\n%s" % msg)