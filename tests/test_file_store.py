from crm_storage.base import ReportRecord
from crm_storage.file_store import FileStore
import os
import subprocess
import sys


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
