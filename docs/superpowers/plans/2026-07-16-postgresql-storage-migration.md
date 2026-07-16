# PostgreSQL Storage Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PostgreSQL the server-side source of truth for all business data while preserving the existing local-file backend for Windows and macOS desktop builds.

**Architecture:** Introduce a small `crm_storage` boundary with explicit domain methods. Existing Flask routes and CRM automation keep their behavior but call the selected backend. `DATABASE_URL` selects PostgreSQL; absence of that variable selects the existing file layout. PostgreSQL writes business state and an idempotent sync outbox in the same transaction so phase two can replicate safely.

**Tech Stack:** Python 3.11, Flask 3, psycopg 3 connection pool, PostgreSQL 17, cryptography/Fernet, pytest, Docker Compose.

## Global Constraints

- Server mode uses PostgreSQL; desktop mode without `DATABASE_URL` continues using local files.
- Store compressed CRM report HTML in PostgreSQL and preserve full report rendering.
- Persist barcode data, matching rules, distributors, accounts, configuration, encrypted CRM credentials, and operation logs.
- Chromium profiles remain under `CRM_SESSION_BASE` and never enter PostgreSQL.
- Deletion writes a tombstone and sync event; it must not immediately erase the entity key.
- Existing Docker volumes are never deleted or overwritten by build/update commands.
- Do not commit passwords, API tokens, encryption keys, database dumps, or CRM sessions.

---

## File Map

- `crm_storage/base.py`: typed storage contract and shared records.
- `crm_storage/factory.py`: selects file or PostgreSQL backend once per process.
- `crm_storage/file_store.py`: preserves the current JSON/HTML directory behavior.
- `crm_storage/postgres_store.py`: PostgreSQL implementation and transactional outbox.
- `crm_storage/crypto.py`: encrypts/decrypts remembered CRM credentials.
- `crm_storage/migrations/001_initial.sql`: business tables, tombstones, logs, sync events, and cursors.
- `crm_storage/migrate.py`: ordered SQL migration runner.
- `scripts/migrate_files_to_postgres.py`: idempotent import and verification command.
- `scripts/backup_postgres.sh`: encrypted, checksummed daily dump.
- `scripts/verify_postgres_restore.sh`: restores a dump into a disposable database and verifies it.
- `deployment/docker-compose.server.yml`: app and PostgreSQL services without changing desktop packaging.
- `deployment/server.env.example`: required non-secret deployment variables and secret variable names.
- `app.py`: delegates persistence to `crm_storage`; CRM browser automation remains here.
- `requirements.txt`: production PostgreSQL, encryption, and HTTP dependencies.
- `requirements-dev.txt`: pytest and test helpers.
- `tests/`: focused unit and PostgreSQL integration tests.

---

### Task 1: Add the storage contract and backend selection

**Files:**
- Create: `crm_storage/__init__.py`
- Create: `crm_storage/base.py`
- Create: `crm_storage/factory.py`
- Create: `crm_storage/file_store.py`
- Create: `crm_storage/postgres_store.py`
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_storage_factory.py`

**Interfaces:**
- Produces: `StorageBackend`, `ReportRecord`, `EntityRecord`, `select_store_class()`, `get_store()`, and `reset_store_for_tests()`.
- `get_store()` returns one process-wide backend selected by `DATABASE_URL`.

- [ ] **Step 1: Add the failing backend-selection tests**

```python
# tests/test_storage_factory.py
from crm_storage.factory import reset_store_for_tests, select_store_class


def test_without_database_url_uses_file_store(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path))
    reset_store_for_tests()
    assert select_store_class().__name__ == "FileStore"


def test_database_url_uses_postgres_store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://crm:test@127.0.0.1:55432/crm_test")
    reset_store_for_tests()
    assert select_store_class().__name__ == "PostgresStore"
