import base64
import gzip
import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from crm_storage.base import EntityRecord, ReportRecord
from crm_storage.postgres_store import PostgresStore


VALID_FERNET_KEY = base64.urlsafe_b64encode(b"p" * 32).decode("ascii")


@contextmanager
def rejecting_sync_events(pg_connection):
    pg_connection.execute(
        "create function reject_all_sync_events() returns trigger language plpgsql as $$ "
        "begin raise exception 'forced outbox failure'; end $$"
    )
    pg_connection.execute(
        "create trigger reject_all_sync_events before insert on sync_events "
        "for each row execute function reject_all_sync_events()"
    )
    try:
        yield
    finally:
        pg_connection.execute("drop trigger reject_all_sync_events on sync_events")
        pg_connection.execute("drop function reject_all_sync_events()")


@pytest.fixture
def pg_store(pg_database_url, pg_connection, monkeypatch):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    monkeypatch.setenv("CRM_NODE_ID", "hk")
    store = PostgresStore(pg_database_url)
    pg_connection.execute(
        "truncate barcode_reports, app_entities, operation_logs, "
        "sync_events, sync_cursors, sync_tombstones"
    )
    pg_connection.execute("alter sequence sync_local_sequence restart with 1")
    try:
        yield store
    finally:
        store.close()


def test_report_write_compresses_html_and_emits_event(pg_store, pg_connection):
    record = ReportRecord(
        "8452508130954",
        b"<html>report</html>",
        False,
        "query-1",
        "2026-07-16 10:00:00",
    )

    pg_store.put_report(record)

    assert pg_store.get_report(record.barcode) == record
    assert pg_store.list_reports() == [record.barcode]
    stored_blob = pg_connection.execute(
        "select html_gzip from barcode_reports where barcode = %s",
        (record.barcode,),
    ).fetchone()[0]
    assert stored_blob != record.html
    assert gzip.decompress(stored_blob) == record.html

    events = pg_store.fetch_events("hk", 0, 10)
    assert [(event.entity_type, event.entity_key, event.operation) for event in events] == [
        ("barcode_report", record.barcode, "upsert")
    ]
    assert events[0].origin_node == "hk"
    assert events[0].local_sequence == 1
    assert events[0].site_epoch == 0
    assert events[0].payload == {
        "archived": False,
        "query_slot": "query-1",
        "updated_at": "2026-07-16 10:00:00",
    }
    assert events[0].blob_gzip == stored_blob


def test_constructor_injected_site_epoch_is_written_to_event(
    pg_database_url, pg_connection, monkeypatch
):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    monkeypatch.setenv("CRM_NODE_ID", "hk")
    pg_connection.execute("truncate barcode_reports, sync_events, sync_tombstones")
    store = PostgresStore(pg_database_url, site_epoch_provider=lambda: 23)
    try:
        store.put_report(
            ReportRecord("epoch", b"report", False, "query-1", "2026-07-16 10:00:00")
        )
        assert store.fetch_events("hk", 0, 1)[0].site_epoch == 23
    finally:
        store.close()


def test_report_and_event_roll_back_when_outbox_insert_fails(pg_store, pg_connection):
    pg_connection.execute(
        "create function reject_sync_event() returns trigger language plpgsql as $$ "
        "begin raise exception 'forced outbox failure'; end $$"
    )
    pg_connection.execute(
        "create trigger reject_sync_event before insert on sync_events "
        "for each row execute function reject_sync_event()"
    )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="forced outbox failure"):
            pg_store.put_report(
                ReportRecord("atomic", b"report", False, "query-1", "2026-07-16 10:00:00")
            )
    finally:
        pg_connection.execute("drop trigger reject_sync_event on sync_events")
        pg_connection.execute("drop function reject_sync_event()")

    assert pg_connection.execute(
        "select count(*) from barcode_reports where barcode = 'atomic'"
    ).fetchone()[0] == 0
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == 0


