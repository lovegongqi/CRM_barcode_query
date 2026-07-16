import json
import os
import tempfile
import threading
import uuid
from datetime import datetime

from .base import EntityRecord, ReportRecord


ENTITY_FILES = {
    "barcode_metadata": "barcode_data.json",
    "product_rule": "product_library.json",
    "account": "accounts.json",
    "distributor": "distributor_history.json",
    "runtime_setting": "runtime_config.json",
    "crm_credentials": "crm_credentials.json",
}


class FileStore:
    def __init__(self, data_dir=None):
        self.data_dir = data_dir or os.environ.get("CRM_DATA_DIR") or os.path.dirname(os.path.dirname(__file__))
        self.barcode_dir = os.path.join(self.data_dir, "barcode")
        self.config_dir = os.path.join(self.data_dir, "config")
        self.results_dir = os.path.join(self.data_dir, "results")
        self._lock = threading.RLock()

    def list_reports(self, archived=False):
        with self._lock:
            directory = self._report_dir(archived)
            if not os.path.isdir(directory):
                return []
            tombstones = self._tombstone_keys()
            return sorted(
                filename[:-5]
                for filename in os.listdir(directory)
                if filename.endswith(".html") and ("report", filename[:-5]) not in tombstones
            )

    def get_report(self, barcode):
        with self._lock:
            if ("report", barcode) in self._tombstone_keys():
                return None
            for archived in (False, True):
                path = self._report_path(barcode, archived)
                if not os.path.isfile(path):
                    continue
                metadata = self._entity_payloads("barcode_metadata").get(barcode, {})
                return ReportRecord(
                    barcode=barcode,
                    html=self._read_bytes(path),
                    archived=archived,
                    query_slot=str(metadata.get("querySlotId") or ""),
                    updated_at=str(metadata.get("queryUpdatedAt") or ""),
                )
            return None

    def put_report(self, record):
        with self._lock:
            path = self._report_path(record.barcode, record.archived)
            self._write_bytes(path, record.html)
            opposite_path = self._report_path(record.barcode, not record.archived)
            if os.path.exists(opposite_path):
                os.unlink(opposite_path)
            self._clear_tombstone("report", record.barcode)
            metadata = self._entity_payloads("barcode_metadata")
            row = dict(metadata.get(record.barcode, {}))
            row.update({
                "archived": record.archived,
                "querySlotId": record.query_slot,
                "queryUpdatedAt": record.updated_at,
            })
            metadata[record.barcode] = row
            self._write_entity_payloads("barcode_metadata", metadata)

    def delete_report(self, barcode, actor="system"):
        with self._lock:
            if self.get_report(barcode) is None:
                return False
            self._set_tombstone("report", barcode, actor)
            return True

    def load_entities(self, kind, include_deleted=False):
        with self._lock:
            self._migrate_legacy_distributor_tombstones(kind)
            payloads = self._entity_payloads(kind)
            tombstones = self._tombstone_keys()
            rows = []
            for key, payload in payloads.items():
                deleted = (kind, key) in tombstones
                if deleted and not include_deleted:
                    continue
                rows.append(EntityRecord(key, dict(payload), deleted, str(payload.get("updated_at") or "")))
            return rows

    def get_entity(self, kind, key):
        with self._lock:
            self._migrate_legacy_distributor_tombstones(kind)
            payload = self._entity_payloads(kind).get(key)
            if payload is None:
                return None
            deleted = (kind, key) in self._tombstone_keys()
            return EntityRecord(key, dict(payload), deleted, str(payload.get("updated_at") or ""))

    def put_entity(self, kind, key, payload, actor="system"):
        with self._lock:
            self._migrate_legacy_distributor_tombstones(kind)
            payloads = self._entity_payloads(kind)
            payloads[key] = dict(payload)
            self._write_entity_payloads(kind, payloads)
            self._clear_tombstone(kind, key)

    def delete_entity(self, kind, key, actor="system"):
        with self._lock:
            self._migrate_legacy_distributor_tombstones(kind)
            if key not in self._entity_payloads(kind):
                return False
            self._set_tombstone(kind, key, actor)
            return True

    def append_log(self, category, level, message, context=None):
        with self._lock:
            event_id = str(uuid.uuid4())
            rows = self._read_json(self._log_path(category), [])
            if not isinstance(rows, list):
                rows = []
            rows.append({
                "event_id": event_id,
                "level": level,
                "message": message,
                "context": context or {},
                "created_at": self._now(),
            })
            self._write_json(self._log_path(category), rows)
            return event_id

    def list_logs(self, category, limit=500):
        with self._lock:
            rows = self._read_json(self._log_path(category), [])
            return rows[-limit:] if isinstance(rows, list) else []

    def _entity_payloads(self, kind):
        path = self._entity_path(kind)
        data = self._read_json(path, self._empty_entity_value(kind))
        if kind == "runtime_setting":
            return {"runtime": data} if isinstance(data, dict) else {}
        if kind == "account":
            return {
                self._account_key(row, index): row
                for index, row in enumerate(data if isinstance(data, list) else [])
                if isinstance(row, dict)
            }
        if kind == "distributor":
            return {
                str(row): {"value": str(row)}
                for row in (data if isinstance(data, list) else [])
                if isinstance(row, str)
            }
        return {
            str(key): value
            for key, value in (data.items() if isinstance(data, dict) else [])
            if isinstance(value, dict)
        }

    def _write_entity_payloads(self, kind, payloads):
        if kind == "runtime_setting":
            self._write_json(self._entity_path(kind), payloads.get("runtime", {}))
        elif kind == "account":
            self._write_json(self._entity_path(kind), list(payloads.values()))
        elif kind == "distributor":
            self._write_json(self._entity_path(kind), [row.get("value", key) for key, row in payloads.items()])
        else:
            self._write_json(self._entity_path(kind), payloads)

    def _entity_path(self, kind):
        try:
            filename = ENTITY_FILES[kind]
        except KeyError as error:
            raise ValueError(f"Unsupported entity kind: {kind}") from error
        return os.path.join(self.config_dir, filename)

    def _empty_entity_value(self, kind):
        return [] if kind in ("account", "distributor") else {}

    def _account_key(self, row, index):
        return str(row.get("id") or row.get("username") or index)

    def _report_dir(self, archived):
        return os.path.join(self.barcode_dir, "archived") if archived else self.barcode_dir

    def _report_path(self, barcode, archived):
        return os.path.join(self._report_dir(archived), f"{barcode}.html")

    def _tombstone_path(self):
        return os.path.join(self.config_dir, "tombstones.json")

    def _migrate_legacy_distributor_tombstones(self, kind):
        if kind != "distributor":
            return
        path = os.path.join(self.config_dir, "distributor_history_deleted.json")
        deleted = self._read_json(path, [])
        if not isinstance(deleted, list):
            return
        names = [str(row) for row in deleted if isinstance(row, str) and row]
        if not names:
            return
        payloads = self._entity_payloads("distributor")
        payloads_changed = False
        for name in names:
            if name not in payloads:
                payloads[name] = {"value": name}
                payloads_changed = True
        if payloads_changed:
            self._write_entity_payloads("distributor", payloads)
        tombstones = self._read_json(self._tombstone_path(), [])
        tombstones = tombstones if isinstance(tombstones, list) else []
        tombstoned = {(row.get("kind"), row.get("key")) for row in tombstones if isinstance(row, dict)}
        tombstones_changed = False
        for name in names:
            if ("distributor", name) not in tombstoned:
                tombstones.append({
                    "kind": "distributor",
                    "key": name,
                    "actor": "legacy",
                    "deleted_at": self._now(),
                    "event_id": str(uuid.uuid4()),
                })
                tombstones_changed = True
        if tombstones_changed:
            self._write_json(self._tombstone_path(), tombstones)
        self._write_json(path, [])

    def _tombstone_keys(self):
        rows = self._read_json(self._tombstone_path(), [])
        if not isinstance(rows, list):
            return set()
        return {
            (str(row.get("kind")), str(row.get("key")))
            for row in rows
            if isinstance(row, dict) and row.get("kind") and row.get("key")
        }

    def _set_tombstone(self, kind, key, actor):
        rows = self._read_json(self._tombstone_path(), [])
        rows = rows if isinstance(rows, list) else []
        rows = [row for row in rows if not (row.get("kind") == kind and row.get("key") == key)]
        rows.append({
            "kind": kind,
            "key": key,
            "actor": actor,
            "deleted_at": self._now(),
            "event_id": str(uuid.uuid4()),
        })
        self._write_json(self._tombstone_path(), rows)

    def _clear_tombstone(self, kind, key):
        rows = self._read_json(self._tombstone_path(), [])
        if not isinstance(rows, list):
            return
        filtered = [row for row in rows if not (row.get("kind") == kind and row.get("key") == key)]
        if len(filtered) != len(rows):
            self._write_json(self._tombstone_path(), filtered)

    def _log_path(self, category):
        safe_category = "".join(char for char in str(category) if char.isalnum() or char in "-_") or "default"
        return os.path.join(self.results_dir, f"{safe_category}.json")

    def _read_json(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return default

    def _read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def _write_json(self, path, value):
        self._write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    def _write_text(self, path, value):
        self._atomic_write(path, value.encode("utf-8"))

    def _write_bytes(self, path, value):
        self._atomic_write(path, value)

    def _atomic_write(self, path, value):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temporary_path = handle.name
            try:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
                os.replace(temporary_path, path)
            except Exception:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
                raise

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