```

- [ ] **Step 2: Run the focused tests and confirm the missing-module failure**

Run: `python -m pytest tests/test_storage_factory.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'crm_storage'`.

- [ ] **Step 3: Define the explicit storage contract**

```python
# crm_storage/base.py
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ReportRecord:
    barcode: str
    html: bytes
    archived: bool
    query_slot: str
    updated_at: str


@dataclass(frozen=True)
class EntityRecord:
    key: str
    payload: dict[str, Any]
    deleted: bool
    updated_at: str


class StorageBackend(Protocol):
    def list_reports(self, archived: bool = False) -> list[str]: ...
    def get_report(self, barcode: str) -> ReportRecord | None: ...
    def put_report(self, record: ReportRecord) -> None: ...
    def delete_report(self, barcode: str, actor: str = "system") -> bool: ...
    def load_entities(self, kind: str, include_deleted: bool = False) -> list[EntityRecord]: ...
    def get_entity(self, kind: str, key: str) -> EntityRecord | None: ...
    def put_entity(self, kind: str, key: str, payload: dict[str, Any], actor: str = "system") -> None: ...
    def delete_entity(self, kind: str, key: str, actor: str = "system") -> bool: ...
    def append_log(self, category: str, level: str, message: str, context: dict[str, Any] | None = None) -> str: ...
    def list_logs(self, category: str, limit: int = 500) -> list[dict[str, Any]]: ...
```

- [ ] **Step 4: Implement lazy backend selection**

```python
# crm_storage/factory.py
import os
import threading

_store = None
_lock = threading.Lock()


def select_store_class():
    if os.environ.get("DATABASE_URL"):
        from .postgres_store import PostgresStore
        return PostgresStore
    from .file_store import FileStore
    return FileStore


def get_store():
    global _store
    with _lock:
        if _store is None:
            store_class = select_store_class()
            argument = os.environ["DATABASE_URL"] if os.environ.get("DATABASE_URL") else os.environ.get("CRM_DATA_DIR")
            _store = store_class(argument)
        return _store


def reset_store_for_tests():
    global _store
    with _lock:
        close = getattr(_store, "close", None)
        if close:
            close()
        _store = None
```

Add constructor-only stubs so backend selection is importable before later tasks fill in behavior:

```python
# crm_storage/file_store.py
class FileStore:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir
```

```python
# crm_storage/postgres_store.py
class PostgresStore:
    def __init__(self, database_url):
        self.database_url = database_url
```

- [ ] **Step 5: Add development dependencies and make the tests pass**

```text
# requirements-dev.txt
-r requirements.txt
pytest>=8.3,<9
```

Run: `python -m pytest tests/test_storage_factory.py -q`

Expected: `2 passed`; full backend methods arrive in later tasks.

- [ ] **Step 6: Commit the boundary**

```bash
git add crm_storage requirements-dev.txt tests
git commit -m "feat: add storage backend boundary"
```

---

### Task 2: Preserve the current file backend behavior

**Files:**
- Modify: `crm_storage/file_store.py`
- Create: `tests/test_file_store.py`
- Modify: `app.py:3370-3470, 4600-4780, 5178-5215, 5881-6220`

**Interfaces:**
- Consumes: `StorageBackend`, `ReportRecord`, and `EntityRecord` from Task 1.
- Produces: `FileStore`, which uses `CRM_DATA_DIR/barcode`, `CRM_DATA_DIR/config`, and `CRM_DATA_DIR/results` exactly as the current app does.

- [ ] **Step 1: Capture current file semantics in failing tests**

```python
# tests/test_file_store.py
from crm_storage.base import ReportRecord
from crm_storage.file_store import FileStore


def test_report_round_trip_and_soft_delete(tmp_path):
    store = FileStore(str(tmp_path))
    store.put_report(ReportRecord("5312503010858", b"<html>ok</html>", False, "query-2", "2026-07-16 10:00:00"))
    assert store.list_reports() == ["5312503010858"]
    assert store.get_report("5312503010858").html == b"<html>ok</html>"
    assert store.delete_report("5312503010858", "admin") is True
    assert store.get_report("5312503010858") is None