def test_entity_round_trip_and_upsert_event(pg_store):
    payload = {
        "product_code": "906020907",
        "product_name": "壁挂式饮水机",
        "updated_at": "2026-07-16 11:00:00",
    }

    pg_store.put_entity("product_rule", "845", payload, "admin")

    expected = EntityRecord("845", payload, False, "2026-07-16 11:00:00")
    assert pg_store.get_entity("product_rule", "845") == expected
    assert pg_store.load_entities("product_rule") == [expected]
    event = pg_store.fetch_events("hk", 0, 10)[0]
    assert (event.entity_type, event.entity_key, event.operation) == (
        "product_rule",
        "845",
        "upsert",
    )
    assert event.payload == payload


def test_entity_upsert_rolls_back_when_outbox_insert_fails(pg_store, pg_connection):
    original = {"mode": "old", "updated_at": "2026-07-16 11:00:00"}
    pg_store.put_entity("runtime_setting", "runtime", original)
    pg_connection.execute("truncate sync_events")

    with rejecting_sync_events(pg_connection):
        with pytest.raises(psycopg.errors.RaiseException, match="forced outbox failure"):
            pg_store.put_entity(
                "runtime_setting",
                "runtime",
                {"mode": "new", "updated_at": "2026-07-16 12:00:00"},
            )

    assert pg_store.get_entity("runtime_setting", "runtime") == EntityRecord(
        "runtime", original, False, "2026-07-16 11:00:00"
    )
    assert pg_store.get_tombstone("runtime_setting", "runtime") is None
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == 0


def test_unknown_entity_kinds_fail(pg_store):
    calls = [
        lambda: pg_store.load_entities("unknown"),
        lambda: pg_store.get_entity("unknown", "key"),
        lambda: pg_store.put_entity("unknown", "key", {}),
        lambda: pg_store.delete_entity("unknown", "key"),
    ]

    for call in calls:
        with pytest.raises(ValueError, match="Unsupported entity kind: unknown"):
            call()


def test_all_crm_credential_owner_rows_are_encrypted_in_entities_and_events(
    pg_store, pg_connection
):
    owners = {
        "operator": {"username": "gongqi", "password": "operator-secret"},
        "secondary": {"username": "other", "password": "secondary-secret"},
    }
    barrier = threading.Barrier(len(owners))
    errors = []

    def save_owner(owner, credentials):
        try:
            barrier.wait(timeout=2)
            pg_store.put_entity("crm_credentials", owner, credentials)
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=save_owner, args=(owner, credentials))
        for owner, credentials in owners.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert {
        row.key: row.payload for row in pg_store.load_entities("crm_credentials")
    } == owners
    entity_rows = list(
        pg_connection.execute(
            "select entity_key, payload from app_entities "
            "where kind = 'crm_credentials' order by entity_key"
        )
    )
    event_rows = list(
        pg_connection.execute(
            "select entity_key, payload from sync_events "
            "where entity_type = 'crm_credentials' order by entity_key"
        )
    )
    assert [row[0] for row in entity_rows] == ["operator", "secondary"]
    assert [row[0] for row in event_rows] == ["operator", "secondary"]
    for (_entity_key, entity_payload), (_event_key, event_payload) in zip(
        entity_rows, event_rows
    ):
        assert set(entity_payload) == {"encrypted_token"}
        assert entity_payload == event_payload
        entity_payload["encrypted_token"].encode("ascii")
    serialized = json.dumps([*entity_rows, *event_rows])
    assert "operator-secret" not in serialized
    assert "secondary-secret" not in serialized


def test_report_delete_is_idempotent_and_creates_tombstone(pg_store):
    barcode = "5312503010858"
    pg_store.put_report(
        ReportRecord(barcode, b"report", False, "query-1", "2026-07-16 10:00:00")
    )

    assert pg_store.delete_report(barcode, "admin") is True
    assert pg_store.delete_report(barcode, "admin") is False
    assert pg_store.get_report(barcode) is None
    assert pg_store.list_reports() == []
    tombstone = pg_store.get_tombstone("barcode_report", barcode)
    events = pg_store.fetch_events("hk", 0, 10)
    assert tombstone.entity_type == "barcode_report"
    assert tombstone.entity_key == barcode
    assert tombstone.origin_node == "hk"
    assert tombstone.delete_event_id == events[-1].event_id
    assert [(event.operation, event.entity_key) for event in events] == [
        ("upsert", barcode),
        ("delete", barcode),
    ]
    assert events[-1].payload["actor"] == "admin"


