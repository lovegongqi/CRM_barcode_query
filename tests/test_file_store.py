from crm_storage.base import ReportRecord
from crm_storage.file_store import FileStore
import os
import subprocess
import sys
import pytest


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


def test_app_uses_store_backed_product_library(tmp_path):
    environment = os.environ | {
        "CRM_DATA_DIR": str(tmp_path),
        "CRM_DISABLE_DATA_MIGRATION": "1",
    }
    script = """
import json
import app

assert app.store.__class__.__name__ == 'FileStore'
app.save_product_library({'845': {'product_name': '壁挂式饮水机'}})
assert app.load_product_library()['845']['product_name'] == '壁挂式饮水机'
with open(app.PRODUCT_LIBRARY_FILE, encoding='utf-8') as handle:
    assert json.load(handle) == {'845': {'product_name': '壁挂式饮水机'}}
"""
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_legacy_deleted_distributors_become_tombstones(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "distributor_history.json").write_text('["保留", "已删除"]', encoding="utf-8")
    (config_dir / "distributor_history_deleted.json").write_text('["已删除"]', encoding="utf-8")

    store = FileStore(str(tmp_path))

    assert [row.key for row in store.load_entities("distributor")] == ["保留"]


def test_restored_legacy_distributor_stays_restored_after_new_store_instance(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "distributor_history_deleted.json").write_text('["已恢复"]', encoding="utf-8")

    store = FileStore(str(tmp_path))
    assert store.load_entities("distributor") == []
    store.put_entity("distributor", "已恢复", {"value": "已恢复"})

    restored = FileStore(str(tmp_path))
    assert [row.key for row in restored.load_entities("distributor")] == ["已恢复"]


def test_put_report_replaces_active_report_when_archiving(tmp_path):
    store = FileStore(str(tmp_path))
    store.put_report(ReportRecord("845", b"active", False, "query-1", "2026-07-16 10:00:00"))
    store.put_report(ReportRecord("845", b"archived", True, "query-1", "2026-07-16 10:01:00"))

    assert not (tmp_path / "barcode" / "845.html").exists()
    assert (tmp_path / "barcode" / "archived" / "845.html").read_bytes() == b"archived"
    assert store.get_report("845").archived is True


def test_put_report_replaces_archived_report_when_restoring(tmp_path):
    store = FileStore(str(tmp_path))
    store.put_report(ReportRecord("845", b"archived", True, "query-1", "2026-07-16 10:00:00"))
    store.put_report(ReportRecord("845", b"active", False, "query-1", "2026-07-16 10:01:00"))

    assert not (tmp_path / "barcode" / "archived" / "845.html").exists()
    assert (tmp_path / "barcode" / "845.html").read_bytes() == b"active"
    assert store.get_report("845").archived is False


def test_append_log_with_stable_event_id_is_idempotent(tmp_path):
    store = FileStore(str(tmp_path))
    event_id = "1f40c2ca-047c-5d50-95ca-7d3894b849f1"

    assert store.append_log(
        "crm", "info", "once", {"job_id": "job-1"}, event_id=event_id
    ) == event_id
    restarted_store = FileStore(str(tmp_path))
    assert restarted_store.append_log(
        "crm", "info", "once", {"job_id": "job-1"}, event_id=event_id
    ) == event_id

    rows = restarted_store.list_logs("crm")
    assert len(rows) == 1
    assert rows[0]["event_id"] == event_id


def test_report_bundle_rolls_back_when_metadata_write_fails(tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    active = ReportRecord(
        "845", b"active", False, "query-1", "2026-07-16 10:00:00"
    )
    store.put_report_bundle(
        active,
        {
            "archived": False,
            "remark": "keep",
            "querySlotId": "query-1",
            "queryUpdatedAt": "2026-07-16 10:00:00",
        },
    )
    original_write = store._write_entity_payloads

    def reject_metadata(kind, payloads):
        if kind == "barcode_metadata":
            raise RuntimeError("forced metadata failure")
        return original_write(kind, payloads)

    monkeypatch.setattr(store, "_write_entity_payloads", reject_metadata)
    archived = ReportRecord(
        "845", b"archived", True, "query-1", "2026-07-16 11:00:00"
    )

    with pytest.raises(RuntimeError, match="forced metadata failure"):
        store.put_report_bundle(
            archived,
            {
                "archived": True,
                "remark": "changed",
                "querySlotId": "query-1",
                "queryUpdatedAt": "2026-07-16 11:00:00",
            },
        )

    assert store.get_report("845") == active
    assert store.get_entity("barcode_metadata", "845").payload["remark"] == "keep"
