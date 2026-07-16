import os
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from .crypto import CredentialCipher
from .migrate import run_migrations


class PostgresStore:
    def __init__(self, database_url):
        self.database_url = database_url
        self.cipher = CredentialCipher.from_env(require_key=True)
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_SIZE") or 8),
            open=False,
        )
        try:
            self.pool.open(wait=True)
            with self.pool.connection() as connection:
                run_migrations(connection)
        except Exception:
            self.pool.close()
            raise

    @contextmanager
    def transaction(self):
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    def close(self):
        self.pool.close()