def test_report_delete_rolls_back_when_outbox_insert_fails(pg_store, pg_connection):
    original = ReportRecord(
        "delete-report", b"original", False, "query-1", "2026-07-16 10:00:00"
    )
    pg_store.put_report(original)
    pg_connection.execute("truncate sync_events")

    with rejecting_sync_events(pg_connection):
        with pytest.raises(psycopg.errors.RaiseException, match="forced outbox failure"):
            pg_store.delete_report(original.barcode, "admin")

    assert pg_store.get_report(original.barcode) == original
    assert pg_store.get_tombstone("barcode_report", original.barcode) is None
    assert pg_connection.execute(
        "select deleted_at, delete_event_id from barcode_reports where barcode = %s",
        (original.barcode,),
    ).fetchone() == (None, None)
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == 0


def test_entity_delete_keeps_key_and_filters_default_listing(pg_store):
    payload = {"value": "旧分销商", "updated_at": "2026-07-16 11:00:00"}
    pg_store.put_entity("distributor", "旧分销商", payload)

    assert pg_store.delete_entity("distributor", "旧分销商", "admin") is True
    assert pg_store.delete_entity("distributor", "旧分销商", "admin") is False
    assert pg_store.load_entities("distributor") == []
    deleted = pg_store.get_entity("distributor", "旧分销商")
    assert deleted == EntityRecord("旧分销商", payload, True, "2026-07-16 11:00:00")
    assert pg_store.load_entities("distributor", include_deleted=True) == [deleted]
    tombstone = pg_store.get_tombstone("distributor", "旧分销商")
    assert tombstone.delete_event_id == pg_store.fetch_events("hk", 0, 10)[-1].event_id


def test_entity_delete_rolls_back_when_outbox_insert_fails(pg_store, pg_connection):
    original = {"value": "保留", "updated_at": "2026-07-16 11:00:00"}
    pg_store.put_entity("distributor", "保留", original)
    pg_connection.execute("truncate sync_events")

    with rejecting_sync_events(pg_connection):
        with pytest.raises(psycopg.errors.RaiseException, match="forced outbox failure"):
            pg_store.delete_entity("distributor", "保留", "admin")

    assert pg_store.get_entity("distributor", "保留") == EntityRecord(
        "保留", original, False, "2026-07-16 11:00:00"
    )
    assert pg_store.get_tombstone("distributor", "保留") is None
    assert pg_connection.execute(
        "select deleted_at, delete_event_id from app_entities "
        "where kind = 'distributor' and entity_key = '保留'"
    ).fetchone() == (None, None)
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == 0


def test_tombstone_acknowledgement_validates_node_and_is_idempotent(pg_store):
    pg_store.put_report(
        ReportRecord("ack", b"report", False, "query-1", "2026-07-16 10:00:00")
    )
    pg_store.delete_report("ack")
    event_id = pg_store.get_tombstone("barcode_report", "ack").delete_event_id

    with pytest.raises(ValueError, match="hk or sg"):
        pg_store.ack_tombstone(event_id, "other")
    assert pg_store.ack_tombstone("00000000-0000-0000-0000-000000000000", "hk") is False
    assert pg_store.ack_tombstone(event_id, "hk") is True
    first_ack = pg_store.get_tombstone("barcode_report", "ack").hk_ack_at
    assert pg_store.ack_tombstone(event_id, "hk") is True
    assert pg_store.get_tombstone("barcode_report", "ack").hk_ack_at == first_ack


@pytest.mark.parametrize("malformed_event_id", ["not-a-uuid", "", None, 7])
def test_tombstone_acknowledgement_rejects_malformed_uuid_before_database_access(
    pg_store, monkeypatch, malformed_event_id
):
    def fail_if_connection_requested():
        raise AssertionError("database access must not occur")

    monkeypatch.setattr(pg_store.pool, "connection", fail_if_connection_requested)

    with pytest.raises(ValueError, match="valid UUID"):
        pg_store.ack_tombstone(malformed_event_id, "hk")


