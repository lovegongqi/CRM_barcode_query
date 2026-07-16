import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


DEFAULT_TEST_POSTGRES_ADMIN_DSN = "postgresql://crm:test@127.0.0.1:55433/crm_test"


def validate_test_admin_dsn(dsn):
    params = conninfo_to_dict(dsn)
    database_name = params.get("dbname")
    host = params.get("host")
    if not database_name or not database_name.startswith("crm_test"):
        raise ValueError("TEST_POSTGRES_ADMIN_DSN must target a crm_test database")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("TEST_POSTGRES_ADMIN_DSN must target a local test server")
    return params


@pytest.fixture(scope="session")
def pg_database_url():
    admin_dsn = os.environ.get("TEST_POSTGRES_ADMIN_DSN", DEFAULT_TEST_POSTGRES_ADMIN_DSN)
    params = validate_test_admin_dsn(admin_dsn)
    database_name = f"crm_test_{uuid4().hex}"
    database_url = make_conninfo(**(params | {"dbname": database_name}))

    with psycopg.connect(admin_dsn, autocommit=True) as admin_connection:
        admin_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            server_version_num = int(connection.execute("show server_version_num").fetchone()[0])
            assert server_version_num // 10000 == 17
        yield database_url
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin_connection:
            admin_connection.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (database_name,),
            )
            admin_connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


@pytest.fixture
def pg_connection(pg_database_url):
    with psycopg.connect(pg_database_url, autocommit=True) as connection:
        server_version_num = int(connection.execute("show server_version_num").fetchone()[0])
        assert server_version_num // 10000 == 17
        yield connection