def test_entities_keep_chinese_and_deleted_keys(tmp_path):
    store = FileStore(str(tmp_path))
    store.put_entity("product_rule", "845", {"product_code": "906020907", "product_name": "壁挂式饮水机"})
    store.delete_entity("product_rule", "845", "admin")
    row = store.get_entity("product_rule", "845")
    assert row.deleted is True
    assert row.payload["product_name"] == "壁挂式饮水机"
```

- [ ] **Step 2: Run the tests and verify the stub fails**

Run: `python -m pytest tests/test_file_store.py -q`

Expected: FAIL because `FileStore` does not implement report/entity operations.

- [ ] **Step 3: Implement atomic JSON and HTML writes**

Implement `FileStore` with `tempfile.NamedTemporaryFile`, `os.replace`, UTF-8 JSON, and a per-instance `RLock`. Map entity kinds exactly:

```python
ENTITY_FILES = {
    "barcode_metadata": "barcode_data.json",
    "product_rule": "product_library.json",
    "account": "accounts.json",
    "distributor": "distributor_history.json",
    "runtime_setting": "runtime_config.json",
    "crm_credentials": "crm_credentials.json",
}
```

Keep deletion tombstones in `config/tombstones.json`; a file tombstone is `{kind, key, actor, deleted_at, event_id}`. Report HTML remains uncompressed on disk for desktop compatibility.

- [ ] **Step 4: Replace direct file helpers with storage-backed compatibility wrappers**

At app startup add:

```python
from crm_storage.factory import get_store

store = get_store()
```

Keep public helper names such as `load_product_library()`, `save_product_library()`, `load_accounts()`, `save_accounts()`, `load_data()`, `save_data()`, and distributor helpers, but make them translate between their existing return shape and `store` methods. This limits changes in CRM automation and templates.

- [ ] **Step 5: Run file-backend and pure helper regression tests**

Run: `python -m pytest tests/test_file_store.py tests/test_storage_factory.py -q`

Expected: all tests pass and no test creates files outside its temporary `CRM_DATA_DIR`.

- [ ] **Step 6: Smoke-test the desktop/file mode**

Run: `env -u DATABASE_URL CRM_DATA_DIR=/tmp/crm-file-smoke python -c 'import app; print(app.store.__class__.__name__)'`

Expected: final line is `FileStore` and importing `app` does not require PostgreSQL.

- [ ] **Step 7: Commit the file backend**

```bash
git add crm_storage/file_store.py app.py tests/test_file_store.py
git commit -m "refactor: preserve persistence behind file store"
```

---

### Task 3: Add the PostgreSQL schema, migration runner, and credential encryption

**Files:**
- Create: `crm_storage/migrations/001_initial.sql`
- Create: `crm_storage/migrate.py`
- Create: `crm_storage/crypto.py`
- Modify: `crm_storage/postgres_store.py`
- Create: `tests/integration/docker-compose.yml`
- Create: `tests/test_crypto.py`
- Create: `tests/test_postgres_schema.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `run_migrations(connection)`, `CredentialCipher`, and a pooled `PostgresStore`.
- `PostgresStore.transaction()` is the only write transaction entry point used by later tasks.

- [ ] **Step 1: Add failing encryption and schema tests**

```python
# tests/test_crypto.py
from crm_storage.crypto import CredentialCipher


def test_credentials_are_not_stored_as_plaintext():
    cipher = CredentialCipher.from_text_key("test-only-key-that-is-not-a-production-secret")
    token = cipher.encrypt({"username": "gongqi", "password": "secret"})
    assert b"secret" not in token
    assert cipher.decrypt(token)["password"] == "secret"
```