def test_tombstone_acknowledgement_returns_false_for_valid_unknown_uuid(pg_store):
    assert pg_store.ack_tombstone(uuid.uuid4(), "sg") is False


def test_purge_requires_cutoff_and_both_acks_but_preserves_keys(pg_store, pg_connection):
    pg_store.put_report(
        ReportRecord("purge", b"large report", False, "query-1", "2026-07-16 10:00:00")
    )
    pg_store.delete_report("purge")
    tombstone = pg_store.get_tombstone("barcode_report", "purge")
    future = datetime.now(timezone.utc) + timedelta(seconds=5)

    assert pg_store.purge_acknowledged_blobs(datetime(2000, 1, 1, tzinfo=timezone.utc)) == 0
    assert pg_store.ack_tombstone(tombstone.delete_event_id, "hk") is True
    assert pg_store.purge_acknowledged_blobs(future) == 0
    assert pg_store.ack_tombstone(tombstone.delete_event_id, "sg") is True
    assert pg_store.purge_acknowledged_blobs(future) == 1

    report_row = pg_connection.execute(
        "select html_gzip, deleted_at, delete_event_id from barcode_reports where barcode = 'purge'"
    ).fetchone()
    assert report_row[0] is None
    assert report_row[1] is not None
    assert str(report_row[2]) == tombstone.delete_event_id
    assert pg_store.get_tombstone("barcode_report", "purge") is not None


def test_report_archive_transition_and_reactivation_survive_stale_tombstone(pg_store):
    pg_store.put_report(
        ReportRecord("transition", b"active", False, "query-1", "2026-07-16 10:00:00")
    )
    pg_store.put_report(
        ReportRecord("transition", b"archived", True, "query-2", "2026-07-16 11:00:00")
    )
    assert pg_store.list_reports(False) == []
    assert pg_store.list_reports(True) == ["transition"]
    assert pg_store.get_report("transition").html == b"archived"

    pg_store.delete_report("transition")
    tombstone = pg_store.get_tombstone("barcode_report", "transition")
    pg_store.ack_tombstone(tombstone.delete_event_id, "hk")
    pg_store.ack_tombstone(tombstone.delete_event_id, "sg")
    pg_store.put_report(
        ReportRecord("transition", b"restored", False, "query-3", "2026-07-16 12:00:00")
    )

    assert pg_store.purge_acknowledged_blobs(
        datetime.now(timezone.utc) + timedelta(seconds=5)
    ) == 0
    assert pg_store.get_report("transition").html == b"restored"


def test_report_bundle_archive_rolls_back_when_metadata_step_fails(
    pg_store, pg_connection
):
    active = ReportRecord(
        "bundle-archive", b"active", False, "query-1", "2026-07-16 10:00:00"
    )
    active_metadata = {
        "archived": False,
        "remark": "keep",
        "querySlotId": "query-1",
        "queryUpdatedAt": "2026-07-16 10:00:00",
    }
    pg_store.put_report_bundle(active, active_metadata)
    event_count = pg_connection.execute("select count(*) from sync_events").fetchone()[0]
    pg_connection.execute(
        "create function reject_bundle_metadata() returns trigger language plpgsql as $$ "
        "begin if new.kind = 'barcode_metadata' then "
        "raise exception 'forced bundle metadata failure'; end if; return new; end $$"
    )
    pg_connection.execute(
        "create trigger reject_bundle_metadata before insert or update on app_entities "
        "for each row execute function reject_bundle_metadata()"
    )
    try:
        with pytest.raises(
            psycopg.errors.RaiseException, match="forced bundle metadata failure"
        ):
            pg_store.put_report_bundle(
                ReportRecord(
                    "bundle-archive",
                    b"archived",
                    True,
                    "query-1",
                    "2026-07-16 11:00:00",
                ),
                {
                    "archived": True,
                    "remark": "changed",
                    "querySlotId": "query-1",
                    "queryUpdatedAt": "2026-07-16 11:00:00",
                },
            )
    finally:
        pg_connection.execute("drop trigger reject_bundle_metadata on app_entities")
        pg_connection.execute("drop function reject_bundle_metadata()")

    assert pg_store.get_report("bundle-archive") == active
    assert pg_store.get_entity(
        "barcode_metadata", "bundle-archive"
    ).payload == active_metadata
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == event_count


