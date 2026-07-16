import base64
import threading
from contextlib import nullcontext

import psycopg
import pytest

from conftest import validate_test_admin_dsn
from crm_storage import migrate
from crm_storage.migrate import run_migrations
from crm_storage.postgres_store import PostgresStore


VALID_FERNET_KEY = base64.urlsafe_b64encode(b"c" * 32).decode("ascii")


def test_admin_dsn_must_target_local_test_database():
    with pytest.raises(ValueError, match="crm_test"):
        validate_test_admin_dsn("postgresql://crm:test@127.0.0.1:55433/crm_production")

    with pytest.raises(ValueError, match="local test server"):
        validate_test_admin_dsn("postgresql://crm:test@database.example/crm_test")


def test_migrations_are_serialized_across_independent_connections(pg_database_url, monkeypatch, tmp_path):
    migration = tmp_path / "000_lock_test.sql"
    migration.write_text(
        "select pg_sleep(0.2);\n"
        "create table schema_migrations (version text primary key);\n"
        "create table migration_lock_marker (id integer primary key);\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    barrier = threading.Barrier(3)
    errors = []

    def apply_migrations():
        try:
            with psycopg.connect(pg_database_url, autocommit=True) as connection:
                server_version_num = int(connection.execute("show server_version_num").fetchone()[0])
                assert server_version_num // 10000 == 17
                barrier.wait()
                run_migrations(connection)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=apply_migrations) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert not errors
    with psycopg.connect(pg_database_url, autocommit=True) as connection:
        assert list(connection.execute("select version from schema_migrations")) == [("000_lock_test.sql",)]
        connection.execute("drop table migration_lock_marker")
        connection.execute("drop table schema_migrations")


def test_initial_schema_contains_required_tables(pg_connection):
    run_migrations(pg_connection)

    names = {row[0] for row in pg_connection.execute("select tablename from pg_tables where schemaname = 'public'")}

    assert {
        "schema_migrations", "barcode_reports", "app_entities", "operation_logs",
        "sync_events", "sync_cursors", "sync_tombstones"
    } <= names


def test_initial_schema_catalog_matches_required_contract(pg_connection):
    run_migrations(pg_connection)
    rows = pg_connection.execute(
        "select table_name, column_name, data_type, is_nullable, column_default "
        "from information_schema.columns where table_schema = 'public' "
        "order by table_name, ordinal_position"
    )
    columns = {(table, column): (data_type, nullable, default) for table, column, data_type, nullable, default in rows}

    assert columns == {
        ("app_entities", "kind"): ("text", "NO", None),
        ("app_entities", "entity_key"): ("text", "NO", None),
        ("app_entities", "payload"): ("jsonb", "NO", "'{}'::jsonb"),
        ("app_entities", "origin_node"): ("text", "NO", None),
        ("app_entities", "updated_at"): ("timestamp with time zone", "NO", "now()"),
        ("app_entities", "deleted_at"): ("timestamp with time zone", "YES", None),
        ("app_entities", "delete_event_id"): ("uuid", "YES", None),
        ("barcode_reports", "barcode"): ("text", "NO", None),
        ("barcode_reports", "html_gzip"): ("bytea", "YES", None),
        ("barcode_reports", "archived"): ("boolean", "NO", "false"),
        ("barcode_reports", "query_slot"): ("text", "NO", "''::text"),
        ("barcode_reports", "origin_node"): ("text", "NO", None),
        ("barcode_reports", "created_at"): ("timestamp with time zone", "NO", "now()"),
        ("barcode_reports", "updated_at"): ("timestamp with time zone", "NO", "now()"),
        ("barcode_reports", "deleted_at"): ("timestamp with time zone", "YES", None),
        ("barcode_reports", "delete_event_id"): ("uuid", "YES", None),
        ("operation_logs", "id"): ("uuid", "NO", None),
        ("operation_logs", "category"): ("text", "NO", None),
        ("operation_logs", "level"): ("text", "NO", None),
        ("operation_logs", "message"): ("text", "NO", None),
        ("operation_logs", "context"): ("jsonb", "NO", "'{}'::jsonb"),
        ("operation_logs", "origin_node"): ("text", "NO", None),
        ("operation_logs", "created_at"): ("timestamp with time zone", "NO", "now()"),
        ("schema_migrations", "version"): ("text", "NO", None),
        ("schema_migrations", "applied_at"): ("timestamp with time zone", "NO", "now()"),
        ("sync_cursors", "peer_node"): ("text", "NO", None),
        ("sync_cursors", "origin_node"): ("text", "NO", None),
        ("sync_cursors", "last_sequence"): ("bigint", "NO", "0"),
        ("sync_cursors", "updated_at"): ("timestamp with time zone", "NO", "now()"),
        ("sync_events", "event_id"): ("uuid", "NO", None),
        ("sync_events", "origin_node"): ("text", "NO", None),
        ("sync_events", "local_sequence"): ("bigint", "NO", None),
        ("sync_events", "site_epoch"): ("bigint", "NO", None),
        ("sync_events", "entity_type"): ("text", "NO", None),
        ("sync_events", "entity_key"): ("text", "NO", None),
        ("sync_events", "operation"): ("text", "NO", None),
        ("sync_events", "payload"): ("jsonb", "NO", "'{}'::jsonb"),
        ("sync_events", "blob_gzip"): ("bytea", "YES", None),
        ("sync_events", "created_at"): ("timestamp with time zone", "NO", "now()"),
        ("sync_tombstones", "entity_type"): ("text", "NO", None),
        ("sync_tombstones", "entity_key"): ("text", "NO", None),
        ("sync_tombstones", "delete_event_id"): ("uuid", "NO", None),
        ("sync_tombstones", "origin_node"): ("text", "NO", None),
        ("sync_tombstones", "deleted_at"): ("timestamp with time zone", "NO", None),
        ("sync_tombstones", "hk_ack_at"): ("timestamp with time zone", "YES", None),
        ("sync_tombstones", "sg_ack_at"): ("timestamp with time zone", "YES", None),
    }

    constraints = {(table, constraint, definition) for table, constraint, definition in pg_connection.execute(
        "select conrelid::regclass::text, contype, pg_get_constraintdef(oid) "
        "from pg_constraint where connamespace = 'public'::regnamespace"
    )}
    assert {
        ("schema_migrations", "p", "PRIMARY KEY (version)"),
        ("barcode_reports", "p", "PRIMARY KEY (barcode)"),
        ("app_entities", "p", "PRIMARY KEY (kind, entity_key)"),
        ("operation_logs", "p", "PRIMARY KEY (id)"),
        ("sync_events", "p", "PRIMARY KEY (event_id)"),
        ("sync_events", "u", "UNIQUE (origin_node, local_sequence)"),
        ("sync_cursors", "p", "PRIMARY KEY (peer_node)"),
        ("sync_tombstones", "p", "PRIMARY KEY (entity_type, entity_key)"),
        ("sync_tombstones", "u", "UNIQUE (delete_event_id)"),
        ("sync_events", "c", "CHECK ((operation = ANY (ARRAY['upsert'::text, 'delete'::text])))"),
    } <= constraints
    assert pg_connection.execute("select to_regclass('public.sync_local_sequence')").fetchone()[0] == "sync_local_sequence"

    indexes = {name: definition for name, definition in pg_connection.execute(
        "select indexname, indexdef from pg_indexes where schemaname = 'public'"
    )}
    assert indexes["barcode_reports_updated_at_idx"].endswith("(updated_at)")
    assert indexes["app_entities_kind_deleted_at_idx"].endswith("(kind, deleted_at)")
    assert indexes["operation_logs_category_created_at_idx"].endswith("(category, created_at)")
    assert indexes["sync_events_origin_node_local_sequence_idx"].endswith("(origin_node, local_sequence)")