```python
# tests/test_postgres_schema.py
def test_initial_schema_contains_required_tables(pg_connection):
    names = {row[0] for row in pg_connection.execute("select tablename from pg_tables where schemaname='public'")}
    assert {
        "schema_migrations", "barcode_reports", "app_entities", "operation_logs",
        "sync_events", "sync_cursors", "sync_tombstones"
    } <= names
```

- [ ] **Step 2: Run tests and confirm missing implementation**

Run: `python -m pytest tests/test_crypto.py tests/test_postgres_schema.py -q`

Expected: FAIL on missing `CredentialCipher` and missing tables.

- [ ] **Step 3: Add pinned server dependencies**

Append to `requirements.txt`:

```text
psycopg[binary,pool]>=3.2,<4
cryptography>=45,<46
requests>=2.32,<3
```

- [ ] **Step 4: Create the initial schema**

`001_initial.sql` must create:

```sql
CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE barcode_reports (
    barcode text PRIMARY KEY,
    html_gzip bytea,
    archived boolean NOT NULL DEFAULT false,
    query_slot text NOT NULL DEFAULT '',
    origin_node text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    delete_event_id uuid
);

CREATE TABLE app_entities (
    kind text NOT NULL,
    entity_key text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin_node text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    delete_event_id uuid,
    PRIMARY KEY (kind, entity_key)
);

CREATE TABLE operation_logs (
    id uuid PRIMARY KEY,
    category text NOT NULL,
    level text NOT NULL,
    message text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    origin_node text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE sync_local_sequence;
CREATE TABLE sync_events (
    event_id uuid PRIMARY KEY,
    origin_node text NOT NULL,
    local_sequence bigint NOT NULL,
    site_epoch bigint NOT NULL,
    entity_type text NOT NULL,
    entity_key text NOT NULL,
    operation text NOT NULL CHECK (operation IN ('upsert', 'delete')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    blob_gzip bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (origin_node, local_sequence)
);

CREATE TABLE sync_cursors (
    peer_node text PRIMARY KEY,
    origin_node text NOT NULL,
    last_sequence bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sync_tombstones (
    entity_type text NOT NULL,
    entity_key text NOT NULL,
    delete_event_id uuid NOT NULL UNIQUE,
    origin_node text NOT NULL,
    deleted_at timestamptz NOT NULL,
    hk_ack_at timestamptz,
    sg_ack_at timestamptz,
    PRIMARY KEY (entity_type, entity_key)
);
```

Add indexes for report update time, entity kind/deleted state, log category/time, and event origin/sequence.

- [ ] **Step 5: Implement migration and encryption utilities**

`run_migrations()` reads sorted `*.sql`, skips versions already in `schema_migrations`, and applies each file in one transaction. `CredentialCipher.from_env()` must fail fast in PostgreSQL mode when `CRM_CREDENTIALS_KEY` is missing.

- [ ] **Step 6: Implement PostgreSQL connection lifecycle**

Create a `psycopg_pool.ConnectionPool` with `min_size=1`, `max_size=int(DB_POOL_SIZE or 8)`, `open=False`; call `pool.open(wait=True)` and `run_migrations()` in `PostgresStore.__init__`. `close()` closes the pool.

- [ ] **Step 7: Run schema and encryption tests**

Run: `docker compose -f tests/integration/docker-compose.yml up -d`

Expected: PostgreSQL test container reports healthy.

Run: `python -m pytest tests/test_crypto.py tests/test_postgres_schema.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit schema and connection code**

```bash
git add requirements.txt crm_storage tests/integration tests/test_crypto.py tests/test_postgres_schema.py
git commit -m "feat: add PostgreSQL storage foundation"
```

---

### Task 4: Implement transactional entities, reports, tombstones, and outbox events

**Files:**
- Modify: `crm_storage/postgres_store.py`
- Create: `tests/test_postgres_store.py`

**Interfaces:**
- Consumes: PostgreSQL schema and `CredentialCipher` from Task 3.
- Produces: complete `StorageBackend` behavior and `fetch_events(origin_node, after_sequence, limit)` for phase two.

- [ ] **Step 1: Write failing transactional behavior tests**

```python
def test_report_write_and_event_are_atomic(pg_store):
    record = ReportRecord("8452508130954", b"<html>report</html>", False, "query-1", "2026-07-16 10:00:00")
    pg_store.put_report(record)
    assert pg_store.get_report(record.barcode).html == record.html
    events = pg_store.fetch_events("hk", 0, 10)
    assert [(e.entity_type, e.entity_key, e.operation) for e in events] == [
        ("barcode_report", record.barcode, "upsert")
    ]


