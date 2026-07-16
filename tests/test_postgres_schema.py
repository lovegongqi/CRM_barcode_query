from crm_storage.migrate import run_migrations
from crm_storage.postgres_store import PostgresStore


def test_initial_schema_contains_required_tables(pg_connection):
    run_migrations(pg_connection)

    names = {row[0] for row in pg_connection.execute("select tablename from pg_tables where schemaname='public'")}

    assert {
        "schema_migrations", "barcode_reports", "app_entities", "operation_logs",
        "sync_events", "sync_cursors", "sync_tombstones"
    } <= names


def test_migrations_are_idempotent(pg_connection):
    run_migrations(pg_connection)
    run_migrations(pg_connection)

    versions = list(pg_connection.execute("select version from schema_migrations"))

    assert versions == [("001_initial.sql",)]


def test_postgres_store_opens_pool_and_provides_transactions(monkeypatch, pg_connection):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", "test-only-key-that-is-not-a-production-secret")
    store = PostgresStore("postgresql://crm:test@127.0.0.1:55433/crm_test")

    try:
        with store.transaction() as connection:
            assert connection.execute("select 1").fetchone() == (1,)
    finally:
        store.close()

    assert store.pool.closed
