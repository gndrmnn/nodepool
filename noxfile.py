# Copyright 2022 Acme Gating, LLC
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

import os

import nox


nox.options.error_on_external_run = True
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["tests-3", "linters"]


def set_env(session, var, default):
    session.env[var] = os.environ.get(var, default)


def set_standard_env_vars(session):
    set_env(session, 'OS_LOG_CAPTURE', '1')
    set_env(session, 'OS_STDERR_CAPTURE', '1')
    set_env(session, 'OS_STDOUT_CAPTURE', '1')
    set_env(session, 'OS_TEST_TIMEOUT', '360')
    set_env(session, 'SQLALCHEMY_WARN_20', '1')
    # Set STATSD env variables so that statsd code paths are tested.
    set_env(session, 'STATSD_HOST', 'localhost')
    set_env(session, 'STATSD_PORT', '8125')
    session.env['PYTHONWARNINGS'] = ','.join([
        'always::DeprecationWarning:zuul.driver.sql.sqlconnection',
        'always::DeprecationWarning:tests.base',
        'always::DeprecationWarning:tests.unit.test_database',
        'always::DeprecationWarning:zuul.driver.sql.alembic.env',
        'always::DeprecationWarning:zuul.driver.sql.alembic.script',
    ])


@nox.session(python='3')
def bindep(session):
    set_standard_env_vars(session)
    set_env(session, 'SQLALCHEMY_WARN_20', '1')
    session.install('bindep')
    session.run('bindep', 'test')


@nox.session(python='3')
def cover(session):
    set_standard_env_vars(session)
    session.env['PYTHON'] = 'coverage run --source nodepool --parallel-mode'
    session.install('-r', 'requirements.txt',
                    '-r', 'test-requirements.txt')
    session.install('-e', '.')
    session.run('stestr', '--test-path', './nodepool/tests/unit',
                'run', '--no-subunit-trace')
    session.run('coverage', 'combine')
    session.run('coverage', 'html', '-d', 'cover')
    session.run('coverage', 'xml', '-o', 'cover/coverage.xml')


@nox.session(python='3')
def docs(session):
    set_standard_env_vars(session)
    session.install('-r', 'requirements.txt',
                    '-r', 'doc/requirements.txt')
    session.install('-e', '.')
    session.run('sphinx-build', '-E', '-W', '-d', 'doc/build/doctrees',
                '-b', 'html', 'doc/source/', 'doc/build/html')


@nox.session(python='3')
def linters(session):
    set_standard_env_vars(session)
    session.install('flake8')
    session.run('flake8', 'nodepool')


@nox.session(python='3')
def tests(session):
    set_standard_env_vars(session)
    session.install('-r', 'requirements.txt',
                    '-r', 'test-requirements.txt')
    session.install('-e', '.')
    session.run('stestr', '--test-path', './nodepool/tests/unit',
                'run',  '--no-subunit-trace',
                *session.posargs)
    session.run('stestr', 'slowest')


@nox.session(python='3')
def functional_kubernetes(session):
    set_standard_env_vars(session)
    session.install('-r', 'requirements.txt',
                    '-r', 'test-requirements.txt')
    session.install('-e', '.')
    session.run('stestr',
                '--test-path', './nodepool/tests/functional/kubernetes',
                'run',  '--no-subunit-trace',
                *session.posargs)
    session.run('stestr', 'slowest')


@nox.session(python='3')
def functional_openshift(session):
    set_standard_env_vars(session)
    session.install('-r', 'requirements.txt',
                    '-r', 'test-requirements.txt')
    session.install('-e', '.')
    session.run('stestr',
                '--test-path', './nodepool/tests/functional/openshift',
                'run',  '--no-subunit-trace',
                *session.posargs)
    session.run('stestr', 'slowest')


@nox.session(python='3')
def venv(session):
    set_standard_env_vars(session)
    session.install('-r', 'requirements.txt',
                    '-r', 'test-requirements.txt')
    session.install('-e', '.')
    session.run(*session.posargs)
