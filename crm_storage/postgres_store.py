import gzip
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

from .base import EntityRecord, ReportRecord
from .crypto import CredentialCipher
from .migrate import run_migrations


@dataclass(frozen=True)
class SyncEventRecord:
    event_id: str
    origin_node: str
    local_sequence: int
    site_epoch: int
    entity_type: str
    entity_key: str
    operation: str
    payload: dict
    blob_gzip: bytes | None
    created_at: str


@dataclass(frozen=True)
class TombstoneRecord:
    entity_type: str
    entity_key: str
    delete_event_id: str
    origin_node: str
    deleted_at: datetime
    hk_ack_at: datetime | None
    sg_ack_at: datetime | None


ENTITY_KINDS = {
    "barcode_metadata",
    "product_rule",
    "distributor",
    "account",
    "runtime_setting",
    "crm_credentials",
}


class PostgresStore:
    def __init__(self, database_url, node_id=None, site_epoch_provider=None):
        self.database_url = database_url
        self.node_id = node_id or os.environ.get("CRM_NODE_ID") or "local"
        self.site_epoch_provider = site_epoch_provider or (lambda: 0)
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

    def list_reports(self, archived=False):
        with self.pool.connection() as connection:
            rows = connection.execute(
                "select barcode from barcode_reports "
                "where archived = %s and deleted_at is null order by barcode",
                (archived,),
            )
            return [row[0] for row in rows]

    def get_report(self, barcode):
        with self.pool.connection() as connection:
            row = connection.execute(
                "select barcode, html_gzip, archived, query_slot, "
                "to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') "
                "from barcode_reports where barcode = %s and deleted_at is null",
                (barcode,),
            ).fetchone()
        if row is None or row[1] is None:
            return None
        return ReportRecord(row[0], gzip.decompress(row[1]), row[2], row[3], row[4])

    def put_report(self, record):
        blob_gzip = gzip.compress(record.html, compresslevel=6)
        payload = {
            "archived": record.archived,
            "query_slot": record.query_slot,
            "updated_at": record.updated_at,
        }
        with self.transaction() as connection:
            connection.execute(
                "insert into barcode_reports "
                "(barcode, html_gzip, archived, query_slot, origin_node, updated_at) "
                "values (%s, %s, %s, %s, %s, %s) "
                "on conflict (barcode) do update set "
                "html_gzip = excluded.html_gzip, archived = excluded.archived, "
                "query_slot = excluded.query_slot, origin_node = excluded.origin_node, "
                "updated_at = excluded.updated_at, deleted_at = null, delete_event_id = null",
                (
                    record.barcode,
                    blob_gzip,
                    record.archived,
                    record.query_slot,
                    self.node_id,
                    record.updated_at,
                ),
            )
            self._insert_event(
                connection,
                entity_type="barcode_report",
                entity_key=record.barcode,
                operation="upsert",
                payload=payload,
                blob_gzip=blob_gzip,
            )

    def delete_report(self, barcode, actor="system"):
        event_id = uuid.uuid4()
        with self.transaction() as connection:
            row = connection.execute(
                "update barcode_reports set deleted_at = now(), delete_event_id = %s, "
                "origin_node = %s where barcode = %s and deleted_at is null "
                "returning deleted_at",
                (event_id, self.node_id, barcode),
            ).fetchone()
            if row is None:
                return False
            deleted_at = row[0]
            self._upsert_tombstone(
                connection, "barcode_report", barcode, event_id, deleted_at
            )
            self._insert_event(
                connection,
                entity_type="barcode_report",
                entity_key=barcode,
                operation="delete",
                payload={"actor": actor, "deleted_at": deleted_at.isoformat()},
                event_id=event_id,
            )
        return True

    def load_entities(self, kind, include_deleted=False):
        self._validate_entity_kind(kind)
        deleted_clause = "" if include_deleted else "and deleted_at is null"
        with self.pool.connection() as connection:
            rows = connection.execute(
                "select entity_key, payload, deleted_at is not null, "
                "to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') "
                f"from app_entities where kind = %s {deleted_clause} order by entity_key",
                (kind,),
            )
            return [self._entity_record(kind, row) for row in rows]

    def get_entity(self, kind, key):
        self._validate_entity_kind(kind)
        self._validate_entity_key(kind, key)
        with self.pool.connection() as connection:
            row = connection.execute(
                "select entity_key, payload, deleted_at is not null, "
                "to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') "
                "from app_entities where kind = %s and entity_key = %s",
                (kind, key),
            ).fetchone()
        return self._entity_record(kind, row) if row is not None else None

    def put_entity(self, kind, key, payload, actor="system"):
        self._validate_entity_kind(kind)
        self._validate_entity_key(kind, key)
        stored_payload = self._stored_entity_payload(kind, key, payload)
        updated_at = payload.get("updated_at")
        with self.transaction() as connection:
            connection.execute(
                "insert into app_entities "
                "(kind, entity_key, payload, origin_node, updated_at) "
                "values (%s, %s, %s, %s, coalesce(%s::timestamptz, now())) "
                "on conflict (kind, entity_key) do update set "
                "payload = excluded.payload, origin_node = excluded.origin_node, "
                "updated_at = excluded.updated_at, deleted_at = null, delete_event_id = null",
                (kind, key, Jsonb(stored_payload), self.node_id, updated_at),
            )
            self._insert_event(
                connection,
                entity_type=kind,
                entity_key=key,
                operation="upsert",
                payload=stored_payload,
            )

    def delete_entity(self, kind, key, actor="system"):
        self._validate_entity_kind(kind)
        self._validate_entity_key(kind, key)
        event_id = uuid.uuid4()
        with self.transaction() as connection:
            row = connection.execute(
                "update app_entities set deleted_at = now(), delete_event_id = %s, "
                "origin_node = %s where kind = %s and entity_key = %s "
                "and deleted_at is null returning deleted_at",
                (event_id, self.node_id, kind, key),
            ).fetchone()
            if row is None:
                return False
            deleted_at = row[0]
            self._upsert_tombstone(connection, kind, key, event_id, deleted_at)
            self._insert_event(
                connection,
                entity_type=kind,
                entity_key=key,
                operation="delete",
                payload={"actor": actor, "deleted_at": deleted_at.isoformat()},
                event_id=event_id,
            )
        return True

    def get_tombstone(self, entity_type, entity_key):
        with self.pool.connection() as connection:
            row = connection.execute(
                "select entity_type, entity_key, delete_event_id, origin_node, "
                "deleted_at, hk_ack_at, sg_ack_at from sync_tombstones "
                "where entity_type = %s and entity_key = %s",
                (entity_type, entity_key),
            ).fetchone()
        if row is None:
            return None
        return TombstoneRecord(row[0], row[1], str(row[2]), row[3], row[4], row[5], row[6])

    def ack_tombstone(self, delete_event_id, node_id):
        if node_id not in {"hk", "sg"}:
            raise ValueError("Tombstone acknowledgement node must be hk or sg")
        try:
            delete_event_id = uuid.UUID(str(delete_event_id))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Tombstone delete_event_id must be a valid UUID") from None
        column = f"{node_id}_ack_at"
        with self.transaction() as connection:
            row = connection.execute(
                f"update sync_tombstones set {column} = coalesce({column}, now()) "
                "where delete_event_id = %s returning delete_event_id",
                (delete_event_id,),
            ).fetchone()
        return row is not None

    def purge_acknowledged_blobs(self, before):
        with self.transaction() as connection:
            cursor = connection.execute(
                "update barcode_reports as report set html_gzip = null "
                "from sync_tombstones as tombstone "
                "where tombstone.entity_type = 'barcode_report' "
                "and tombstone.entity_key = report.barcode "
                "and tombstone.delete_event_id = report.delete_event_id "
                "and tombstone.hk_ack_at is not null and tombstone.sg_ack_at is not null "
                "and tombstone.deleted_at < %s and report.deleted_at is not null "
                "and report.html_gzip is not null",
                (before,),
            )
            return cursor.rowcount

    def append_log(self, category, level, message, context=None):
        event_id = uuid.uuid4()
        context = dict(context or {})
        with self.transaction() as connection:
            created_at = connection.execute(
                "insert into operation_logs "
                "(id, category, level, message, context, origin_node) "
                "values (%s, %s, %s, %s, %s, %s) returning created_at",
                (event_id, category, level, message, Jsonb(context), self.node_id),
            ).fetchone()[0]
            self._insert_event(
                connection,
                entity_type="operation_log",
                entity_key=str(event_id),
                operation="upsert",
                payload={
                    "category": category,
                    "level": level,
                    "message": message,
                    "context": context,
                    "created_at": created_at.isoformat(),
                },
                event_id=event_id,
            )
        return str(event_id)

    def list_logs(self, category, limit=500):
        with self.pool.connection() as connection:
            rows = connection.execute(
                "select id, level, message, context, "
                "to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') from ("
                "select id, level, message, context, created_at "
                "from operation_logs where category = %s "
                "order by created_at desc limit %s"
                ") as recent order by created_at",
                (category, limit),
            )
            return [
                {
                    "event_id": str(row[0]),
                    "level": row[1],
                    "message": row[2],
                    "context": row[3],
                    "created_at": row[4],
                }
                for row in rows
            ]

    def fetch_events(self, origin_node, after_sequence, limit):
        with self.pool.connection() as connection:
            rows = connection.execute(
                "select event_id, origin_node, local_sequence, site_epoch, "
                "entity_type, entity_key, operation, payload, blob_gzip, created_at "
                "from sync_events where origin_node = %s and local_sequence > %s "
                "order by local_sequence limit %s",
                (origin_node, after_sequence, limit),
            )
            return [
                SyncEventRecord(
                    str(row[0]),
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    str(row[9]),
                )
                for row in rows
            ]

    def _insert_event(
        self,
        connection,
        *,
        entity_type,
        entity_key,
        operation,
        payload,
        blob_gzip=None,
        event_id=None,
    ):
        event_id = event_id or uuid.uuid4()
        connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 918273::bigint))",
            (self.node_id,),
        )
        local_sequence = connection.execute(
            "select nextval('sync_local_sequence')"
        ).fetchone()[0]
        connection.execute(
            "insert into sync_events "
            "(event_id, origin_node, local_sequence, site_epoch, entity_type, "
            "entity_key, operation, payload, blob_gzip) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event_id,
                self.node_id,
                local_sequence,
                self.site_epoch_provider(),
                entity_type,
                entity_key,
                operation,
                Jsonb(payload),
                blob_gzip,
            ),
        )
        return event_id

    def _upsert_tombstone(
        self, connection, entity_type, entity_key, delete_event_id, deleted_at
    ):
        connection.execute(
            "insert into sync_tombstones "
            "(entity_type, entity_key, delete_event_id, origin_node, deleted_at) "
            "values (%s, %s, %s, %s, %s) "
            "on conflict (entity_type, entity_key) do update set "
            "delete_event_id = excluded.delete_event_id, "
            "origin_node = excluded.origin_node, deleted_at = excluded.deleted_at, "
            "hk_ack_at = null, sg_ack_at = null",
            (entity_type, entity_key, delete_event_id, self.node_id, deleted_at),
        )

    def _validate_entity_kind(self, kind):
        if kind not in ENTITY_KINDS:
            raise ValueError(f"Unsupported entity kind: {kind}")

    def _validate_entity_key(self, kind, key):
        if kind == "crm_credentials" and key != "default":
            raise ValueError("crm_credentials entity_key must be default")

    def _stored_entity_payload(self, kind, key, payload):
        if kind == "crm_credentials" and key == "default":
            token = self.cipher.encrypt(payload).decode("ascii")
            return {"encrypted_token": token}
        return dict(payload)

    def _entity_record(self, kind, row):
        self._validate_entity_key(kind, row[0])
        payload = row[1]
        if kind == "crm_credentials" and row[0] == "default":
            payload = self.cipher.decrypt(payload["encrypted_token"].encode("ascii"))
        return EntityRecord(row[0], payload, row[2], row[3])
