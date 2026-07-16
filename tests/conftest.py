import os

import psycopg
import pytest


@pytest.fixture
def pg_connection():
    database_url = os.environ.get("TEST_DATABASE_URL", "postgresql://crm:test@127.0.0.1:55433/crm_test")
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
        yield connection
