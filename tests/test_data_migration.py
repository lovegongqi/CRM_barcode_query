import base64
import json

import pytest

from crm_storage.base import ReportRecord
from crm_storage.postgres_store import PostgresStore
from scripts.migrate_files_to_postgres import (
    MigrationError,
    load_source_inventory,
    main,
    migrate_directory,
    verify_directory,
)


@pytest.fixture
def pg_store(pg_temporary_database, monkeypatch):
    monkeypatch.setenv(
        "CRM_CREDENTIALS_KEY",
        base64.urlsafe_b64encode(b"m" * 32).decode("ascii"),
    )
    store = PostgresStore(pg_temporary_database, node_id="hk")
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def sample_legacy_data(tmp_path):
    data_dir = tmp_path / "data"
    config_dir = data_dir / "config"
    barcode_dir = data_dir / "barcode"
    archive_dir = barcode_dir / "archived"
    config_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)

    files = {
        "runtime_config.json": {
            "query_workers": 5,
            "transfer_workers": 2,
            "own_dealer_name": "测试省代",
        },
        "accounts.json": [
            {
                "id": "admin",
                "username": "admin",
                "display_name": "管理员",
                "password": "tool-secret",
                "permissions": ["crm", "results"],
            }
        ],
        "crm_credentials.json": {
            "admin": {
                "remember": True,
                "username": "crm-user",
                "password": "crm-secret",
            }
        },
        "product_library.json": {
            "531": {
                "prefix": "531",
                "product_code": "906020907",
                "product_name": "壁挂式饮水机",
            }
        },
        "distributor_history.json": ["南昌怡口净水"],
        "barcode_data.json": {
            "5312503010858": {
                "remark": "active",
                "archived": False,
                "querySlotId": "query-1",
                "queryUpdatedAt": "2026-07-16 10:00:00",
            },
            "8452508130954": {
                "remark": "archived",
                "archived": True,
                "querySlotId": "query-2",
                "queryUpdatedAt": "2026-07-16 10:01:00",
            },
        },
    }
    for filename, payload in files.items():
        (config_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    (barcode_dir / "5312503010858.html").write_bytes(b"<html>active</html>")
    (archive_dir / "8452508130954.html").write_bytes(
        b"<html>archived</html>"
    )
    return data_dir


def test_migration_is_idempotent(sample_legacy_data, pg_store):
    before = {
        path.relative_to(sample_legacy_data): path.read_bytes()
        for path in sample_legacy_data.rglob("*")
        if path.is_file()
    }

    first = migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    second = migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    verification = verify_directory(sample_legacy_data, pg_store)

    assert first.imported_reports == 2
    assert second.imported_reports == 0
    assert second.imported_entities == 0
    assert verification.ok is True
    assert verification.source_totals == verification.destination_totals == {
        "active_reports": 1,
        "archived_reports": 1,
        "metadata_entries": 2,
        "product_rules": 1,
        "distributors": 1,
        "accounts": 1,
        "credentials": 1,
    }
    after = {
        path.relative_to(sample_legacy_data): path.read_bytes()
        for path in sample_legacy_data.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_migration_rejects_malformed_json_without_changing_source(
    sample_legacy_data, pg_store
):
    broken = sample_legacy_data / "config" / "product_library.json"
    broken.write_text('{"531": ', encoding="utf-8")
    before = broken.read_bytes()

    with pytest.raises(MigrationError, match="product_library.json"):
        migrate_directory(sample_legacy_data, pg_store, node_id="hk")

    assert broken.read_bytes() == before
    assert pg_store.list_reports(False) == []
    assert pg_store.load_entities("product_rule") == []


def test_verification_detects_report_hash_mismatch(sample_legacy_data, pg_store):
    migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    original = pg_store.get_report("5312503010858")
    pg_store.put_report(ReportRecord(
        barcode=original.barcode,
        html=b"<html>changed</html>",
        archived=original.archived,
        query_slot=original.query_slot,
        updated_at=original.updated_at,
    ))

    verification = verify_directory(sample_legacy_data, pg_store)

    assert verification.ok is False
    assert "report/5312503010858: SHA-256 不一致" in verification.mismatches


def test_cli_returns_two_when_verification_finds_a_stale_hash_marker(
    sample_legacy_data, pg_store, capsys
):
    migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    original = pg_store.get_report("5312503010858")
    pg_store.put_report(ReportRecord(
        barcode=original.barcode,
        html=b"<html>changed after marker</html>",
        archived=original.archived,
        query_slot=original.query_slot,
        updated_at=original.updated_at,
    ))

    exit_code = main([
        "--data-dir",
        str(sample_legacy_data),
        "--database-url",
        pg_store.database_url,
        "--node-id",
        "hk",
        "--verify",
    ])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "report/5312503010858: SHA-256 不一致" in captured.err


def test_cli_returns_one_for_import_failure(sample_legacy_data, pg_store, capsys):
    (sample_legacy_data / "config" / "accounts.json").write_text(
        "not-json", encoding="utf-8"
    )

    exit_code = main([
        "--data-dir",
        str(sample_legacy_data),
        "--database-url",
        pg_store.database_url,
        "--node-id",
        "hk",
        "--verify",
    ])

    assert exit_code == 1
    assert "迁移失败" in capsys.readouterr().err


def test_cli_success_prints_exact_source_and_destination_totals(
    sample_legacy_data, pg_store, capsys
):
    exit_code = main([
        "--data-dir",
        str(sample_legacy_data),
        "--database-url",
        pg_store.database_url,
        "--node-id",
        "hk",
        "--verify",
    ])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "源数据：" in output
    assert "PostgreSQL：" in output
    for key, value in {
        "active_reports": 1,
        "archived_reports": 1,
        "metadata_entries": 2,
        "product_rules": 1,
        "distributors": 1,
        "accounts": 1,
        "credentials": 1,
    }.items():
        assert output.count(f"{key}: {value}") == 2
    assert "校验通过：数量和 SHA-256 一致" in output


def test_tombstoned_source_rows_are_not_reimported(sample_legacy_data, pg_store):
    tombstones = [
        {"kind": "distributor", "key": "南昌怡口净水"},
        {"kind": "report", "key": "5312503010858"},
        {"kind": "barcode_metadata", "key": "5312503010858"},
    ]
    (sample_legacy_data / "config" / "tombstones.json").write_text(
        json.dumps(tombstones, ensure_ascii=False), encoding="utf-8"
    )

    inventory = load_source_inventory(sample_legacy_data)
    result = migrate_directory(sample_legacy_data, pg_store, node_id="hk")

    assert inventory.totals["active_reports"] == 0
    assert inventory.totals["metadata_entries"] == 1
    assert inventory.totals["distributors"] == 0
    assert result.imported_reports == 1
    assert pg_store.get_report("5312503010858") is None


def test_migration_writes_business_objects_in_deterministic_order(
    sample_legacy_data, pg_store
):
    migrate_directory(sample_legacy_data, pg_store, node_id="hk")

    business_events = [
        (event.entity_type, event.entity_key)
        for event in pg_store.fetch_events("hk", 0, 100)
        if not (
            event.entity_type == "runtime_setting"
            and event.entity_key.startswith("migration/source_hash/")
        )
    ]

    assert business_events == [
        ("runtime_setting", "runtime"),
        ("account", "admin"),
        ("crm_credentials", "admin"),
        ("product_rule", "531"),
        ("distributor", "南昌怡口净水"),
        ("barcode_metadata", "5312503010858"),
        ("barcode_metadata", "8452508130954"),
        ("barcode_report", "5312503010858"),
        ("barcode_report", "8452508130954"),
    ]


def test_changed_report_state_is_reimported_even_when_html_is_unchanged(
    sample_legacy_data, pg_store
):
    migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    active_path = sample_legacy_data / "barcode" / "5312503010858.html"
    archived_path = (
        sample_legacy_data / "barcode" / "archived" / "5312503010858.html"
    )
    active_path.rename(archived_path)
    metadata_path = sample_legacy_data / "config" / "barcode_data.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["5312503010858"].update({
        "archived": True,
        "querySlotId": "query-5",
        "queryUpdatedAt": "2026-07-16 11:00:00",
    })
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = migrate_directory(sample_legacy_data, pg_store, node_id="hk")
    report = pg_store.get_report("5312503010858")

    assert result.imported_reports == 1
    assert report.archived is True
    assert report.query_slot == "query-5"
    assert report.updated_at == "2026-07-16 11:00:00"