def test_report_bundle_delete_rolls_back_when_metadata_step_fails(
    pg_store, pg_connection
):
    record = ReportRecord(
        "bundle-delete", b"active", False, "query-1", "2026-07-16 10:00:00"
    )
    pg_store.put_report_bundle(
        record,
        {
            "archived": False,
            "querySlotId": "query-1",
            "queryUpdatedAt": "2026-07-16 10:00:00",
        },
    )
    event_count = pg_connection.execute("select count(*) from sync_events").fetchone()[0]
    pg_connection.execute(
        "create function reject_bundle_metadata_delete() returns trigger language plpgsql as $$ "
        "begin if old.kind = 'barcode_metadata' then "
        "raise exception 'forced bundle metadata delete failure'; end if; return new; end $$"
    )
    pg_connection.execute(
        "create trigger reject_bundle_metadata_delete before update on app_entities "
        "for each row execute function reject_bundle_metadata_delete()"
    )
    try:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="forced bundle metadata delete failure",
        ):
            pg_store.delete_report_bundle("bundle-delete", "operator")
    finally:
        pg_connection.execute(
            "drop trigger reject_bundle_metadata_delete on app_entities"
        )
        pg_connection.execute("drop function reject_bundle_metadata_delete()")

    assert pg_store.get_report("bundle-delete") == record
    assert pg_store.get_entity("barcode_metadata", "bundle-delete").deleted is False
    assert pg_store.get_tombstone("barcode_report", "bundle-delete") is None
    assert pg_store.get_tombstone("barcode_metadata", "bundle-delete") is None
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == event_count


def test_entity_upsert_reactivates_deleted_key(pg_store):
    pg_store.put_entity("runtime_setting", "runtime", {"mode": "old"})
    pg_store.delete_entity("runtime_setting", "runtime")

    pg_store.put_entity("runtime_setting", "runtime", {"mode": "new"})

    row = pg_store.get_entity("runtime_setting", "runtime")
    assert row.payload == {"mode": "new"}
    assert row.deleted is False


def test_append_and_list_logs_emit_transactional_events(pg_store):
    first_id = pg_store.append_log("query", "INFO", "first", {"barcode": "845"})
    second_id = pg_store.append_log("query", "ERROR", "second")
    pg_store.append_log("other", "INFO", "not listed")

    assert pg_store.list_logs("query", limit=1) == [
        {
            "event_id": second_id,
            "level": "ERROR",
            "message": "second",
            "context": {},
            "created_at": pg_store.list_logs("query", limit=2)[1]["created_at"],
        }
    ]
    rows = pg_store.list_logs("query", limit=2)
    assert [row["event_id"] for row in rows] == [first_id, second_id]
    event = next(
        event for event in pg_store.fetch_events("hk", 0, 10) if event.entity_key == first_id
    )
    assert (event.entity_type, event.operation) == ("operation_log", "upsert")
    assert event.payload["category"] == "query"
    assert event.payload["context"] == {"barcode": "845"}


def test_append_log_with_stable_event_id_is_idempotent(pg_store, pg_connection):
    event_id = "1f40c2ca-047c-5d50-95ca-7d3894b849f1"

    assert pg_store.append_log(
        "query", "INFO", "once", {"job_id": "job-1"}, event_id=event_id
    ) == event_id
    restarted_store = PostgresStore(pg_store.database_url, node_id="hk")
    try:
        assert restarted_store.append_log(
            "query", "INFO", "once", {"job_id": "job-1"}, event_id=event_id
        ) == event_id
    finally:
        restarted_store.close()

    assert len(pg_store.list_logs("query")) == 1
    assert pg_connection.execute(
        "select count(*) from sync_events where event_id = %s", (event_id,)
    ).fetchone()[0] == 1