def test_delete_creates_tombstone_and_hides_report(pg_store):
    pg_store.put_report(ReportRecord("5312503010858", b"x", False, "query-1", "2026-07-16 10:00:00"))
    assert pg_store.delete_report("5312503010858", "admin") is True
    assert pg_store.get_report("5312503010858") is None
    tombstone = pg_store.get_tombstone("barcode_report", "5312503010858")
    assert tombstone.delete_event_id
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run: `python -m pytest tests/test_postgres_store.py -q`

Expected: FAIL on unimplemented report/entity methods.

- [ ] **Step 3: Implement report writes with gzip and outbox insertion**

Within one database transaction, upsert `barcode_reports`, allocate `nextval('sync_local_sequence')`, and insert `sync_events`. Store HTML with `gzip.compress(record.html, compresslevel=6)` and return decompressed bytes from `get_report()`.

- [ ] **Step 4: Implement explicit entity kinds**

Allow only:

```python
ENTITY_KINDS = {
    "barcode_metadata", "product_rule", "distributor", "account",
    "runtime_setting", "crm_credentials"
}
```

Reject unknown kinds with `ValueError`. Encrypt only the `crm_credentials/default` payload before storage; decrypt it when read.

- [ ] **Step 5: Implement soft delete and tombstone acknowledgement**

`delete_report()` and `delete_entity()` must update `deleted_at`, set `delete_event_id`, insert the corresponding delete event, and upsert `sync_tombstones` in one transaction. Add `ack_tombstone(delete_event_id, node_id)` and `purge_acknowledged_blobs(before)`; purge may null `html_gzip` but must not remove the tombstone key.

- [ ] **Step 6: Run PostgreSQL behavior tests**

Run: `python -m pytest tests/test_postgres_store.py -q`

Expected: report, entity, outbox, credential, idempotency, and tombstone tests pass.

- [ ] **Step 7: Commit transactional storage**

```bash
git add crm_storage/postgres_store.py tests/test_postgres_store.py
git commit -m "feat: persist business data with sync outbox"
```

---

### Task 5: Route all application persistence through the selected store

**Files:**
- Modify: `app.py:4600-4780, 5178-5215, 5529-5725, 5881-6220, 6422-7575`
- Modify: `templates/index.html`
- Modify: `templates/product_library.html`
- Modify: `templates/accounts.html`
- Create: `tests/test_app_storage_integration.py`

**Interfaces:**
- Consumes: complete `StorageBackend` from Task 4.
- Produces: unchanged public web API behavior backed by either storage implementation.

- [ ] **Step 1: Add API regression tests using `FileStore`**

```python
def test_barcode_detail_and_delete_use_store(client, file_store, monkeypatch):
    file_store.put_report(ReportRecord("5312503010858", b"<html>detail</html>", False, "query-1", "2026-07-16 10:00:00"))
    monkeypatch.setattr(app_module, "store", file_store)
    assert client.get("/api/barcodes/5312503010858").status_code == 200
    assert client.delete("/api/barcodes/5312503010858").get_json()["success"] is True
    assert file_store.get_report("5312503010858") is None
```

Add equivalent tests for product rules, distributors, runtime config, accounts, credentials, and operation logs.

- [ ] **Step 2: Run the API tests and verify direct-file assumptions fail**

