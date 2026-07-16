#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crm_storage.base import ReportRecord
from crm_storage.postgres_store import PostgresStore


ENTITY_IMPORT_ORDER = (
    "runtime_setting",
    "account",
    "crm_credentials",
    "product_rule",
    "distributor",
    "barcode_metadata",
)

TOTAL_KEYS = (
    "active_reports",
    "archived_reports",
    "metadata_entries",
    "product_rules",
    "distributors",
    "accounts",
    "credentials",
)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceReport:
    record: ReportRecord
    digest: str
    html_digest: str


@dataclass
class SourceInventory:
    entities: dict[str, list[tuple[str, dict]]]
    reports: list[SourceReport]

    @property
    def totals(self):
        return {
            "active_reports": sum(not row.record.archived for row in self.reports),
            "archived_reports": sum(row.record.archived for row in self.reports),
            "metadata_entries": len(self.entities["barcode_metadata"]),
            "product_rules": len(self.entities["product_rule"]),
            "distributors": len(self.entities["distributor"]),
            "accounts": len(self.entities["account"]),
            "credentials": len(self.entities["crm_credentials"]),
        }


@dataclass
class MigrationResult:
    imported_reports: int = 0
    imported_entities: int = 0
    imported_by_kind: dict[str, int] = field(default_factory=dict)


@dataclass
class VerificationResult:
    ok: bool
    source_totals: dict[str, int]
    destination_totals: dict[str, int]
    mismatches: list[str]


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_payload(payload):
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _find_config_file(data_dir, filename):
    candidates = (
        data_dir / "config" / filename,
        data_dir / filename,
        data_dir / "barcode" / filename,
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _read_json(data_dir, filename, expected_type, default):
    path = _find_config_file(data_dir, filename)
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"无法读取 {path}: {error}") from error
    if not isinstance(value, expected_type):
        expected = expected_type.__name__
        raise MigrationError(f"{path} 格式错误，应为 {expected}")
    return value


def _entity_rows(mapping, tombstones, kind):
    return sorted(
        (
            (str(key), dict(payload))
            for key, payload in mapping.items()
            if isinstance(payload, dict) and (kind, str(key)) not in tombstones
        ),
        key=lambda row: row[0],
    )


def _account_rows(rows, tombstones):
    result = []
    for index, payload in enumerate(rows):
        if not isinstance(payload, dict):
            continue
        key = str(payload.get("id") or payload.get("username") or index)
        if ("account", key) not in tombstones:
            result.append((key, dict(payload)))
    return sorted(result, key=lambda row: row[0])


def _distributor_rows(rows, deleted_names, tombstones):
    values = {
        str(value)
        for value in rows
        if isinstance(value, str)
        and value
        and value not in deleted_names
        and ("distributor", value) not in tombstones
    }
    return [(value, {"value": value}) for value in sorted(values)]


def _report_time(path, metadata):
    value = str(metadata.get("queryUpdatedAt") or "")
    if value:
        return value
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _report_source_digest(record, html_digest):
    return _sha256_payload({
        "barcode": record.barcode,
        "html_sha256": html_digest,
        "archived": record.archived,
        "query_slot": record.query_slot,
        "updated_at": record.updated_at,
    })