def test_log_and_event_roll_back_when_outbox_insert_fails(pg_store, pg_connection):
    pg_connection.execute(
        "create function reject_log_sync_event() returns trigger language plpgsql as $$ "
        "begin raise exception 'forced log outbox failure'; end $$"
    )
    pg_connection.execute(
        "create trigger reject_log_sync_event before insert on sync_events "
        "for each row execute function reject_log_sync_event()"
    )
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="forced log outbox failure"):
            pg_store.append_log("query", "INFO", "must roll back")
    finally:
        pg_connection.execute("drop trigger reject_log_sync_event on sync_events")
        pg_connection.execute("drop function reject_log_sync_event()")

    assert pg_connection.execute("select count(*) from operation_logs").fetchone()[0] == 0
    assert pg_connection.execute("select count(*) from sync_events").fetchone()[0] == 0


def test_fetch_events_filters_origin_sequence_and_limit(pg_store, pg_database_url):
    pg_store.append_log("query", "INFO", "hk-1")
    sg_store = PostgresStore(pg_database_url, node_id="sg", site_epoch_provider=lambda: 8)
    try:
        sg_store.append_log("query", "INFO", "sg-1")
    finally:
        sg_store.close()
    pg_store.append_log("query", "INFO", "hk-2")

    first = pg_store.fetch_events("hk", 0, 1)
    remaining = pg_store.fetch_events("hk", first[0].local_sequence, 10)
    assert [event.payload["message"] for event in first] == ["hk-1"]
    assert [event.payload["message"] for event in remaining] == ["hk-2"]
    assert [event.payload["message"] for event in pg_store.fetch_events("sg", 0, 10)] == [
        "sg-1"
    ]


def test_same_origin_sequences_cannot_commit_out_of_order(pg_store, pg_connection):
    second_started = threading.Event()
    second_finished = threading.Event()
    second_pid = []
    errors = []

    def insert_second_event():
        try:
            with pg_store.transaction() as connection:
                second_pid.append(connection.info.backend_pid)
                second_started.set()
                pg_store._insert_event(
                    connection,
                    entity_type="operation_log",
                    entity_key="second",
                    operation="upsert",
                    payload={"message": "second"},
                )
        except Exception as error:
            errors.append(error)
        finally:
            second_finished.set()

    with pg_store.transaction() as first_connection:
        pg_store._insert_event(
            first_connection,
            entity_type="operation_log",
            entity_key="first",
            operation="upsert",
            payload={"message": "first"},
        )
        thread = threading.Thread(target=insert_second_event)
        thread.start()
        assert second_started.wait(timeout=5)

        waiting_on_advisory_lock = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not second_finished.is_set():
            wait_state = pg_connection.execute(
                "select wait_event_type, wait_event from pg_stat_activity where pid = %s",
                (second_pid[0],),
            ).fetchone()
            if wait_state == ("Lock", "advisory"):
                waiting_on_advisory_lock = True
                break
            time.sleep(0.01)

        second_finished_while_first_uncommitted = second_finished.is_set()
        visible_while_first_uncommitted = [
            event.local_sequence for event in pg_store.fetch_events("hk", 0, 10)
        ]

    thread.join(timeout=5)

    assert not errors
    assert not thread.is_alive()
    assert waiting_on_advisory_lock is True
    assert second_finished_while_first_uncommitted is False
    assert visible_while_first_uncommitted == []
    assert [event.local_sequence for event in pg_store.fetch_events("hk", 0, 10)] == [1, 2]


def test_node_id_defaults_to_local(pg_database_url, pg_connection, monkeypatch):
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    monkeypatch.delenv("CRM_NODE_ID", raising=False)
    pg_connection.execute("truncate operation_logs, sync_events")
    store = PostgresStore(pg_database_url)
    try:
        store.append_log("system", "INFO", "local event")
        assert store.fetch_events("local", 0, 1)[0].origin_node == "local"
    finally:
        store.close()