Run: `python -m pytest tests/test_app_storage_integration.py -q`

Expected: at least report serving and direct file deletion tests fail.

- [ ] **Step 3: Replace report path assumptions**

Change CRM query result ingestion to read the generated temporary HTML once, call `store.put_report()`, then remove the temporary file in PostgreSQL mode. Change `/barcode/<filename>` and detail routes to return `Response(record.html, mimetype='text/html; charset=utf-8')` when using PostgreSQL.

- [ ] **Step 4: Delegate all JSON domains**

Keep existing helper function signatures but replace direct JSON access with entity calls. Preserve existing payload shapes consumed by templates. All delete routes pass `current_account_public()['username']` as the actor.

- [ ] **Step 5: Persist completed job logs**

On completion of query, transfer, service-close, product-library lookup, and bulk-login jobs, append their final log lines and result summary to `operation_logs`. Keep in-memory logs for live polling; historical modal reads `/api/logs?category=<page>&limit=500` from the store.

- [ ] **Step 6: Run focused API and parser tests**

Run: `python -m pytest tests/test_app_storage_integration.py tests/test_file_store.py tests/test_postgres_store.py -q`

Expected: all tests pass against both backends.

- [ ] **Step 7: Run a local browser smoke test in file mode**

Run: `CRM_DATA_DIR=/tmp/crm-file-ui python app.py`

Expected: `http://127.0.0.1:5001/` opens, product matching and account pages load, and no PostgreSQL connection is attempted.

- [ ] **Step 8: Commit the application cutover**

```bash
git add app.py templates tests/test_app_storage_integration.py
git commit -m "refactor: use storage backend across web APIs"
```

---

### Task 6: Build an idempotent file-to-PostgreSQL migration command

**Files:**
- Create: `scripts/migrate_files_to_postgres.py`
- Create: `tests/test_data_migration.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `python scripts/migrate_files_to_postgres.py --data-dir PATH --database-url URL --node-id hk --verify`.
- The command exits nonzero on count/hash mismatch and never deletes source files.

- [ ] **Step 1: Add a failing fixture migration test**

```python
def test_migration_is_idempotent(sample_legacy_data, pg_store):
    first = migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    second = migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    assert first.imported_reports == 2
    assert second.imported_reports == 0
    assert verify_directory(sample_legacy_data, pg_store).ok is True
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/test_data_migration.py -q`

Expected: FAIL because migration helpers do not exist.

- [ ] **Step 3: Implement deterministic import order**

Import runtime settings, accounts, credentials, product rules, distributors, barcode metadata, then HTML reports. For each source object compute SHA-256 and store `migration/source_hash/<kind>/<key>` as an internal runtime setting. Skip matching hashes on rerun.

- [ ] **Step 4: Implement verification output**

Print and return exact totals for source and destination: active reports, archived reports, metadata entries, product rules, distributors, accounts, and credentials. For report HTML compare SHA-256 after decompression. A mismatch exits `2`; connection/import failure exits `1`; success exits `0`.

- [ ] **Step 5: Run migration tests twice**

Run: `python -m pytest tests/test_data_migration.py -q`

Expected: all tests pass, including duplicate-run and malformed-JSON cases.

- [ ] **Step 6: Document the safe migration command**

README must say: stop writes, take a volume snapshot, run with `--verify`, start PostgreSQL mode only after success, and keep the original volume for at least 30 days.

- [ ] **Step 7: Commit the importer**

```bash
git add scripts/migrate_files_to_postgres.py tests/test_data_migration.py README.md
git commit -m "feat: migrate file data into PostgreSQL safely"
```

---

### Task 7: Add server Compose, backup, and restore verification

**Files:**
- Create: `deployment/docker-compose.server.yml`
- Create: `deployment/server.env.example`
- Create: `scripts/backup_postgres.sh`
- Create: `scripts/verify_postgres_restore.sh`
- Create: `tests/test_server_compose.py`
- Modify: `.gitignore`
- Modify: `Dockerfile`

**Interfaces:**
- Produces: a single-node server stack that remains useful before HA is enabled.
- Database and browser profiles use distinct named volumes.

- [ ] **Step 1: Add a failing Compose contract test**

```python
def test_server_compose_keeps_data_and_sessions_separate(compose_config):
    app = compose_config["services"]["crm-barcode-query"]
    assert "crm_app_postgres:/var/lib/postgresql/data" in compose_config["services"]["postgres"]["volumes"]
    assert "crm_browser_session:/app/session" in app["volumes"]
    assert app["environment"]["DATABASE_URL"]