def load_source_inventory(data_dir):
    data_dir = Path(data_dir).expanduser().resolve()
    runtime = _read_json(data_dir, "runtime_config.json", dict, {})
    accounts = _read_json(data_dir, "accounts.json", list, [])
    credentials = _read_json(data_dir, "crm_credentials.json", dict, {})
    product_rules = _read_json(data_dir, "product_library.json", dict, {})
    distributors = _read_json(data_dir, "distributor_history.json", list, [])
    metadata = _read_json(data_dir, "barcode_data.json", dict, {})
    deleted_distributors = set(
        value
        for value in _read_json(
            data_dir, "distributor_history_deleted.json", list, []
        )
        if isinstance(value, str)
    )
    tombstone_rows = _read_json(data_dir, "tombstones.json", list, [])
    tombstones = {
        (str(row.get("kind")), str(row.get("key")))
        for row in tombstone_rows
        if isinstance(row, dict) and row.get("kind") and row.get("key")
    }

    entities = {
        "runtime_setting": (
            [] if ("runtime_setting", "runtime") in tombstones else [("runtime", runtime)]
        ),
        "account": _account_rows(accounts, tombstones),
        "crm_credentials": _entity_rows(
            credentials, tombstones, "crm_credentials"
        ),
        "product_rule": _entity_rows(product_rules, tombstones, "product_rule"),
        "distributor": _distributor_rows(
            distributors, deleted_distributors, tombstones
        ),
        "barcode_metadata": _entity_rows(
            metadata, tombstones, "barcode_metadata"
        ),
    }

    reports = []
    seen_barcodes = set()
    report_directories = (
        (data_dir / "barcode", False),
        (data_dir / "barcode" / "archived", True),
    )
    metadata_by_barcode = dict(entities["barcode_metadata"])
    for directory, archived in report_directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.html")):
            barcode = path.stem
            if ("report", barcode) in tombstones:
                continue
            if barcode in seen_barcodes:
                raise MigrationError(f"条码 {barcode} 同时存在于活动和归档目录")
            seen_barcodes.add(barcode)
            payload = metadata_by_barcode.get(barcode, {})
            html = path.read_bytes()
            record = ReportRecord(
                barcode=barcode,
                html=html,
                archived=archived,
                query_slot=str(payload.get("querySlotId") or ""),
                updated_at=_report_time(path, payload),
            )
            html_digest = _sha256_bytes(html)
            reports.append(SourceReport(
                record=record,
                digest=_report_source_digest(record, html_digest),
                html_digest=html_digest,
            ))
    reports.sort(key=lambda row: (row.record.archived, row.record.barcode))
    return SourceInventory(entities=entities, reports=reports)


def _marker_key(kind, key):
    return f"migration/source_hash/{kind}/{key}"


def _marker_matches(store, kind, key, digest):
    row = store.get_entity("runtime_setting", _marker_key(kind, key))
    return bool(
        row
        and not row.deleted
        and isinstance(row.payload, dict)
        and row.payload.get("sha256") == digest
    )


def _save_marker(store, kind, key, digest, actor):
    store.put_entity(
        "runtime_setting",
        _marker_key(kind, key),
        {"sha256": digest, "source_kind": kind, "source_key": key},
        actor,
    )


def migrate_directory(data_dir, store, node_id="hk"):
    inventory = load_source_inventory(data_dir)
    result = MigrationResult(
        imported_by_kind={kind: 0 for kind in (*ENTITY_IMPORT_ORDER, "report")}
    )
    actor = f"migration:{node_id}"

    for kind in ENTITY_IMPORT_ORDER:
        for key, payload in inventory.entities[kind]:
            digest = _sha256_payload(payload)
            if _marker_matches(store, kind, key, digest):
                continue
            store.put_entity(kind, key, payload, actor)
            _save_marker(store, kind, key, digest, actor)
            result.imported_entities += 1
            result.imported_by_kind[kind] += 1

    for source_report in inventory.reports:
        barcode = source_report.record.barcode
        if _marker_matches(store, "report", barcode, source_report.digest):
            continue
        store.put_report(source_report.record)
        _save_marker(store, "report", barcode, source_report.digest, actor)
        result.imported_reports += 1
        result.imported_by_kind["report"] += 1

    return result


def _destination_totals(store):
    return {
        "active_reports": len(store.list_reports(False)),
        "archived_reports": len(store.list_reports(True)),
        "metadata_entries": len(store.load_entities("barcode_metadata")),
        "product_rules": len(store.load_entities("product_rule")),
        "distributors": len(store.load_entities("distributor")),
        "accounts": len(store.load_entities("account")),
        "credentials": len(store.load_entities("crm_credentials")),
    }


