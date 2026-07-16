import base64
import json
import re
import threading
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


def test_healthz_is_public_and_checks_selected_storage(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    monkeypatch.setattr(app_module, "IS_DESKTOP_APP", False)

    with app_module.app.test_client() as anonymous_client:
        response = anonymous_client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "storage": storage_case.name}


def test_healthz_returns_503_when_storage_check_fails(app_module, monkeypatch):
    class BrokenStore:
        def load_entities(self, _kind):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module, "store", BrokenStore())
    with app_module.app.test_client() as anonymous_client:
        response = anonymous_client.get("/healthz")

    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "storage": "unavailable"}


def test_delete_actor_falls_back_to_system_without_request_context(app_module):
    assert app_module._current_actor() == "system"


def test_stale_collection_save_does_not_delete_concurrent_entity(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    storage_case.store.put_entity(
        "product_rule", "531", {"product_name": "existing"}
    )
    snapshot_loaded = threading.Event()
    concurrent_write_done = threading.Event()

    def save_stale_snapshot():
        snapshot = app_module.load_product_library()
        snapshot_loaded.set()
        assert concurrent_write_done.wait(timeout=2)
        app_module.save_product_library(snapshot)

    thread = threading.Thread(target=save_stale_snapshot)
    thread.start()
    assert snapshot_loaded.wait(timeout=2)
    storage_case.store.put_entity(
        "product_rule", "845", {"product_name": "concurrent"}
    )
    concurrent_write_done.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    concurrent = storage_case.store.get_entity("product_rule", "845")
    assert concurrent is not None
    assert concurrent.deleted is False
    assert concurrent.payload["product_name"] == "concurrent"


def test_product_rule_upsert_does_not_overwrite_unrelated_stale_snapshot(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    storage_case.store.put_entity(
        "product_rule", "531", {"product_name": "concurrent"}
    )
    monkeypatch.setattr(
        app_module,
        "load_product_library",
        lambda: {"531": {"product_name": "stale"}},
    )

    assert app_module.upsert_product_library("845", "P-845", "new product") is True

    unrelated = storage_case.store.get_entity("product_rule", "531")
    assert unrelated.payload["product_name"] == "concurrent"
    assert storage_case.store.get_entity("product_rule", "845").payload == {
        "prefix": "845",
        "product_code": "P-845",
        "product_name": "new product",
        "source_barcode": "",
        "updated_at": storage_case.store.get_entity(
            "product_rule", "845"
        ).payload["updated_at"],
    }


def test_account_create_does_not_overwrite_unrelated_stale_snapshot(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    storage_case.store.put_entity(
        "account",
        "existing-id",
        {
            "id": "existing-id",
            "username": "existing",
            "display_name": "Concurrent",
            "password": "new-secret",
            "permissions": ["results"],
        },
    )
    monkeypatch.setattr(app_module, "is_admin_account", lambda: True)
    monkeypatch.setattr(
        app_module,
        "load_accounts",
        lambda: [
            {
                "id": "existing-id",
                "username": "existing",
                "display_name": "Stale",
                "password": "old-secret",
                "permissions": [],
            }
        ],
    )

    with app_module.app.test_request_context(
        "/api/accounts",
        method="POST",
        json={
            "username": "new-user",
            "display_name": "New User",
            "password": "secret",
            "permissions": ["crm"],
        },
    ):
        response = app_module.api_accounts_save()

    assert response.get_json() == {"success": True}
    unrelated = storage_case.store.get_entity("account", "existing-id")
    assert unrelated.payload["display_name"] == "Concurrent"
    assert unrelated.payload["password"] == "new-secret"


def test_default_admin_bootstrap_only_writes_admin_entity(
    app_module, storage_case, monkeypatch
):
    storage_case.store.put_entity(
        "account",
        "existing-id",
        {
            "id": "existing-id",
            "username": "existing",
            "display_name": "Existing",
            "password": "secret",
            "permissions": ["results"],
        },
    )

    class RecordingStore:
        def __init__(self, delegate):
            self.delegate = delegate
            self.put_keys = []

        def put_entity(self, kind, key, payload, actor="system"):
            self.put_keys.append((kind, key))
            return self.delegate.put_entity(kind, key, payload, actor)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    proxy = RecordingStore(storage_case.store)
    monkeypatch.setattr(app_module, "store", proxy)

    accounts = app_module.load_accounts()

    assert {row["username"] for row in accounts} == {"admin", "existing"}
    assert proxy.put_keys == [("account", "admin")]


def test_server_store_mode_does_not_touch_legacy_source_files(
    app_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    barcode_dir = data_dir / "barcode"
    config_dir = data_dir / "config"
    results_dir = data_dir / "results"
    legacy_barcode_dir = tmp_path / "legacy-barcode"
    legacy_results_dir = tmp_path / "legacy-results"
    barcode_dir.mkdir(parents=True)
    legacy_barcode_dir.mkdir()
    legacy_results_dir.mkdir()
    root_config = data_dir / "runtime_config.json"
    barcode_config = barcode_dir / "product_library.json"
    legacy_report = legacy_barcode_dir / "531.html"
    legacy_log = legacy_results_dir / "crm.json"
    root_config.write_text('{"query_workers": 4}', encoding="utf-8")
    barcode_config.write_text('{"531": {}}', encoding="utf-8")
    legacy_report.write_text("<html>legacy</html>", encoding="utf-8")
    legacy_log.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(app_module, "store", object())
    monkeypatch.setattr(app_module, "DATA_BASE_DIR", str(data_dir))
    monkeypatch.setattr(app_module, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(app_module, "BARCODE_DIR", str(barcode_dir))
    monkeypatch.setattr(app_module, "RESULTS_DIR", str(results_dir))
    monkeypatch.setenv("CRM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CRM_LEGACY_BARCODE_DIR", str(legacy_barcode_dir))
    monkeypatch.setenv("CRM_LEGACY_RESULTS_DIR", str(legacy_results_dir))
    monkeypatch.delenv("CRM_DISABLE_DATA_MIGRATION", raising=False)

    app_module._migrate_root_config_file("runtime_config.json")
    app_module._migrate_legacy_runtime_data()
    app_module._migrate_config_files_from_barcode_dir()

    assert root_config.read_text(encoding="utf-8") == '{"query_workers": 4}'
    assert barcode_config.read_text(encoding="utf-8") == '{"531": {}}'
    assert legacy_report.read_text(encoding="utf-8") == "<html>legacy</html>"
    assert legacy_log.read_text(encoding="utf-8") == "[]"
    assert not config_dir.exists()
    assert not (barcode_dir / "531.html").exists()
    assert not (results_dir / "crm.json").exists()


def test_query_result_ingestion_uses_selected_store_and_cleans_postgres_source_html(
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


def test_temporary_query_result_is_not_stored_and_is_cleaned(
    app_module, storage_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    monkeypatch.setattr(app_module, "TEMP_QUERY_DIR", str(tmp_path))
    generated = tmp_path / f"{BARCODE}.html"
    generated.write_bytes(REPORT_HTML)

    fields = app_module._ingest_query_result(
        BARCODE, str(generated), is_temporary=True
    )
    if generated.exists():
        app_module.delete_temporary_query_result(BARCODE)

    assert fields["sr1"]["newname1"] == BARCODE
    assert storage_case.store.get_report(BARCODE) is None
    assert storage_case.store.get_entity("barcode_metadata", BARCODE) is None
    assert not generated.exists()


def test_temporary_query_cleanup_explicitly_tombstones_transient_metadata(
    app_module, storage_case, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    monkeypatch.setattr(app_module, "TEMP_QUERY_DIR", str(tmp_path))
    generated = tmp_path / f"{BARCODE}.html"
    generated.write_bytes(REPORT_HTML)
    storage_case.store.put_entity(
        "barcode_metadata", BARCODE, {"temporary": True}
    )

    assert app_module.delete_temporary_query_result(BARCODE) is True

    assert not generated.exists()
    metadata = storage_case.store.get_entity("barcode_metadata", BARCODE)
    assert metadata is not None
    assert metadata.deleted is True


def test_metadata_update_does_not_overwrite_unrelated_stale_snapshot(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    storage_case.store.put_entity(
        "barcode_metadata", "concurrent", {"remark": "new"}
    )
    monkeypatch.setattr(
        app_module,
        "load_data",
        lambda: {"concurrent": {"remark": "stale"}},
    )

    app_module.update_barcode_info("target", {"remark": "target"})

    unrelated = storage_case.store.get_entity("barcode_metadata", "concurrent")
    assert unrelated.payload["remark"] == "new"
    assert storage_case.store.get_entity(
        "barcode_metadata", "target"
    ).payload["remark"] == "target"


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


def test_report_state_routes_use_storage_bundle_methods(
    app_module, client, storage_case, monkeypatch
):
    put_report(storage_case)

    class BundleOnlyStore:
        def __init__(self, delegate):
            self.delegate = delegate
            self.put_bundle_calls = 0
            self.delete_bundle_calls = 0

        def put_report_bundle(self, record, metadata, actor="system"):
            self.put_bundle_calls += 1
            return self.delegate.put_report_bundle(record, metadata, actor)

        def delete_report_bundle(self, barcode, actor="system"):
            self.delete_bundle_calls += 1
            return self.delegate.delete_report_bundle(barcode, actor)

        def put_report(self, record):
            raise AssertionError("report transitions must use put_report_bundle")

        def delete_report(self, barcode, actor="system"):
            raise AssertionError("report deletion must use delete_report_bundle")

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    proxy = BundleOnlyStore(storage_case.store)
    monkeypatch.setattr(app_module, "store", proxy)

    assert client.post(f"/api/barcodes/{BARCODE}/archive").get_json() == {
        "success": True,
        "message": "归档成功",
    }
    assert client.post(f"/api/barcodes/{BARCODE}/unarchive").get_json() == {
        "success": True,
        "message": "取消归档成功",
    }
    assert client.delete(f"/api/barcodes/{BARCODE}").get_json() == {
        "success": True,
        "message": f"已删除 {BARCODE}",
    }
    assert proxy.put_bundle_calls == 2
    assert proxy.delete_bundle_calls == 1


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


def test_xlsx_export_uses_unique_controlled_download_and_results_permission(
    app_module, client, storage_case
):
    put_report(storage_case)
    export_rows = client.get("/api/barcodes").get_json()["barcodes"]

    first = client.post(
        "/api/export/xlsx", json={"barcodes": export_rows}
    ).get_json()
    second = client.post(
        "/api/export/xlsx", json={"barcodes": export_rows}
    ).get_json()

    assert first["success"] is True
    assert second["success"] is True
    assert first["filename"] != second["filename"]
    assert re.fullmatch(r"exports/[0-9a-f]{32}\.xlsx", first["filename"])
    download_url = "/barcode/" + first["filename"]

    storage_case.store.put_entity(
        "account",
        "no-results-id",
        {
            "id": "no-results-id",
            "username": "no-results",
            "display_name": "No Results",
            "password": "secret",
            "permissions": ["crm"],
        },
    )
    with app_module.app.test_client() as forbidden_client:
        with forbidden_client.session_transaction() as forbidden_session:
            forbidden_session["account_username"] = "no-results"
        assert forbidden_client.get(download_url).status_code == 403

    downloaded = client.get(download_url)

    assert downloaded.status_code == 200
    assert downloaded.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert downloaded.data.startswith(b"PK")
    assert "attachment" in downloaded.headers["Content-Disposition"]
    assert "export_result.xlsx" in downloaded.headers["Content-Disposition"]
    assert client.get(download_url).status_code == 404
    assert client.get("/barcode/exports/not-a-token.xlsx").status_code == 404


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


def test_crm_credentials_api_preserves_payload_and_isolates_owner_entities(
    app_module, client, storage_case
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
    storage_case.store.put_entity(
        "account",
        "secondary-id",
        {
            "id": "secondary-id",
            "username": "secondary",
            "display_name": "Secondary",
            "password": "tool-secret",
            "permissions": ["crm"],
        },
    )
    with app_module.app.test_client() as secondary_client:
        with secondary_client.session_transaction() as secondary_session:
            secondary_session["account_username"] = "secondary"
        secondary_saved = secondary_client.post(
            "/api/crm/credentials",
            json={
                "remember": True,
                "username": "crm-secondary",
                "password": "secondary-secret",
            },
        )
        assert secondary_saved.get_json() == {"success": True, "remember": True}
        assert secondary_client.get("/api/crm/credentials").get_json() == {
            "success": True,
            "remember": True,
            "username": "crm-secondary",
            "password": "secondary-secret",
        }

    operator_row = storage_case.store.get_entity("crm_credentials", "operator")
    secondary_row = storage_case.store.get_entity("crm_credentials", "secondary")
    assert operator_row.payload["username"] == "crm-user"
    assert secondary_row.payload["username"] == "crm-secondary"
    assert client.get("/api/crm/credentials").get_json()["username"] == "crm-user"

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
    assert storage_case.store.get_entity(
        "crm_credentials", "operator"
    ).deleted is True
    assert storage_case.store.get_entity(
        "crm_credentials", "secondary"
    ).deleted is False
    assert_delete_actor(storage_case, "crm_credentials", "operator")


def test_legacy_default_credential_map_lazily_migrates_current_owner(
    app_module, client, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    legacy = {
        "remember": True,
        "username": "legacy-user",
        "password": "legacy-secret",
    }
    storage_case.store.put_entity(
        "crm_credentials", "default", {"operator": legacy}
    )

    assert client.get("/api/crm/credentials").get_json() == {
        "success": True,
        "remember": True,
        "username": "legacy-user",
        "password": "legacy-secret",
    }
    migrated = storage_case.store.get_entity("crm_credentials", "operator")
    assert migrated is not None
    assert migrated.deleted is False
    assert migrated.payload == legacy


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

    assert app_module._persist_job_logs("crm", "query", job) is True
    assert app_module._persist_job_logs("crm", "query", job) is True
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


def test_job_log_retry_fills_partial_failure_without_duplicates(
    app_module, storage_case, monkeypatch
):
    class FailSecondAppendOnce:
        def __init__(self, delegate):
            self.delegate = delegate
            self.calls = 0
            self.failed = False

        def append_log(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("forced second log failure")
            return self.delegate.append_log(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

    proxy = FailSecondAppendOnce(storage_case.store)
    monkeypatch.setattr(app_module, "store", proxy)
    job = {
        "job_id": f"retry-{storage_case.name}",
        "started_at": UPDATED_AT,
        "finished_at": "2026-07-16 10:01:00",
        "logs": [
            {"id": 1, "time": "10:00:00", "level": "info", "message": "start"},
            {"id": 2, "time": "10:01:00", "level": "success", "message": "done"},
        ],
    }

    assert app_module._persist_job_logs("crm", "query", job) is False
    assert app_module._persist_job_logs("crm", "query", job) is True

    rows = storage_case.store.list_logs("crm", 10)
    assert [row["message"] for row in rows] == ["start", "done"]
    assert len({row["event_id"] for row in rows}) == 2


def test_logs_api_uses_stable_keys_and_full_timestamp_descending_order(
    app_module, client, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    job = {
        "job_id": f"ordered-{storage_case.name}",
        "started_at": UPDATED_AT,
        "finished_at": "2026-07-16 11:00:00",
        "logs": [
            {
                "id": 1,
                "time": "10:00:00",
                "created_at": "2026-07-16 10:00:00",
                "level": "info",
                "message": "older",
            },
            {
                "id": 2,
                "time": "11:00:00",
                "created_at": "2026-07-16 11:00:00",
                "level": "success",
                "message": "newer",
            },
        ],
    }
    assert app_module._persist_job_logs("crm", "query", job) is True

    logs = client.get("/api/logs?category=crm&limit=10").get_json()["logs"]

    assert [row["message"] for row in logs] == ["newer", "older"]
    assert [row["created_at"] for row in logs] == [
        "2026-07-16 11:00:00",
        "2026-07-16 10:00:00",
    ]
    assert [row["key"] for row in logs] == [
        f"job:ordered-{storage_case.name}:2",
        f"job:ordered-{storage_case.name}:1",
    ]


@pytest.mark.parametrize(
    ("scope", "category"),
    [("query", "crm"), ("transfer", "transfer"), ("all", "accounts")],
)
def test_bulk_login_immediate_completion_is_persisted_in_scope_category(
    app_module, client, storage_case, monkeypatch, scope, category
):
    class LoggedInPool:
        def slots_payload(self):
            return {
                "query": [
                    {
                        "id": "query-1",
                        "kind": "query",
                        "label": "查询 1",
                        "logged_in": True,
                    }
                ],
                "transfer": [
                    {
                        "id": "transfer-1",
                        "kind": "transfer",
                        "label": "移库 1",
                        "logged_in": True,
                    }
                ],
            }

    monkeypatch.setattr(app_module, "store", storage_case.store)
    monkeypatch.setattr(app_module, "crm_pool", LoggedInPool())
    app_module.bulk_login_jobs.clear()
    app_module.latest_bulk_login_job_by_scope.clear()

    payload = client.post(
        "/api/crm/bulk-login/start",
        json={"scope": scope, "username": "crm", "password": "secret"},
    ).get_json()

    assert payload["done"] is True
    rows = storage_case.store.list_logs(category, 10)
    assert [row["message"] for row in rows] == ["所有 CRM 通道都已登录"]
    assert rows[0]["context"]["job_type"] == "bulk-login"


@pytest.mark.parametrize("slot_status", ["waiting_captcha", "opening"])
def test_bulk_login_cancel_finalizes_and_persists_once(
    app_module, client, storage_case, monkeypatch, slot_status
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    app_module.bulk_login_jobs.clear()
    app_module.latest_bulk_login_job_by_scope.clear()
    slot = {"id": "query-1", "kind": "query", "label": "查询 1"}
    job = app_module._empty_bulk_login_job("query", [slot])
    job.update(
        {
            "running": True,
            "started_at": UPDATED_AT,
            "step1_done": slot_status == "waiting_captcha",
        }
    )
    job["slots"][0]["status"] = slot_status
    app_module.bulk_login_jobs[job["job_id"]] = job
    app_module.latest_bulk_login_job_by_scope["query"] = job["job_id"]

    first = client.post(
        "/api/crm/bulk-login/cancel",
        json={"scope": "query", "job_id": job["job_id"]},
    ).get_json()
    second = client.post(
        "/api/crm/bulk-login/cancel",
        json={"scope": "query", "job_id": job["job_id"]},
    ).get_json()
    app_module._run_bulk_login_job(job["job_id"], "crm", "secret")

    assert first["done"] is True
    assert second["done"] is True
    assert [
        row["message"] for row in job["logs"] if row["message"] == "批量登录已取消"
    ] == ["批量登录已取消"]
    rows = storage_case.store.list_logs("crm", 10)
    assert [row["message"] for row in rows] == ["批量登录已取消"]
    assert storage_case.store.list_logs("accounts", 10) == []


@pytest.mark.parametrize(
    ("case", "category", "job_type", "message"),
    [
        ("query", "crm", "query", "query cancelled"),
        ("summary", "transfer", "transfer-summary", "summary failed"),
        ("transfer", "transfer", "transfer", "transfer complete"),
        ("service-close", "results", "service-close", "service close failed"),
    ],
)
def test_registered_job_wrappers_persist_each_terminal_path(
    app_module,
    storage_case,
    monkeypatch,
    case,
    category,
    job_type,
    message,
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    definitions = {
        "query": (
            app_module._empty_batch_job("query-1", []),
            app_module.batch_job_lock,
            app_module.batch_jobs,
            "_run_batch_job_inner",
            "_run_batch_job",
            (object(), [], 0, None),
        ),
        "summary": (
            app_module._empty_summary_job("transfer-1"),
            app_module.summary_job_lock,
            app_module.summary_jobs,
            "_run_summary_job_inner",
            "_run_summary_job",
            (object(), [], "移出", "Dealer", None),
        ),
        "transfer": (
            app_module._empty_transfer_job("transfer-1", {}),
            app_module.transfer_job_lock,
            app_module.transfer_jobs,
            "_run_transfer_job_inner",
            "_run_transfer_job",
            (object(), {}, "Dealer", "移出", ""),
        ),
        "service-close": (
            app_module._empty_service_close_job("query-1", []),
            app_module.service_close_job_lock,
            app_module.service_close_jobs,
            "_run_service_close_job_inner",
            "_run_service_close_job",
            ([], []),
        ),
    }
    job, lock, jobs, inner_name, wrapper_name, wrapper_args = definitions[case]
    job["running"] = True
    jobs.clear()
    jobs[job["job_id"]] = job

    def finish(job_id, *args):
        with lock:
            target = jobs[job_id]
            target["running"] = False
            target["done"] = True
            target["success"] = case == "transfer"
            target["finished_at"] = "2026-07-16 12:00:00"
            app_module._append_job_log_unlocked(
                target,
                message,
                "success" if case == "transfer" else "warn",
            )

    monkeypatch.setattr(app_module, inner_name, finish)

    getattr(app_module, wrapper_name)(job["job_id"], *wrapper_args)

    rows = storage_case.store.list_logs(category, 10)
    assert [row["message"] for row in rows] == [message]
    assert rows[0]["context"]["job_type"] == job_type


def test_product_library_job_wrapper_persists_terminal_log(
    app_module, storage_case, monkeypatch
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    with app_module.library_query_lock:
        app_module.library_query_job.update(
            {
                "job_id": f"library-{storage_case.name}",
                "running": True,
                "done": False,
                "success": False,
                "barcode": BARCODE,
                "log_seq": 0,
                "logs": [],
                "started_at": UPDATED_AT,
                "finished_at": "",
            }
        )

    def finish(*args):
        with app_module.library_query_lock:
            app_module.library_query_job["running"] = False
            app_module.library_query_job["done"] = True
            app_module.library_query_job["success"] = True
            app_module.library_query_job["finished_at"] = "2026-07-16 12:00:00"
            app_module._append_job_log_unlocked(
                app_module.library_query_job, "library complete", "success"
            )

    monkeypatch.setattr(app_module, "_run_library_query_job_inner", finish)

    app_module._run_library_query_job(BARCODE)

    rows = storage_case.store.list_logs("product-library", 10)
    assert [row["message"] for row in rows] == ["library complete"]
    assert rows[0]["context"]["job_type"] == "product-library-query"


@pytest.mark.parametrize(
    ("slot_status", "expected_success", "terminal_message"),
    [
        ("logged_in", True, "批量登录完成，成功 1 个，失败 0 个"),
        ("failed", False, "批量登录完成，成功 0 个，失败 1 个"),
    ],
)
def test_bulk_login_normal_completion_finalizes_once(
    app_module,
    storage_case,
    monkeypatch,
    slot_status,
    expected_success,
    terminal_message,
):
    monkeypatch.setattr(app_module, "store", storage_case.store)
    app_module.bulk_login_jobs.clear()
    slot = {"id": "transfer-1", "kind": "transfer", "label": "移库 1"}
    job = app_module._empty_bulk_login_job("transfer", [slot])
    job.update(
        {
            "running": True,
            "step1_done": True,
            "started_at": UPDATED_AT,
        }
    )
    job["slots"][0]["status"] = slot_status
    app_module.bulk_login_jobs[job["job_id"]] = job

    assert app_module._finalize_bulk_login_job_if_ready(job["job_id"]) is True
    assert app_module._finalize_bulk_login_job_if_ready(job["job_id"]) is True

    assert job["done"] is True
    assert job["success"] is expected_success
    assert [row["message"] for row in job["logs"]] == [terminal_message]
    rows = storage_case.store.list_logs("transfer", 10)
    assert [row["message"] for row in rows] == [terminal_message]


def test_logs_api_rejects_unknown_page_category(client):
    response = client.get("/api/logs?category=everything")

    assert response.status_code == 400
    assert response.get_json()["success"] is False