def test_migrations_are_idempotent(pg_connection):
    run_migrations(pg_connection)
    run_migrations(pg_connection)

    versions = list(pg_connection.execute("select version from schema_migrations order by version"))

    assert versions == [("001_initial.sql",)]


class RecordingPool:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        RecordingPool.instances.append(self)

    def open(self, wait):
        self.open_wait = wait

    def connection(self):
        return nullcontext(object())

    def close(self):
        self.closed = True


@pytest.mark.parametrize(("pool_size", "expected"), [(None, 8), ("", 8), ("3", 3)])
def test_postgres_store_uses_expected_pool_size(monkeypatch, pg_database_url, pool_size, expected):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    if pool_size is None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    else:
        monkeypatch.setenv("DB_POOL_SIZE", pool_size)
    monkeypatch.setattr("crm_storage.postgres_store.ConnectionPool", RecordingPool)
    monkeypatch.setattr("crm_storage.postgres_store.run_migrations", lambda connection: None)
    RecordingPool.instances.clear()

    store = PostgresStore(pg_database_url)

    assert store.pool.kwargs["max_size"] == expected
    store.close()


def test_postgres_store_closes_pool_when_open_fails(monkeypatch, pg_database_url):
    class OpenFailingPool(RecordingPool):
        def open(self, wait):
            raise RuntimeError("open failed")

    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    monkeypatch.setattr("crm_storage.postgres_store.ConnectionPool", OpenFailingPool)
    RecordingPool.instances.clear()

    with pytest.raises(RuntimeError, match="open failed"):
        PostgresStore(pg_database_url)

    assert RecordingPool.instances[0].closed


def test_postgres_store_closes_pool_when_migration_fails(monkeypatch, pg_database_url):
    def fail_migration(connection):
        raise RuntimeError("migration failed")

    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    monkeypatch.setattr("crm_storage.postgres_store.ConnectionPool", RecordingPool)
    monkeypatch.setattr("crm_storage.postgres_store.run_migrations", fail_migration)
    RecordingPool.instances.clear()

    with pytest.raises(RuntimeError, match="migration failed"):
        PostgresStore(pg_database_url)

    assert RecordingPool.instances[0].closed


def test_postgres_store_opens_pool_and_provides_transactions(monkeypatch, pg_database_url):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    store = PostgresStore(pg_database_url)

    try:
        with store.transaction() as connection:
            server_version_num = int(connection.execute("show server_version_num").fetchone()[0])
            assert server_version_num // 10000 == 17
            assert connection.execute("select 1").fetchone() == (1,)
    finally:
        store.close()

    assert store.pool.closed
