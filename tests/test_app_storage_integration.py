import base64
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from crm_storage.base import ReportRecord
from crm_storage.file_store import FileStore
from crm_storage.postgres_store import PostgresStore


VALID_FERNET_KEY = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")
BARCODE = "5312503010858"
UPDATED_AT = "2026-07-16 10:00:00"
REPORT_HTML = (
    b'<!doctype html><html><body><div id="Subreport1">'
    b'<div id="newname1"><span>5312503010858</span></div>'
    b'<div id="ProductNumber1"><span>906020907</span></div>'
    b'</div><p>complete report</p></body></html>'
)
ALL_PERMISSIONS = ["crm", "results", "transfer", "accounts", "product-library"]


@dataclass(frozen=True)
class StorageCase:
    name: str
    store: object


@pytest.fixture(scope="module")
def app_module(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CRM_DATA_DIR", str(tmp_path_factory.mktemp("app-import")))
    monkeypatch.setenv("CRM_STARTUP_LOGIN_AUTO_CHECK", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import app as module

    module.app.config.update(TESTING=True)
    try:
        yield module
    finally:
        monkeypatch.undo()


@pytest.fixture(params=["file", "postgres"])
def storage_case(
    request, tmp_path, pg_database_url, pg_connection, monkeypatch
):
    if request.param == "file":
        yield StorageCase("file", FileStore(str(tmp_path)))
        return

    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)
    store = PostgresStore(pg_database_url, node_id="app-test")
    pg_connection.execute(
        "truncate barcode_reports, app_entities, operation_logs, "
        "sync_events, sync_cursors, sync_tombstones"
    )
    pg_connection.execute("alter sequence sync_local_sequence restart with 1")
    try:
        yield StorageCase("postgres", store)
    finally:
        store.close()


@pytest.fixture
def client(app_module, storage_case, monkeypatch):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", False)
    storage_case.store.put_entity(
        "account",
        "operator-id",
        {
            "id": "operator-id",
            "username": "operator",
            "display_name": "Operator",
            "password": "secret",
            "permissions": ALL_PERMISSIONS,
            "updated_at": UPDATED_AT,
            "is_admin": True,
        },
    )
    with app_module.app.test_client() as test_client:
        with test_client.session_transaction() as session:
            session["account_username"] = "operator"
        yield test_client


def put_report(storage_case, archived=False):
    storage_case.store.put_report(
        ReportRecord(BARCODE, REPORT_HTML, archived, "query-1", UPDATED_AT)
    )
    metadata = storage_case.store.get_entity("barcode_metadata", BARCODE)
    payload = dict(metadata.payload) if metadata else {}
    payload.update({
        "archived": archived,
        "querySlotId": "query-1",
        "queryUpdatedAt": UPDATED_AT,
    })
    storage_case.store.put_entity("barcode_metadata", BARCODE, payload)


def assert_delete_actor(storage_case, entity_type, entity_key, actor="operator"):
    if storage_case.name == "file":
        rows = json.loads(
            (Path(storage_case.store.config_dir) / "tombstones.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(
            item
            for item in rows
            if item["kind"] == entity_type and item["key"] == entity_key
        )
        assert row["actor"] == actor
        return

    event = next(
        row
        for row in reversed(storage_case.store.fetch_events("app-test", 0, 100))
        if row.entity_type == entity_type
        and row.entity_key == entity_key
        and row.operation == "delete"
    )
    assert event.payload["actor"] == actor


def test_delete_actor_falls_back_to_system_without_request_context(app_module):
    assert app_module._current_actor() == "system"


def test_query_result_ingestion_uses_selected_store_and_cleans_postgres_temp_html(
    app_module, storage_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    generated = tmp_path / f"{BARCODE}.html"
    generated.write_bytes(REPORT_HTML)

    fields = app_module._ingest_query_result(BARCODE, str(generated))

    report = storage_case.store.get_report(BARCODE)
    assert report.html == REPORT_HTML
    assert fields["sr1"]["newname1"] == BARCODE
    assert generated.exists() is (storage_case.name == "file")


def test_report_list_detail_and_full_html_render_use_selected_store(
    client, storage_case
):
    put_report(storage_case)

    listing = client.get("/api/barcodes").get_json()
    detail = client.get(f"/api/barcodes/{BARCODE}").get_json()
    rendered = client.get(f"/barcode/{BARCODE}.html")

    assert listing["success"] is True
    assert listing["total"] == 1
    assert listing["barcodes"][0]["barcode"] == BARCODE
    assert listing["barcodes"][0]["fields"]["sr1"]["ProductNumber1"] == "906020907"
    assert detail == {
        "success": True,
        "barcode": BARCODE,
        "time": UPDATED_AT,
        "fields": {
            "sr1": {
                "newname1": BARCODE,
                "ProductNumber1": "906020907",
            }
        },
    }
    assert rendered.status_code == 200
    assert rendered.mimetype == "text/html"
    assert rendered.data == REPORT_HTML


def test_archive_and_unarchive_move_report_through_selected_store(
    client, storage_case
):
    put_report(storage_case)

    archived = client.post(f"/api/barcodes/{BARCODE}/archive").get_json()

    assert archived == {"success": True, "message": "归档成功"}
    assert storage_case.store.list_reports(False) == []
    assert storage_case.store.list_reports(True) == [BARCODE]
    archived_listing = client.get("/api/barcodes/archived").get_json()
    assert archived_listing["barcodes"][0]["barcode"] == BARCODE
    assert client.get(f"/barcode/archived/{BARCODE}.html").data == REPORT_HTML

    restored = client.post(f"/api/barcodes/{BARCODE}/unarchive").get_json()

    assert restored == {"success": True, "message": "取消归档成功"}
    assert storage_case.store.list_reports(False) == [BARCODE]
    assert storage_case.store.list_reports(True) == []


def test_report_delete_uses_selected_store_and_current_username(
    client, storage_case
):
    put_report(storage_case)

    response = client.delete(f"/api/barcodes/{BARCODE}")

    assert response.get_json() == {"success": True, "message": f"已删除 {BARCODE}"}
    assert storage_case.store.get_report(BARCODE) is None
    assert storage_case.store.get_entity("barcode_metadata", BARCODE).deleted is True
    assert_delete_actor(
        storage_case,
        "report" if storage_case.name == "file" else "barcode_report",
        BARCODE,
    )
    assert_delete_actor(storage_case, "barcode_metadata", BARCODE)


def test_barcode_metadata_remark_api_uses_selected_store(client, storage_case):
    put_report(storage_case)

    response = client.post(
        f"/api/barcodes/{BARCODE}/remark", json={"remark": "needs review"}
    )

    assert response.get_json() == {"success": True, "remark": "needs review"}
    assert storage_case.store.get_entity(
        "barcode_metadata", BARCODE
    ).payload["remark"] == "needs review"


def test_product_library_api_uses_selected_store_and_delete_actor(
    client, storage_case
):
    created = client.post(
        "/api/product-library",
        json={
            "prefix": "531",
            "product_code": "906020907",
            "product_name": "Water dispenser",
            "source_barcode": BARCODE,
        },
    )

    assert created.get_json() == {"success": True}
    listing = client.get("/api/product-library").get_json()
    assert listing["products"][0]["prefix"] == "531"
    assert storage_case.store.get_entity(
        "product_rule", "531"
    ).payload["product_code"] == "906020907"

    assert client.delete("/api/product-library/531").get_json() == {"success": True}
    assert storage_case.store.get_entity("product_rule", "531").deleted is True
    assert_delete_actor(storage_case, "product_rule", "531")


def test_distributor_history_api_uses_selected_store_and_delete_actor(
    client, storage_case
):
    created = client.post(
        "/api/distributor-history", json={"distributor": "Dealer A"}
    ).get_json()

    assert created["success"] is True
    assert created["dealers"] == ["Dealer A"]
    assert storage_case.store.get_entity(
        "distributor", "Dealer A"
    ).payload == {"value": "Dealer A"}

    deleted = client.delete(
        "/api/distributor-history", json={"distributor": "Dealer A"}
    ).get_json()

    assert deleted["success"] is True
    assert deleted["dealers"] == []
    assert storage_case.store.get_entity("distributor", "Dealer A").deleted is True
    assert_delete_actor(storage_case, "distributor", "Dealer A")


def test_runtime_config_api_uses_selected_store(
    app_module, client, storage_case, monkeypatch
):
    class StubPool:
        query_slots = {"query-1": object()}
        transfer_slots = {"transfer-1": object()}

        def resize(self, query_workers, transfer_workers):
            self.query_slots = {
                f"query-{index}": object()
                for index in range(1, query_workers + 1)
            }
            self.transfer_slots = {
                f"transfer-{index}": object()
                for index in range(1, transfer_workers + 1)
            }
            return {"query": [], "transfer": []}

    monkeypatch.setattr(app_module, "crm_pool", StubPool())

    saved = client.post(
        "/api/runtime-config",
        json={
            "query_workers": 3,
            "transfer_workers": 2,
            "own_dealer_name": "Own Dealer",
            "frozen_warehouse_name": "Frozen Warehouse",
            "frozen_warehouse_save_only": False,
        },
    ).get_json()

    assert saved["success"] is True
    assert saved["config"]["query_workers"] == 3
    assert saved["active"] == {"query_workers": 3, "transfer_workers": 2}
    stored = storage_case.store.get_entity("runtime_setting", "runtime")
    assert stored.payload["own_dealer_name"] == "Own Dealer"
    assert stored.payload["frozen_warehouse_save_only"] is False
    loaded = client.get("/api/runtime-config").get_json()["config"]
    assert loaded == {
        key: value for key, value in stored.payload.items() if key != "updated_at"
    }


def test_accounts_api_uses_selected_store_and_delete_actor(client, storage_case):
    created = client.post(
        "/api/accounts",
        json={
            "username": "viewer",
            "display_name": "Viewer",
            "password": "viewer-secret",
            "permissions": ["results"],
        },
    )

    assert created.get_json() == {"success": True}
    viewer = next(
        row
        for row in storage_case.store.load_entities("account")
        if row.payload.get("username") == "viewer"
    )
    listing = client.get("/api/accounts").get_json()
    public_viewer = next(row for row in listing["accounts"] if row["username"] == "viewer")
    assert "password" not in public_viewer

    assert client.delete(f"/api/accounts/{viewer.key}").get_json() == {"success": True}
    assert storage_case.store.get_entity("account", viewer.key).deleted is True
    assert_delete_actor(storage_case, "account", viewer.key)


def test_crm_credentials_api_preserves_payload_and_uses_default_postgres_entity(
    client, storage_case
):
    saved = client.post(
        "/api/crm/credentials",
        json={
            "remember": True,
            "username": "crm-user",
            "password": "crm-secret",
        },
    )

    assert saved.get_json() == {"success": True, "remember": True}
    assert client.get("/api/crm/credentials").get_json() == {
        "success": True,
        "remember": True,
        "username": "crm-user",
        "password": "crm-secret",
    }
    key = "operator" if storage_case.name == "file" else "default"
    row = storage_case.store.get_entity("crm_credentials", key)
    expected_payload = row.payload if storage_case.name == "file" else row.payload["operator"]
    assert expected_payload["username"] == "crm-user"

    forgotten = client.post(
        "/api/crm/credentials", json={"remember": False}
    ).get_json()

    assert forgotten == {"success": True, "remember": False}
    assert client.get("/api/crm/credentials").get_json() == {
        "success": True,
        "remember": False,
        "username": "",
        "password": "",
    }
    assert storage_case.store.get_entity("crm_credentials", key).deleted is True
    assert_delete_actor(storage_case, "crm_credentials", key)


def test_completed_job_logs_are_persisted_once_and_filtered_by_page(
    app_module, client, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    job = {
        "job_id": f"query-{storage_case.name}",
        "slot_id": "query-2",
        "success": 1,
        "failed": 0,
        "started_at": UPDATED_AT,
        "finished_at": "2026-07-16 10:01:00",
        "logs": [
            {"id": 1, "time": "10:00:00", "level": "info", "message": "start"},
            {"id": 2, "time": "10:01:00", "level": "success", "message": "done"},
        ],
    }

    app_module._persist_job_logs("crm", "query", job)
    app_module._persist_job_logs("crm", "query", job)
    storage_case.store.append_log("transfer", "info", "other page")

    response = client.get("/api/logs?category=crm&limit=1")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["category"] == "crm"
    assert [row["message"] for row in payload["logs"]] == ["done"]
    assert payload["logs"][0]["time"] == "10:01:00"
    assert payload["logs"][0]["context"]["job_type"] == "query"
    assert payload["logs"][0]["context"]["slot_id"] == "query-2"
    assert len(storage_case.store.list_logs("crm", 10)) == 2


def test_logs_api_rejects_unknown_page_category(client):
    response = client.get("/api/logs?category=everything")

    assert response.status_code == 400
    assert response.get_json()["success"] is False