def _entity_mismatches(inventory, store):
    mismatches = []
    for kind in ENTITY_IMPORT_ORDER:
        source_rows = dict(inventory.entities[kind])
        destination_rows = {
            row.key: row.payload for row in store.load_entities(kind)
            if not (kind == "runtime_setting" and row.key.startswith("migration/source_hash/"))
        }
        source_keys = set(source_rows)
        destination_keys = set(destination_rows)
        for key in sorted(source_keys - destination_keys):
            mismatches.append(f"{kind}/{key}: 目标缺失")
        for key in sorted(destination_keys - source_keys):
            mismatches.append(f"{kind}/{key}: 目标多出")
        for key in sorted(source_keys & destination_keys):
            if _sha256_payload(source_rows[key]) != _sha256_payload(destination_rows[key]):
                mismatches.append(f"{kind}/{key}: SHA-256 不一致")
    return mismatches


def _report_mismatches(inventory, store):
    mismatches = []
    source_reports = {
        row.record.barcode: row for row in inventory.reports
    }
    destination_keys = set(store.list_reports(False)) | set(store.list_reports(True))
    source_keys = set(source_reports)
    for barcode in sorted(source_keys - destination_keys):
        mismatches.append(f"report/{barcode}: 目标缺失")
    for barcode in sorted(destination_keys - source_keys):
        mismatches.append(f"report/{barcode}: 目标多出")
    for barcode in sorted(source_keys & destination_keys):
        source = source_reports[barcode]
        destination = store.get_report(barcode)
        if destination is None:
            mismatches.append(f"report/{barcode}: 目标缺失")
            continue
        if destination.archived != source.record.archived:
            mismatches.append(f"report/{barcode}: 归档状态不一致")
        if destination.query_slot != source.record.query_slot:
            mismatches.append(f"report/{barcode}: 查询通道不一致")
        if destination.updated_at != source.record.updated_at:
            mismatches.append(f"report/{barcode}: 更新时间不一致")
        if _sha256_bytes(destination.html) != source.html_digest:
            mismatches.append(f"report/{barcode}: SHA-256 不一致")
    return mismatches


def verify_directory(data_dir, store):
    inventory = load_source_inventory(data_dir)
    source_totals = inventory.totals
    destination_totals = _destination_totals(store)
    mismatches = []
    for key in TOTAL_KEYS:
        if source_totals[key] != destination_totals[key]:
            mismatches.append(
                f"{key}: 源 {source_totals[key]}，目标 {destination_totals[key]}"
            )
    mismatches.extend(_entity_mismatches(inventory, store))
    mismatches.extend(_report_mismatches(inventory, store))
    return VerificationResult(
        ok=not mismatches,
        source_totals=source_totals,
        destination_totals=destination_totals,
        mismatches=mismatches,
    )


def _build_parser():
    parser = argparse.ArgumentParser(description="将 CRM 文件数据安全迁移到 PostgreSQL")
    parser.add_argument("--data-dir", required=True, help="现有 /app/data 数据目录")
    parser.add_argument("--database-url", required=True, help="PostgreSQL 连接地址")
    parser.add_argument("--node-id", default="hk", help="写入事件的节点标识")
    parser.add_argument("--verify", action="store_true", help="导入后核对数量和 SHA-256")
    return parser


def _print_totals(title, totals):
    print(title)
    for key in TOTAL_KEYS:
        print(f"  {key}: {totals[key]}")


def main(argv=None):
    args = _build_parser().parse_args(argv)
    store = None
    try:
        store = PostgresStore(args.database_url, node_id=args.node_id)
        result = migrate_directory(args.data_dir, store, node_id=args.node_id)
        print(
            f"导入完成：实体 {result.imported_entities} 个，报告 {result.imported_reports} 个"
        )
        if not args.verify:
            return 0
        verification = verify_directory(args.data_dir, store)
        _print_totals("源数据：", verification.source_totals)
        _print_totals("PostgreSQL：", verification.destination_totals)
        if verification.ok:
            print("校验通过：数量和 SHA-256 一致")
            return 0
        print("校验失败：", file=sys.stderr)
        for mismatch in verification.mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"迁移失败：{error}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