```

- [ ] **Step 2: Create the server stack**

Compose contains `postgres:17`, `crm-barcode-query`, and a one-shot `migrate` service. Require secrets with Compose syntax such as `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}`. Do not publish PostgreSQL publicly. Add health checks using `pg_isready` and `/healthz`.

- [ ] **Step 3: Add backup scripts**

`backup_postgres.sh` writes a custom-format dump, encrypts with `age` using `BACKUP_AGE_RECIPIENT`, writes `.sha256`, and deletes local backups older than `BACKUP_RETENTION_DAYS=30`. `verify_postgres_restore.sh` restores into a disposable database and checks all required tables plus row counts.

- [ ] **Step 4: Keep desktop and server dependency behavior explicit**

Docker installs `requirements.txt`; desktop build scripts continue installing the existing desktop requirements and include `crm_storage` modules. PostgreSQL imports remain lazy so desktop startup does not require a database URL.

- [ ] **Step 5: Validate Compose and shell syntax**

Run: `docker compose -f deployment/docker-compose.server.yml config --quiet`

Expected: exit 0 when required test environment variables are supplied.

Run: `bash -n scripts/backup_postgres.sh scripts/verify_postgres_restore.sh`

Expected: exit 0.

- [ ] **Step 6: Run the server stack smoke test**

Run: `docker compose -f deployment/docker-compose.server.yml --env-file deployment/test.env up -d --build`

Expected: app and PostgreSQL become healthy; rebuilding the app leaves the PostgreSQL and browser-session volume names unchanged.

- [ ] **Step 7: Commit deployment foundations**

```bash
git add deployment scripts/backup_postgres.sh scripts/verify_postgres_restore.sh tests/test_server_compose.py .gitignore Dockerfile
git commit -m "ops: add persistent PostgreSQL server stack"
```

---

### Task 8: Run the phase-one regression and migration rehearsal

**Files:**
- Modify only files required by failures found in this task.

**Interfaces:**
- Produces: a reviewed PostgreSQL-capable single-node release that phase two can build on.

- [ ] **Step 1: Run all unit and integration tests**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Check formatting and syntax**

Run: `python -m compileall -q app.py crm_storage scripts/migrate_files_to_postgres.py`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 3: Rehearse migration on a copy of current Hong Kong data**

Run the importer against a copied volume export, never the live volume:

```bash
python scripts/migrate_files_to_postgres.py \
  --data-dir /tmp/crm-hk-data-copy \
  --database-url "$TEST_DATABASE_URL" \
  --node-id hk \
  --verify
```

Expected: source and destination counts match and the command exits 0.

- [ ] **Step 4: Rebuild desktop packages as a compatibility check**

Run macOS build on macOS and Windows build in GitHub Actions using the existing scripts.

Expected: both launch without `DATABASE_URL`, retain their local data path, and open the existing pages.

- [ ] **Step 5: Request code review and fix only phase-one findings**

Use `superpowers:requesting-code-review` against the commits from Tasks 1-7. Re-run Steps 1-4 after any fix.

- [ ] **Step 6: Commit final phase-one corrections**

```bash
git add -A
git commit -m "fix: complete PostgreSQL storage migration"
```

The phase is complete only when the working tree is clean, tests pass, migration verification passes, and the original server data volume has not been modified.
