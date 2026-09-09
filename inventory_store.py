"""Durable persistence primitives for GYJ inventory stocktake."""

from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import json
import os
import sqlite3
import uuid


_NORMAL_CATALOG_KEYS = frozenset(
    {"barcode", "name", "spec", "model", "category", "unit", "has_serial", "initial_stock"}
)
_ANOMALY_CATALOG_KEYS = frozenset(
    {"barcode", "name", "spec", "model", "category", "unit", "initial_stock", "data_error"}
)

_SERIAL_DISCREPANCY_KINDS = {
    "system_only": "system_only_serial",
    "physical_only": "physical_only_serial",
    "other_product": "other_product_serial",
    "already_shipped": "already_shipped_serial",
    "unknown": "unknown_serial",
}

_AUDIT_EVENT_LABELS = {
    "count_entry_added": "新增分次数量",
    "count_entry_updated": "修改分次数量",
    "count_entry_deleted": "删除分次数量",
    "carton_preset_changed": "修改商品箱规",
    "carton_created": "整箱录入",
    "carton_serial_added": "箱内补录",
    "carton_serial_removed": "箱内删除",
    "carton_deleted": "删除整箱",
    "serial_expected_refreshed": "刷新账面序列号",
    "serial_scanned": "扫描序列号",
    "serial_scan_reclassified": "重新分类序列号",
    "serial_scan_removed": "删除序列号",
    "serial_item_completed": "完成序列号核对",
    "serial_item_reopened": "重新核对序列号",
    "count_adjusted_for_stock_movement": "库存变动调整实盘",
    "task_completed": "完成盘点",
    "task_reopened": "继续盘点",
}
_VISIBLE_AUDIT_EVENT_TYPES = tuple(
    event_type for event_type in _AUDIT_EVENT_LABELS
    if event_type not in {
        "serial_expected_refreshed", "serial_scan_reclassified",
    }
)

_DISCREPANCY_SELECT = """
    SELECT discrepancies.discrepancy_id AS id,
           discrepancies.task_id,
           discrepancies.barcode,
           discrepancies.serial,
           discrepancies.kind,
           discrepancies.book_quantity,
           discrepancies.counted_quantity,
           discrepancies.difference,
           discrepancies.status AS state,
           discrepancies.archived_by,
           discrepancies.archived_at,
           discrepancies.created_at,
           items.name,
           items.spec,
           items.model,
           items.category,
           items.unit,
           items.has_serial,
           items.data_error,
           tasks.completed_at,
           tasks.version AS task_version,
           scans.actor AS scan_actor,
           scans.device_id AS scan_device,
           scans.scanned_at,
           scans.lookup_barcode,
           scans.lookup_name,
           scans.warehouse,
           scans.is_checked_out
      FROM inventory_discrepancies AS discrepancies
      JOIN inventory_tasks AS tasks
        ON tasks.task_id = discrepancies.task_id
      JOIN inventory_items AS items
        ON items.task_id = discrepancies.task_id
       AND items.barcode = discrepancies.barcode
 LEFT JOIN inventory_serial_scans AS scans
        ON scans.task_id = discrepancies.task_id
       AND scans.barcode = discrepancies.barcode
       AND scans.serial = discrepancies.serial
       AND scans.active = 1
"""


class InventoryConflict(RuntimeError):
    def __init__(self, message, *, lock_owner=None):
        super().__init__(message)
        self.lock_owner = lock_owner


class InventoryVersionConflict(InventoryConflict):
    def __init__(self, message, *, current_version):
        super().__init__(message)
        self.current_version = int(current_version)


class InventoryConfirmationRequired(InventoryConflict):
    def __init__(self, message, *, pending_serial_count):
        super().__init__(message)
        self.pending_serial_count = int(pending_serial_count)


class InventoryNotFound(RuntimeError):
    pass


class InventoryPermissionDenied(RuntimeError):
    pass


def _decimal_text(number):
    # ``format(..., "f")`` does not apply the active context, unlike
    # ``normalize()``. Strip insignificant fractional zeroes explicitly.
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def normalize_quantity(value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("数量格式不正确")
    if not number.is_finite() or number < 0:
        raise ValueError("数量必须是大于或等于 0 的数字")
    return _decimal_text(number)


def quantity_difference(actual, book):
    actual_number = Decimal(normalize_quantity(actual))
    book_number = Decimal(normalize_quantity(book))
    precision = (
        max(len(actual_number.as_tuple().digits), len(book_number.as_tuple().digits))
        + abs(actual_number.as_tuple().exponent - book_number.as_tuple().exponent)
        + 2
    )
    with localcontext() as context:
        context.prec = precision
        return _decimal_text(actual_number - book_number)


def _expected_current_quantity(completed_actual, latest_book, completed_book):
    values = [
        Decimal(normalize_quantity(completed_actual)),
        Decimal(normalize_quantity(latest_book)),
        Decimal(normalize_quantity(completed_book)),
    ]
    precision = (
        sum(len(value.as_tuple().digits) for value in values)
        + max(abs(value.as_tuple().exponent) for value in values)
        + 3
    )
    with localcontext() as context:
        context.prec = precision
        return _decimal_text(values[0] + values[1] - values[2])


class InventoryStore:
    def __init__(self, db_path, now=None):
        self.db_path = db_path
        self.now = now or datetime.now
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self):
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_tasks (
                    task_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'loading',
                    version INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_sync_at TEXT,
                    last_sync_attempt_at TEXT,
                    gyj_status TEXT,
                    sync_resume_phase TEXT
                );

                CREATE TABLE IF NOT EXISTS inventory_items (
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    spec TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    unit TEXT NOT NULL DEFAULT '',
                    has_serial INTEGER NOT NULL DEFAULT 0,
                    data_error TEXT NOT NULL DEFAULT '',
                    initial_stock TEXT NOT NULL DEFAULT '0',
                    book_quantity TEXT,
                    counted_quantity TEXT,
                    completed_book_quantity TEXT,
                    completed_counted_quantity TEXT,
                    latest_book_quantity TEXT,
                    expected_current_quantity TEXT,
                    difference TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    serial_synced_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, barcode),
                    FOREIGN KEY (task_id) REFERENCES inventory_tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_item_locks (
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, barcode),
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_count_entries (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    created_device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_device_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_carton_presets (
                    barcode TEXT PRIMARY KEY,
                    carton_quantity INTEGER NOT NULL
                        CHECK (carton_quantity BETWEEN 1 AND 999),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory_cartons (
                    carton_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    preset_quantity INTEGER NOT NULL,
                    confirmed_quantity INTEGER NOT NULL,
                    start_serial TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    deleted_by TEXT,
                    deleted_device_id TEXT,
                    deleted_at TEXT,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_serial_expected (
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    serial TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    warehouse TEXT NOT NULL DEFAULT '',
                    is_checked_out INTEGER NOT NULL DEFAULT 0,
                    sync_status TEXT NOT NULL DEFAULT 'synced',
                    synced_at TEXT,
                    PRIMARY KEY (task_id, barcode, serial),
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_serial_scans (
                    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    serial TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    source_classification TEXT NOT NULL DEFAULT '',
                    lookup_barcode TEXT NOT NULL DEFAULT '',
                    lookup_name TEXT NOT NULL DEFAULT '',
                    warehouse TEXT NOT NULL DEFAULT '',
                    is_checked_out INTEGER NOT NULL DEFAULT 0,
                    device_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    carton_id INTEGER,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE,
                    FOREIGN KEY (carton_id)
                        REFERENCES inventory_cartons(carton_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS inventory_stock_movements (
                    movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    before_quantity TEXT NOT NULL,
                    after_quantity TEXT NOT NULL,
                    change_quantity TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_discrepancies (
                    discrepancy_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    serial TEXT,
                    kind TEXT NOT NULL,
                    book_quantity TEXT,
                    counted_quantity TEXT,
                    difference TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    note TEXT,
                    archived_by TEXT,
                    archived_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    serial TEXT,
                    note TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    barcode TEXT,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    device_id TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES inventory_tasks(task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_inventory_tasks_owner_phase
                    ON inventory_tasks(owner, phase);
                CREATE INDEX IF NOT EXISTS idx_inventory_items_status
                    ON inventory_items(task_id, status);
                CREATE INDEX IF NOT EXISTS idx_inventory_count_entries_item
                    ON inventory_count_entries(task_id, barcode, entry_id);
                CREATE INDEX IF NOT EXISTS idx_inventory_serial_scans_item
                    ON inventory_serial_scans(task_id, barcode, serial);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_serial_scans_active
                    ON inventory_serial_scans(task_id, barcode, serial)
                    WHERE active = 1;
                DROP INDEX IF EXISTS idx_inventory_serial_scans_task_active;
                CREATE TRIGGER IF NOT EXISTS inventory_serial_scans_task_active_insert
                    BEFORE INSERT ON inventory_serial_scans
                    WHEN NEW.active = 1
                     AND EXISTS (
                         SELECT 1 FROM inventory_serial_scans
                          WHERE task_id = NEW.task_id
                            AND serial = NEW.serial
                            AND active = 1
                     )
                    BEGIN
                        SELECT RAISE(ABORT, 'task_active_serial_duplicate');
                    END;
                CREATE TRIGGER IF NOT EXISTS inventory_serial_scans_task_active_update
                    BEFORE UPDATE OF task_id, serial, active ON inventory_serial_scans
                    WHEN NEW.active = 1
                     AND EXISTS (
                         SELECT 1 FROM inventory_serial_scans
                          WHERE task_id = NEW.task_id
                            AND serial = NEW.serial
                            AND active = 1
                            AND scan_id <> NEW.scan_id
                     )
                    BEGIN
                        SELECT RAISE(ABORT, 'task_active_serial_duplicate');
                    END;
                CREATE INDEX IF NOT EXISTS idx_inventory_discrepancies_status
                    ON inventory_discrepancies(status);
                """
            )
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(inventory_tasks)")
            }
            if "sync_resume_phase" not in task_columns:
                connection.execute(
                    "ALTER TABLE inventory_tasks ADD COLUMN sync_resume_phase TEXT"
                )
            if "last_sync_attempt_at" not in task_columns:
                connection.execute(
                    "ALTER TABLE inventory_tasks ADD COLUMN last_sync_attempt_at TEXT"
                )
            item_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(inventory_items)")
            }
            if "expected_current_quantity" not in item_columns:
                connection.execute(
                    "ALTER TABLE inventory_items ADD COLUMN expected_current_quantity TEXT"
                )
            if "serial_synced_at" not in item_columns:
                connection.execute(
                    "ALTER TABLE inventory_items ADD COLUMN serial_synced_at TEXT"
                )
            if "data_error" not in item_columns:
                connection.execute(
                    "ALTER TABLE inventory_items "
                    "ADD COLUMN data_error TEXT NOT NULL DEFAULT ''"
                )
            scan_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(inventory_serial_scans)"
                )
            }
            for name, definition in (
                ("source_classification", "TEXT NOT NULL DEFAULT ''"),
                ("lookup_barcode", "TEXT NOT NULL DEFAULT ''"),
                ("lookup_name", "TEXT NOT NULL DEFAULT ''"),
                ("warehouse", "TEXT NOT NULL DEFAULT ''"),
                ("is_checked_out", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in scan_columns:
                    connection.execute(
                        f"ALTER TABLE inventory_serial_scans ADD COLUMN {name} {definition}"
                    )
            if "carton_id" not in scan_columns:
                connection.execute(
                    "ALTER TABLE inventory_serial_scans ADD COLUMN carton_id INTEGER"
                )
            connection.execute(
                """INSERT INTO inventory_count_entries
                   (task_id, barcode, quantity, version,
                    created_by, created_device_id, created_at,
                    updated_by, updated_device_id, updated_at)
                   SELECT items.task_id, items.barcode,
                          items.completed_counted_quantity, 1,
                          'system:migration', 'system:migration',
                          COALESCE(items.completed_at, items.updated_at),
                          'system:migration', 'system:migration',
                          COALESCE(items.completed_at, items.updated_at)
                     FROM inventory_items AS items
                    WHERE items.completed_counted_quantity IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM inventory_count_entries AS entries
                           WHERE entries.task_id = items.task_id
                             AND entries.barcode = items.barcode
                      )"""
            )
            connection.execute("DELETE FROM inventory_item_locks")
            connection.commit()

    def _now_text(self):
        return self._timestamp_text(self.now())

    @staticmethod
    def _timestamp_text(value):
        return value.isoformat(timespec="microseconds" if value.microsecond else "seconds")

    @staticmethod
    def _row_dict(row):
        return dict(row) if row is not None else None

    @classmethod
    def _item_dict(cls, row):
        item = cls._row_dict(row)
        if item is None:
            return None
        item.update({
            "state": item["status"],
            "open_book_qty": item["book_quantity"],
            "completed_book_qty": item["completed_book_quantity"],
            "completed_actual_qty": item["completed_counted_quantity"],
            "latest_book_qty": item["latest_book_quantity"],
            "expected_current_qty": item["expected_current_quantity"],
            "diff_qty": item["difference"],
        })
        item["has_serial"] = (
            None if item.get("data_error") else bool(item["has_serial"])
        )
        return item

    @staticmethod
    def _count_entries(connection, task_id, barcode):
        return [dict(row) for row in connection.execute(
            """SELECT entry_id, quantity, version,
                      created_by, created_device_id, created_at,
                      updated_by, updated_device_id, updated_at
                 FROM inventory_count_entries
                WHERE task_id = ? AND barcode = ?
                ORDER BY entry_id""",
            (task_id, barcode),
        )]

    @classmethod
    def _item_with_counts(cls, connection, row):
        item = cls._item_dict(row)
        if item is None:
            return None
        item["serial_expected_count"] = (
            connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_expected "
                "WHERE task_id = ? AND barcode = ? AND sync_status = 'active'",
                (item["task_id"], item["barcode"]),
            ).fetchone()[0]
            if item.get("has_serial") and item.get("serial_synced_at") else 0
        )
        entries = cls._count_entries(
            connection, item["task_id"], item["barcode"]
        )
        quantities = [entry["quantity"] for entry in entries]
        if quantities:
            decimals = [Decimal(value) for value in quantities]
            precision = sum(len(value.as_tuple().digits) for value in decimals)
            with localcontext() as context:
                context.prec = max(precision + 2, 28)
                total = _decimal_text(sum(decimals, Decimal("0")))
        else:
            total = None
        item["count_entries"] = entries
        item["count_total"] = total
        item["count_expression"] = (
            "" if not quantities else
            quantities[0] if len(quantities) == 1 else
            " + ".join(quantities) + f" = {total}"
        )
        return item

    def create_task(self, owner, actor, catalog):
        for product in catalog:
            if (
                not isinstance(product, dict)
                or set(product) not in {
                    _NORMAL_CATALOG_KEYS, _ANOMALY_CATALOG_KEYS,
                }
                or (
                    set(product) == _ANOMALY_CATALOG_KEYS
                    and not str(product.get("data_error") or "").strip()
                )
            ):
                raise ValueError("catalog item must contain exactly the allowed keys")
        task_id = uuid.uuid4().hex
        timestamp = self._now_text()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """SELECT 1 FROM inventory_tasks
                   WHERE owner = ? AND phase IN ('loading', 'counting', 'serial_check', 'sync_error')
                   LIMIT 1""",
                (owner,),
            ).fetchone()
            if active:
                raise InventoryConflict("该用户已有进行中的盘点任务")
            connection.execute(
                """INSERT INTO inventory_tasks
                   (task_id, owner, created_by, phase, version, started_at)
                   VALUES (?, ?, ?, 'loading', 1, ?)""",
                (task_id, owner, actor, timestamp),
            )
            for product in catalog:
                connection.execute(
                    """INSERT INTO inventory_items
                       (task_id, barcode, name, spec, model, category, unit,
                        has_serial, data_error, initial_stock, book_quantity,
                        status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_id,
                        product["barcode"],
                        product["name"],
                        product["spec"],
                        product["model"],
                        product["category"],
                        product["unit"],
                        1 if product.get("has_serial") else 0,
                        str(product.get("data_error") or ""),
                        normalize_quantity(product["initial_stock"]),
                        normalize_quantity(product["initial_stock"]),
                        "data_error" if product.get("data_error") else "pending",
                        timestamp,
                    ),
                )
            connection.execute(
                """INSERT INTO inventory_audit_events
                   (task_id, event_type, actor, details, created_at)
                   VALUES (?, 'task_created', ?, ?, ?)""",
                (
                    task_id, actor,
                    "catalog_loaded;data_errors="
                    + str(sum(bool(row.get("data_error")) for row in catalog)),
                    timestamp,
                ),
            )
            connection.commit()
            return self._row_dict(connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
            ).fetchone())
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_active_task(self, owner):
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT * FROM inventory_tasks
                   WHERE owner = ? AND phase IN ('loading', 'counting', 'serial_check', 'sync_error')
                   ORDER BY started_at DESC LIMIT 1""",
                (owner,),
            ).fetchone()
        return self._row_dict(row)

    def get_task_snapshot(self, owner, task_id, known_version=None):
        connection = self.connect()
        try:
            task = connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ? AND owner = ?",
                (task_id, owner),
            ).fetchone()
            if task is None:
                raise InventoryNotFound("盘点任务不存在")
            if known_version is not None and int(known_version) == int(task["version"]):
                return {"success": True, "unchanged": True, "version": task["version"]}
            snapshot = self._row_dict(task)
            snapshot["items"] = [self._item_with_counts(connection, row) for row in connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? ORDER BY barcode",
                (task_id,),
            )]
            snapshot["success"] = True
            return snapshot
        finally:
            connection.close()

    def list_items(self, task_id, query="", state="", include_zero=False):
        query = str(query or "").strip()
        clauses = ["task_id = ?"]
        params = [task_id]
        if state:
            clauses.append("status = ?")
            params.append(state)
        if query:
            clauses.append("(LOWER(barcode) LIKE LOWER(?) OR LOWER(name) LIKE LOWER(?))")
            pattern = f"%{query}%"
            params.extend((pattern, pattern))
        elif not include_zero:
            clauses.append("initial_stock <> '0'")
        sql = "SELECT * FROM inventory_items WHERE " + " AND ".join(clauses) + " ORDER BY barcode"
        with closing(self.connect()) as connection:
            return [
                self._item_with_counts(connection, row)
                for row in connection.execute(sql, params)
            ]

    @staticmethod
    def _task_for_owner(connection, owner, task_id):
        task = connection.execute(
            "SELECT * FROM inventory_tasks WHERE task_id = ? AND owner = ?",
            (task_id, owner),
        ).fetchone()
        if task is None:
            raise InventoryNotFound("盘点任务不存在")
        return task

    @staticmethod
    def _audit(connection, task_id, event_type, actor, barcode=None, device_id=None, details=None, created_at=None):
        connection.execute(
            """INSERT INTO inventory_audit_events
               (task_id, barcode, event_type, actor, device_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, barcode, event_type, actor, device_id, details, created_at),
        )

    @staticmethod
    def _bump_version(connection, task_id):
        connection.execute(
            "UPDATE inventory_tasks SET version = version + 1 WHERE task_id = ?",
            (task_id,),
        )

    @staticmethod
    def _task_version(connection, task_id):
        row = connection.execute(
            "SELECT version FROM inventory_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise InventoryNotFound("盘点任务不存在")
        return int(row["version"])

    @classmethod
    def _assert_expected_version(cls, connection, task_id, expected_version):
        current_version = cls._task_version(connection, task_id)
        if expected_version is None:
            return current_version
        if int(expected_version) != current_version:
            raise InventoryVersionConflict(
                "盘点任务已被其他设备更新，请刷新后重试",
                current_version=current_version,
            )
        return current_version

    def get_task_version(self, owner, task_id):
        with closing(self.connect()) as connection:
            self._task_for_owner(connection, owner, task_id)
            return self._task_version(connection, task_id)

    def get_carton_preset(self, owner, task_id, barcode):
        with closing(self.connect()) as connection:
            self._task_for_owner(connection, owner, task_id)
            item = connection.execute(
                "SELECT 1 FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            return self._row_dict(connection.execute(
                "SELECT * FROM inventory_carton_presets WHERE barcode = ?",
                (barcode,),
            ).fetchone())

    def save_carton_preset(
        self, owner, task_id, barcode, device_id, actor, carton_quantity
    ):
        if (
            isinstance(carton_quantity, bool)
            or not isinstance(carton_quantity, int)
            or not 1 <= carton_quantity <= 999
        ):
            raise ValueError("每箱数量必须是1至999之间的整数")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._task_for_owner(connection, owner, task_id)
            item = connection.execute(
                "SELECT 1 FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            before = connection.execute(
                "SELECT carton_quantity FROM inventory_carton_presets WHERE barcode = ?",
                (barcode,),
            ).fetchone()
            timestamp = self._now_text()
            connection.execute(
                """INSERT INTO inventory_carton_presets
                   (barcode, carton_quantity, created_by, created_at,
                    updated_by, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(barcode) DO UPDATE SET
                       carton_quantity = excluded.carton_quantity,
                       updated_by = excluded.updated_by,
                       updated_at = excluded.updated_at""",
                (barcode, carton_quantity, actor, timestamp, actor, timestamp),
            )
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_carton_presets WHERE barcode = ?",
                (barcode,),
            ).fetchone())
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "carton_preset_changed", actor,
                barcode=barcode, device_id=device_id,
                details=json.dumps({
                    "before": before["carton_quantity"] if before else None,
                    "after": carton_quantity,
                }, ensure_ascii=False, sort_keys=True),
                created_at=timestamp,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _expire_locks(self, connection, task_id, now_text):
        expired = connection.execute(
            "SELECT barcode, device_id FROM inventory_item_locks "
            "WHERE task_id = ? AND expires_at <= ?",
            (task_id, now_text),
        ).fetchall()
        for lock in expired:
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, lock["barcode"]),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "lock_expired", "system",
                barcode=lock["barcode"], device_id=lock["device_id"],
                details="expires_at_reached", created_at=now_text,
            )
        return len(expired)

    def claim_item(
        self, task_id, barcode, device_id, actor, phase, *, expected_version=None
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_expected_version(connection, task_id, expected_version)
            now_dt = self.now()
            now = self._timestamp_text(now_dt)
            expires = self._timestamp_text(now_dt + timedelta(seconds=120))
            self._expire_locks(connection, task_id, now)
            item = connection.execute(
                "SELECT status FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            if item["status"] == "data_error":
                raise InventoryConflict("商品资料异常，不可盘点")
            existing = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if existing is not None and existing["device_id"] != device_id:
                self._audit(
                    connection, task_id, "lock_conflict", actor,
                    barcode=barcode, device_id=device_id,
                    details="owned_by=" + existing["device_id"], created_at=now,
                )
                connection.commit()
                raise InventoryConflict(
                    "商品已被其他设备锁定", lock_owner=existing["actor"]
                )
            if existing is None:
                connection.execute(
                    """INSERT INTO inventory_item_locks
                       (task_id, barcode, device_id, actor, phase, claimed_at, heartbeat_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, barcode, device_id, actor, phase, now, now, expires),
                )
                self._bump_version(connection, task_id)
                self._audit(connection, task_id, "lock_claimed", actor, barcode, device_id, phase, now)
            else:
                connection.execute(
                    """UPDATE inventory_item_locks
                       SET actor = ?, phase = ?, heartbeat_at = ?, expires_at = ?
                       WHERE task_id = ? AND barcode = ?""",
                    (actor, phase, now, expires, task_id, barcode),
                )
                self._audit(connection, task_id, "lock_heartbeat", actor, barcode, device_id, "reclaim", now)
            connection.commit()
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone())
            result["task_version"] = self._task_version(connection, task_id)
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat_lock(
        self, task_id, barcode, device_id, actor=None, *, owner=None,
        expected_version=None,
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if owner is not None:
                self._task_for_owner(connection, owner, task_id)
            self._assert_expected_version(connection, task_id, expected_version)
            now_dt = self.now()
            now = self._timestamp_text(now_dt)
            expires = self._timestamp_text(now_dt + timedelta(seconds=120))
            self._expire_locks(connection, task_id, now)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if lock is None or lock["device_id"] != device_id:
                self._audit(
                    connection, task_id, "lock_conflict", actor or "system",
                    barcode, device_id, "heartbeat_owner_mismatch", now,
                )
                connection.commit()
                raise InventoryConflict(
                    "设备未持有商品锁",
                    lock_owner=lock["actor"] if lock is not None else None,
                )
            connection.execute(
                "UPDATE inventory_item_locks SET heartbeat_at = ?, expires_at = ? WHERE task_id = ? AND barcode = ?",
                (now, expires, task_id, barcode),
            )
            self._audit(
                connection, task_id, "lock_heartbeat", actor or lock["actor"],
                barcode, device_id, "extension", now,
            )
            connection.commit()
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone())
            result["task_version"] = self._task_version(connection, task_id)
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def assert_item_lock(
        self, task_id, barcode, device_id, phase, actor=None, *, expected_version=None
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_expected_version(connection, task_id, expected_version)
            now = self._now_text()
            self._expire_locks(connection, task_id, now)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone()
            if lock is None or lock["device_id"] != device_id or lock["phase"] != phase:
                self._audit(
                    connection, task_id, "lock_conflict", actor or "system",
                    barcode, device_id, "assertion_failed", now,
                )
                connection.commit()
                raise InventoryConflict(
                    "设备未持有当前阶段的商品锁",
                    lock_owner=(
                        lock["actor"]
                        if lock is not None and lock["device_id"] != device_id
                        else None
                    ),
                )
            connection.commit()
            return self._task_version(connection, task_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release_expired_locks(self, task_id):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            count = self._expire_locks(connection, task_id, self._now_text())
            connection.commit()
            return count
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def admin_unlock(
        self, task_id, barcode, actor, is_admin, *, expected_version=None
    ):
        if is_admin is not True:
            raise InventoryPermissionDenied("只有管理员可以强制解锁")
        now = self._now_text()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_expected_version(connection, task_id, expected_version)
            lock = connection.execute(
                "SELECT device_id FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone()
            if lock is None:
                self._audit(connection, task_id, "lock_admin_unlocked", actor, barcode, details="no_lock", created_at=now)
                connection.commit()
                return {
                    "unlocked": False, "task_id": task_id, "barcode": barcode,
                    "task_version": self._task_version(connection, task_id),
                }
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            )
            self._bump_version(connection, task_id)
            self._audit(connection, task_id, "lock_admin_unlocked", actor, barcode, lock["device_id"], "manual_unlock", now)
            connection.commit()
            return {
                "unlocked": True, "task_id": task_id, "barcode": barcode,
                "task_version": self._task_version(connection, task_id),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _advance_count_phase(self, connection, task, timestamp):
        phase = task["phase"]
        next_phase = phase
        if phase == "loading":
            next_phase = "counting"
        elif phase == "counting":
            remaining = connection.execute(
                "SELECT COUNT(*) FROM inventory_items "
                "WHERE task_id = ? AND completed_counted_quantity IS NULL "
                "AND status <> 'data_error'",
                (task["task_id"],),
            ).fetchone()[0]
            serial_pending = connection.execute(
                "SELECT COUNT(*) FROM inventory_items "
                "WHERE task_id = ? AND status = 'serial_pending'",
                (task["task_id"],),
            ).fetchone()[0]
            if remaining == 0 and serial_pending:
                next_phase = "serial_check"
        if next_phase != phase:
            connection.execute(
                "UPDATE inventory_tasks SET phase = ? WHERE task_id = ?",
                (next_phase, task["task_id"]),
            )
            self._audit(
                connection, task["task_id"], "task_phase_changed", "system",
                details=f"{phase}->{next_phase}", created_at=timestamp,
            )
        return next_phase

    def advance_count_phase_if_ready(self, owner, task_id):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task = self._task_for_owner(connection, owner, task_id)
            next_phase = self._advance_count_phase(connection, task, timestamp)
            if next_phase != task["phase"]:
                self._bump_version(connection, task_id)
            connection.commit()
            return self._row_dict(connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
            ).fetchone())
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_open_book_quantity(
        self, owner, task_id, barcode, device_id, actor, book_quantity, *,
        expected_version=None,
    ):
        book_quantity = normalize_quantity(book_quantity)
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task = self._task_for_owner(connection, owner, task_id)
            self._assert_expected_version(connection, task_id, expected_version)
            if task["phase"] != "counting":
                raise InventoryConflict("当前任务不在数量盘点阶段")
            self._expire_locks(connection, task_id, timestamp)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if lock is None or lock["device_id"] != device_id or lock["phase"] != "counting":
                self._audit(
                    connection, task_id, "lock_conflict", actor, barcode, device_id,
                    "open_book_owner_mismatch", timestamp,
                )
                connection.commit()
                raise InventoryConflict(
                    "设备未持有数量盘点锁",
                    lock_owner=(
                        lock["actor"]
                        if lock is not None and lock["device_id"] != device_id
                        else None
                    ),
                )
            item = connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            if item["status"] == "data_error":
                raise InventoryConflict("商品资料异常，不可盘点")
            if item["completed_counted_quantity"] is not None:
                raise InventoryConflict("商品数量盘点已完成")
            connection.execute(
                "UPDATE inventory_items SET book_quantity = ?, updated_at = ? "
                "WHERE task_id = ? AND barcode = ?",
                (book_quantity, timestamp, task_id, barcode),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "count_item_opened", actor, barcode, device_id,
                details=book_quantity, created_at=timestamp,
            )
            connection.commit()
            result = self._item_dict(connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone())
            result["task_version"] = self._task_version(connection, task_id)
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_count(
        self, owner, task_id, barcode, device_id, actor, *,
        completed_book_qty, completed_actual_qty, diff_qty, state,
        expected_version=None,
    ):
        completed_book_qty = normalize_quantity(completed_book_qty)
        completed_actual_qty = normalize_quantity(completed_actual_qty)
        expected_difference = quantity_difference(
            completed_actual_qty, completed_book_qty
        )
        if diff_qty != expected_difference:
            raise ValueError("数量差异与账实数量不一致")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task = self._task_for_owner(connection, owner, task_id)
            self._assert_expected_version(connection, task_id, expected_version)
            if task["phase"] != "counting":
                raise InventoryConflict("当前任务不在数量盘点阶段")
            self._expire_locks(connection, task_id, timestamp)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if lock is None or lock["device_id"] != device_id or lock["phase"] != "counting":
                self._audit(
                    connection, task_id, "lock_conflict", actor, barcode, device_id,
                    "count_submit_owner_mismatch", timestamp,
                )
                connection.commit()
                raise InventoryConflict(
                    "设备未持有数量盘点锁",
                    lock_owner=(
                        lock["actor"]
                        if lock is not None and lock["device_id"] != device_id
                        else None
                    ),
                )
            item = connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            if item["status"] == "data_error":
                raise InventoryConflict("商品资料异常，不可盘点")
            expected_state = (
                "matched" if expected_difference == "0"
                else ("serial_pending" if item["has_serial"] else "variance")
            )
            if state != expected_state:
                raise ValueError("数量盘点状态与差异不一致")
            connection.execute(
                """UPDATE inventory_items
                   SET book_quantity = ?, counted_quantity = ?,
                       completed_book_quantity = ?, completed_counted_quantity = ?,
                       latest_book_quantity = ?, expected_current_quantity = ?,
                       difference = ?, status = ?, completed_at = ?, updated_at = ?
                   WHERE task_id = ? AND barcode = ?""",
                (
                    completed_book_qty, completed_actual_qty,
                    completed_book_qty, completed_actual_qty,
                    completed_book_qty, completed_actual_qty,
                    expected_difference, state, timestamp, timestamp,
                    task_id, barcode,
                ),
            )
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            )
            self._audit(
                connection, task_id, "count_recorded", actor, barcode, device_id,
                details=f"state={state};difference={expected_difference}",
                created_at=timestamp,
            )
            self._advance_count_phase(connection, task, timestamp)
            self._bump_version(connection, task_id)
            connection.commit()
            result = self._item_dict(connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone())
            result["task_version"] = self._task_version(connection, task_id)
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def _recalculate_item_from_entries(
        cls, connection, task, item, book_quantity, timestamp
    ):
        book_quantity = normalize_quantity(book_quantity)
        entries = cls._count_entries(
            connection, task["task_id"], item["barcode"]
        )
        if entries:
            values = [Decimal(entry["quantity"]) for entry in entries]
            precision = sum(len(value.as_tuple().digits) for value in values)
            with localcontext() as context:
                context.prec = max(precision + 2, 28)
                count_total = _decimal_text(sum(values, Decimal("0")))
            difference = quantity_difference(count_total, book_quantity)
            state = (
                "matched" if difference == "0"
                else "serial_pending" if item["has_serial"]
                else "variance"
            )
            completed_at = timestamp
        else:
            count_total = None
            difference = None
            state = "pending"
            completed_at = None
        if item["status"] == "serial_complete":
            connection.execute(
                """UPDATE inventory_serial_scans
                   SET active = 0
                   WHERE task_id = ? AND barcode = ? AND active = 1
                     AND source_classification = 'system_only'""",
                (task["task_id"], item["barcode"]),
            )
        connection.execute(
            """UPDATE inventory_items
               SET book_quantity = ?, counted_quantity = ?,
                   completed_book_quantity = ?, completed_counted_quantity = ?,
                   latest_book_quantity = ?, expected_current_quantity = ?,
                   difference = ?, status = ?, completed_at = ?, updated_at = ?
               WHERE task_id = ? AND barcode = ?""",
            (
                book_quantity, count_total,
                book_quantity if entries else None, count_total,
                book_quantity, count_total,
                difference, state, completed_at, timestamp,
                task["task_id"], item["barcode"],
            ),
        )

    @staticmethod
    def _count_entry_audit_details(entry_id, before, after):
        return json.dumps(
            {"entry_id": entry_id, "before": before, "after": after},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _count_mutation_context(self, connection, owner, task_id, barcode):
        task = self._task_for_owner(connection, owner, task_id)
        if task["phase"] not in {"counting", "serial_check"}:
            raise InventoryConflict("当前任务不能修改盘点数量")
        item = connection.execute(
            "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
            (task_id, barcode),
        ).fetchone()
        if item is None:
            raise InventoryNotFound("商品不存在")
        if item["status"] == "data_error":
            raise InventoryConflict("商品资料异常，不可盘点")
        return task, item

    def _count_mutation_result(self, connection, task_id, barcode):
        row = connection.execute(
            "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
            (task_id, barcode),
        ).fetchone()
        result = self._item_with_counts(connection, row)
        result["task_version"] = self._task_version(connection, task_id)
        return result

    def add_count_entry(
        self, owner, task_id, barcode, actor, device_id, quantity, book_quantity
    ):
        quantity = normalize_quantity(quantity)
        book_quantity = normalize_quantity(book_quantity)
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task, item = self._count_mutation_context(
                connection, owner, task_id, barcode
            )
            cursor = connection.execute(
                """INSERT INTO inventory_count_entries
                   (task_id, barcode, quantity, version,
                    created_by, created_device_id, created_at,
                    updated_by, updated_device_id, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, barcode, quantity,
                    actor, device_id, timestamp,
                    actor, device_id, timestamp,
                ),
            )
            entry_id = cursor.lastrowid
            after = dict(connection.execute(
                "SELECT * FROM inventory_count_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone())
            self._recalculate_item_from_entries(
                connection, task, item, book_quantity, timestamp
            )
            self._advance_count_phase(connection, task, timestamp)
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "count_entry_added", actor,
                barcode, device_id,
                self._count_entry_audit_details(entry_id, None, after),
                timestamp,
            )
            connection.commit()
            return self._count_mutation_result(connection, task_id, barcode)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_count_entry(
        self, owner, task_id, barcode, entry_id, entry_version,
        actor, device_id, quantity, book_quantity
    ):
        quantity = normalize_quantity(quantity)
        book_quantity = normalize_quantity(book_quantity)
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task, item = self._count_mutation_context(
                connection, owner, task_id, barcode
            )
            before_row = connection.execute(
                """SELECT * FROM inventory_count_entries
                   WHERE entry_id = ? AND task_id = ? AND barcode = ?""",
                (entry_id, task_id, barcode),
            ).fetchone()
            if before_row is None:
                raise InventoryNotFound("分次盘点记录不存在")
            if int(before_row["version"]) != int(entry_version):
                raise InventoryVersionConflict(
                    "该条数量已被其他设备修改，请刷新后重试",
                    current_version=self._task_version(connection, task_id),
                )
            before = dict(before_row)
            cursor = connection.execute(
                """UPDATE inventory_count_entries
                   SET quantity = ?, version = version + 1,
                       updated_by = ?, updated_device_id = ?, updated_at = ?
                   WHERE entry_id = ? AND task_id = ? AND barcode = ?
                     AND version = ?""",
                (
                    quantity, actor, device_id, timestamp,
                    entry_id, task_id, barcode, entry_version,
                ),
            )
            if cursor.rowcount != 1:
                raise InventoryVersionConflict(
                    "该条数量已被其他设备修改，请刷新后重试",
                    current_version=self._task_version(connection, task_id),
                )
            after = dict(connection.execute(
                "SELECT * FROM inventory_count_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone())
            self._recalculate_item_from_entries(
                connection, task, item, book_quantity, timestamp
            )
            self._advance_count_phase(connection, task, timestamp)
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "count_entry_updated", actor,
                barcode, device_id,
                self._count_entry_audit_details(entry_id, before, after),
                timestamp,
            )
            connection.commit()
            return self._count_mutation_result(connection, task_id, barcode)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_count_entry(
        self, owner, task_id, barcode, entry_id, entry_version,
        actor, device_id, book_quantity
    ):
        book_quantity = normalize_quantity(book_quantity)
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task, item = self._count_mutation_context(
                connection, owner, task_id, barcode
            )
            before_row = connection.execute(
                """SELECT * FROM inventory_count_entries
                   WHERE entry_id = ? AND task_id = ? AND barcode = ?""",
                (entry_id, task_id, barcode),
            ).fetchone()
            if before_row is None:
                raise InventoryNotFound("分次盘点记录不存在")
            if int(before_row["version"]) != int(entry_version):
                raise InventoryVersionConflict(
                    "该条数量已被其他设备修改，请刷新后重试",
                    current_version=self._task_version(connection, task_id),
                )
            before = dict(before_row)
            cursor = connection.execute(
                """DELETE FROM inventory_count_entries
                   WHERE entry_id = ? AND task_id = ? AND barcode = ?
                     AND version = ?""",
                (entry_id, task_id, barcode, entry_version),
            )
            if cursor.rowcount != 1:
                raise InventoryVersionConflict(
                    "该条数量已被其他设备修改，请刷新后重试",
                    current_version=self._task_version(connection, task_id),
                )
            self._recalculate_item_from_entries(
                connection, task, item, book_quantity, timestamp
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "count_entry_deleted", actor,
                barcode, device_id,
                self._count_entry_audit_details(entry_id, before, None),
                timestamp,
            )
            connection.commit()
            return self._count_mutation_result(connection, task_id, barcode)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_completed_stock(
        self, owner, task_id, stock_totals=None, *, synced_at=None, error=None
    ):
        timestamp = self._timestamp_text(synced_at or self.now())
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_for_owner(connection, owner, task_id)
            if error is not None:
                resume_phase = (
                    task["sync_resume_phase"] if task["phase"] == "sync_error"
                    else task["phase"]
                )
                connection.execute(
                    """UPDATE inventory_tasks
                       SET phase = ?, sync_resume_phase = NULL, gyj_status = ?,
                           last_sync_attempt_at = ?
                       WHERE task_id = ?""",
                    (resume_phase or "counting", str(error), timestamp, task_id),
                )
                self._bump_version(connection, task_id)
                self._audit(
                    connection, task_id, "completed_stock_sync_failed", "system",
                    details=str(error), created_at=timestamp,
                )
                connection.commit()
                return self._row_dict(connection.execute(
                    "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
                ).fetchone())

            if not isinstance(stock_totals, dict):
                raise ValueError("库存汇总结果格式不正确")
            items = connection.execute(
                "SELECT * FROM inventory_items "
                "WHERE task_id = ? AND completed_counted_quantity IS NOT NULL "
                "ORDER BY barcode",
                (task_id,),
            ).fetchall()
            prepared = []
            for item in items:
                barcode = item["barcode"]
                latest = normalize_quantity(stock_totals.get(barcode, "0"))
                completed_book = normalize_quantity(item["completed_book_quantity"])
                completed_actual = normalize_quantity(item["completed_counted_quantity"])
                expected_current = _expected_current_quantity(
                    completed_actual, latest, completed_book
                )
                previous = normalize_quantity(
                    item["latest_book_quantity"] or completed_book
                )
                prepared.append((item, latest, expected_current, previous))

            movement_count = 0
            for item, latest, expected_current, previous in prepared:
                if latest != previous:
                    connection.execute(
                        """INSERT INTO inventory_stock_movements
                           (task_id, barcode, before_quantity, after_quantity,
                            change_quantity, discovered_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            task_id, item["barcode"], previous, latest,
                            quantity_difference(latest, previous), timestamp,
                        ),
                    )
                    movement_count += 1
                connection.execute(
                    """UPDATE inventory_items
                       SET latest_book_quantity = ?, expected_current_quantity = ?,
                           updated_at = ?
                       WHERE task_id = ? AND barcode = ?""",
                    (latest, expected_current, timestamp, task_id, item["barcode"]),
                )

            recovered_phase = task["phase"]
            if task["phase"] == "sync_error":
                recovered_phase = task["sync_resume_phase"] or "counting"
            connection.execute(
                """UPDATE inventory_tasks
                   SET phase = ?, last_sync_at = ?, last_sync_attempt_at = ?,
                       gyj_status = 'synced', sync_resume_phase = NULL
                   WHERE task_id = ?""",
                (recovered_phase, timestamp, timestamp, task_id),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "completed_stock_synced", "system",
                details=f"movements={movement_count}", created_at=timestamp,
            )
            connection.commit()
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
            ).fetchone())
            result["items"] = [self._item_dict(row) for row in connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? ORDER BY barcode",
                (task_id,),
            )]
            result.update({"skipped": False, "movement_count": movement_count})
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _serial_mutation_context(
        self, connection, owner, task_id, barcode
    ):
        timestamp = self._now_text()
        task = self._task_for_owner(connection, owner, task_id)
        if task["phase"] not in {"counting", "serial_check"}:
            raise InventoryConflict("当前任务不能核对序列号")
        item = connection.execute(
            "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
            (task_id, barcode),
        ).fetchone()
        if item is None:
            raise InventoryNotFound("商品不存在")
        if item["status"] != "serial_pending":
            raise InventoryConflict("商品不在待核对序列号状态")
        return task, item, timestamp

    @staticmethod
    def _active_task_serial_scan(connection, task_id, serial):
        return connection.execute(
            """SELECT * FROM inventory_serial_scans
               WHERE task_id = ? AND serial = ? AND active = 1
               ORDER BY scan_id LIMIT 1""",
            (task_id, serial),
        ).fetchone()

    @staticmethod
    def _serial_scan_dict(row):
        result = dict(row)
        result["shipped"] = bool(result.pop("is_checked_out", 0))
        return result

    @staticmethod
    def _serial_audit_snapshot(row):
        if row is None:
            return None
        return {
            "serial": row["serial"],
            "classification": row["classification"],
            "lookup_barcode": row["lookup_barcode"],
            "lookup_name": row["lookup_name"],
            "warehouse": row["warehouse"],
            "shipped": bool(row["is_checked_out"]),
        }

    @classmethod
    def _serial_audit_details(cls, before, after):
        return json.dumps({
            "before": cls._serial_audit_snapshot(before),
            "after": cls._serial_audit_snapshot(after),
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _active_carton(connection, task_id, barcode, carton_id):
        carton = connection.execute(
            """SELECT * FROM inventory_cartons
               WHERE carton_id = ? AND task_id = ? AND barcode = ? AND active = 1""",
            (carton_id, task_id, barcode),
        ).fetchone()
        if carton is None:
            raise InventoryNotFound("箱组不存在或已变更，请刷新")
        return carton

    @staticmethod
    def _carton_scan_values(connection, task_id, barcode, serial):
        expected = connection.execute(
            """SELECT * FROM inventory_serial_expected
               WHERE task_id = ? AND barcode = ? AND serial = ?
                 AND sync_status = 'active'""",
            (task_id, barcode, serial),
        ).fetchone()
        if expected is None:
            return ("unknown", "unknown", "", "", "", 0)
        return (
            "matched", "matched", barcode, expected["name"],
            expected["warehouse"], 0,
        )

    def _insert_carton_scan(
        self, connection, task_id, barcode, carton_id, device_id, actor,
        serial, timestamp,
    ):
        existing = self._active_task_serial_scan(connection, task_id, serial)
        if existing is not None:
            raise InventoryConflict(f"任务内序列号重复：{serial}")
        values = self._carton_scan_values(connection, task_id, barcode, serial)
        cursor = connection.execute(
            """INSERT INTO inventory_serial_scans
               (task_id, barcode, serial, classification,
                source_classification, lookup_barcode, lookup_name,
                warehouse, is_checked_out, device_id, actor, scanned_at,
                active, carton_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (task_id, barcode, serial, *values, device_id, actor, timestamp,
             carton_id),
        )
        return connection.execute(
            "SELECT * FROM inventory_serial_scans WHERE scan_id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    @staticmethod
    def _carton_audit_details(
        carton, affected_count, serials, range_serials=None,
    ):
        serials = list(serials)
        range_source = range_serials if range_serials else serials
        range_serials = sorted(
            str(serial) for serial in range_source if str(serial)
        )
        return json.dumps({
            "carton_id": int(carton["carton_id"]),
            "confirmed_quantity": int(carton["confirmed_quantity"]),
            "affected_count": int(affected_count),
            "start_serial": range_serials[0] if range_serials else None,
            "end_serial": range_serials[-1] if range_serials else None,
            "serials": serials,
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _carton_active_serials(connection, task_id, barcode, carton_id):
        return [
            row["serial"] for row in connection.execute(
                """SELECT serial FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND carton_id = ?
                     AND active = 1
                   ORDER BY serial""",
                (task_id, barcode, carton_id),
            )
        ]

    def release_item_lock(
        self, owner, task_id, barcode, device_id, actor, phase, reason
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._task_for_owner(connection, owner, task_id)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if (
                lock is None
                or lock["device_id"] != device_id
                or lock["phase"] != phase
            ):
                connection.commit()
                return False
            timestamp = self._now_text()
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "lock_released", actor,
                barcode=barcode, device_id=device_id,
                details=reason, created_at=timestamp,
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_expected_serials(
        self, owner, task_id, barcode, device_id, actor, rows, *, synced_at=None,
        expected_version=None,
    ):
        if not isinstance(rows, list):
            raise ValueError("序列号库存结果格式不正确")
        prepared = []
        seen = set()
        allowed_keys = {"serial", "barcode", "name", "warehouse", "shipped"}
        for row in rows:
            if not isinstance(row, dict) or set(row) != allowed_keys:
                raise ValueError("序列号库存字段不正确")
            serial = str(row["serial"] or "").strip()
            row_barcode = str(row["barcode"] or "").strip()
            if not serial or row_barcode != barcode or row["shipped"] is not False:
                raise ValueError("序列号库存内容不正确")
            if serial in seen:
                raise ValueError("序列号库存存在重复")
            seen.add(serial)
            prepared.append((
                serial,
                str(row["name"] or ""),
                str(row["warehouse"] or ""),
            ))

        timestamp = self._timestamp_text(synced_at or self.now())
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            scans_before = {
                row["scan_id"]: row for row in connection.execute(
                    """SELECT * FROM inventory_serial_scans
                       WHERE task_id = ? AND barcode = ? AND active = 1""",
                    (task_id, barcode),
                ).fetchall()
            }
            connection.execute(
                "UPDATE inventory_serial_expected SET sync_status = 'inactive' "
                "WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            )
            for serial, name, warehouse in prepared:
                connection.execute(
                    """INSERT INTO inventory_serial_expected
                       (task_id, barcode, serial, name, warehouse,
                        is_checked_out, sync_status, synced_at)
                       VALUES (?, ?, ?, ?, ?, 0, 'active', ?)
                       ON CONFLICT(task_id, barcode, serial) DO UPDATE SET
                           name = excluded.name,
                           warehouse = excluded.warehouse,
                           is_checked_out = 0,
                           sync_status = 'active',
                           synced_at = excluded.synced_at""",
                    (task_id, barcode, serial, name, warehouse, timestamp),
                )
            connection.execute(
                """UPDATE inventory_serial_scans AS scans
                   SET classification = CASE
                       WHEN EXISTS (
                           SELECT 1 FROM inventory_serial_expected AS expected
                           WHERE expected.task_id = scans.task_id
                             AND expected.barcode = scans.barcode
                             AND expected.serial = scans.serial
                             AND expected.sync_status = 'active'
                       ) THEN 'matched'
                       WHEN scans.source_classification = 'matched' THEN 'unknown'
                       ELSE scans.source_classification
                   END
                   WHERE scans.task_id = ? AND scans.barcode = ? AND scans.active = 1""",
                (task_id, barcode),
            )
            connection.execute(
                "UPDATE inventory_items SET serial_synced_at = ?, updated_at = ? "
                "WHERE task_id = ? AND barcode = ?",
                (timestamp, timestamp, task_id, barcode),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "serial_expected_refreshed", actor,
                barcode=barcode, device_id=device_id,
                details=f"expected={len(prepared)}", created_at=timestamp,
            )
            for after in connection.execute(
                """SELECT * FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND active = 1
                   ORDER BY scan_id""",
                (task_id, barcode),
            ).fetchall():
                before = scans_before.get(after["scan_id"])
                if before is not None and before["classification"] != after["classification"]:
                    self._audit(
                        connection, task_id, "serial_scan_reclassified", actor,
                        barcode=barcode, device_id=device_id,
                        details=self._serial_audit_details(before, after),
                        created_at=timestamp,
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def add_serial_scan(
        self, owner, task_id, barcode, device_id, actor, serial,
        classification, lookup=None, *, expected_version=None,
    ):
        serial = str(serial or "").strip()
        if not serial:
            raise ValueError("序列号不能为空")
        if classification not in {"matched", "other_product", "already_shipped", "unknown"}:
            raise ValueError("序列号分类不正确")
        lookup = lookup or {}
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, _, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            existing = self._active_task_serial_scan(
                connection, task_id, serial
            )
            if existing is not None:
                self._audit(
                    connection, task_id, "serial_scan_duplicate", actor,
                    barcode=barcode, device_id=device_id,
                    details=serial, created_at=timestamp,
                )
                connection.commit()
                result = self._serial_scan_dict(existing)
                result["classification"] = "duplicate"
                result["task_version"] = self._task_version(
                    connection, task_id
                )
                return result

            expected = connection.execute(
                """SELECT * FROM inventory_serial_expected
                   WHERE task_id = ? AND barcode = ? AND serial = ?
                     AND sync_status = 'active'""",
                (task_id, barcode, serial),
            ).fetchone()
            if expected is not None:
                classification = "matched"
                source_classification = "matched"
                lookup_barcode = barcode
                lookup_name = expected["name"]
                warehouse = expected["warehouse"]
                is_checked_out = 0
            else:
                if classification == "matched":
                    raise ValueError("序列号不在当前商品账面集合中")
                source_classification = classification
                lookup_barcode = str(lookup.get("barcode") or "")
                lookup_name = str(lookup.get("name") or "")
                warehouse = str(lookup.get("warehouse") or "")
                is_checked_out = 1 if lookup.get("shipped") is True else 0
            cursor = connection.execute(
                """INSERT INTO inventory_serial_scans
                   (task_id, barcode, serial, classification,
                    source_classification, lookup_barcode, lookup_name,
                    warehouse, is_checked_out, device_id, actor, scanned_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    task_id, barcode, serial, classification,
                    source_classification, lookup_barcode, lookup_name,
                    warehouse, is_checked_out, device_id, actor, timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM inventory_serial_scans WHERE scan_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "serial_scanned", actor,
                barcode=barcode, device_id=device_id,
                details=self._serial_audit_details(None, row),
                created_at=timestamp,
            )
            connection.commit()
            result = self._serial_scan_dict(row)
            result["task_version"] = self._task_version(connection, task_id)
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def remove_serial_scan(
        self, owner, task_id, barcode, device_id, actor, serial, *,
        expected_version=None,
    ):
        serial = str(serial or "").strip()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, _, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            scan = connection.execute(
                """SELECT * FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND serial = ? AND active = 1""",
                (task_id, barcode, serial),
            ).fetchone()
            if scan is None:
                raise InventoryNotFound("扫描序列号不存在")
            connection.execute(
                "UPDATE inventory_serial_scans SET active = 0 WHERE scan_id = ?",
                (scan["scan_id"],),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "serial_scan_removed", actor,
                barcode=barcode, device_id=device_id,
                details=self._serial_audit_details(scan, None),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        result = self.serial_reconciliation(owner, task_id, barcode)
        result["removed"] = serial
        return result

    def create_serial_carton(
        self, owner, task_id, barcode, device_id, actor, preset_quantity,
        start_serial, serials,
    ):
        start_serial = str(start_serial or "").strip()
        if not start_serial:
            raise ValueError("起始序列号不能为空")
        if (
            isinstance(preset_quantity, bool)
            or not isinstance(preset_quantity, int)
            or not 1 <= preset_quantity <= 999
        ):
            raise ValueError("每箱数量必须是1至999之间的整数")
        if not isinstance(serials, list) or not serials:
            raise ValueError("本箱序列号不能为空")
        prepared = [str(serial or "").strip() for serial in serials]
        if any(not serial for serial in prepared):
            raise ValueError("序列号不能为空")
        if len(set(prepared)) != len(prepared):
            raise ValueError("本箱序列号存在重复")

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, item, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            if not item["serial_synced_at"]:
                raise InventoryConflict("请先获取账面序列号后再录入整箱")
            for serial in prepared:
                if self._active_task_serial_scan(connection, task_id, serial) is not None:
                    raise InventoryConflict(f"任务内序列号重复：{serial}")
            cursor = connection.execute(
                """INSERT INTO inventory_cartons
                   (task_id, barcode, preset_quantity, confirmed_quantity,
                    start_serial, created_by,
                    created_device_id, created_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    task_id, barcode, preset_quantity, len(prepared),
                    start_serial, actor, device_id, timestamp,
                ),
            )
            carton_id = cursor.lastrowid
            for serial in prepared:
                self._insert_carton_scan(
                    connection, task_id, barcode, carton_id, device_id, actor,
                    serial, timestamp,
                )
            carton = connection.execute(
                "SELECT * FROM inventory_cartons WHERE carton_id = ?",
                (carton_id,),
            ).fetchone()
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "carton_created", actor,
                barcode=barcode, device_id=device_id,
                details=self._carton_audit_details(
                    carton, len(prepared), prepared,
                ),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def add_carton_serial(
        self, owner, task_id, barcode, device_id, actor, carton_id, serial,
    ):
        serial = str(serial or "").strip()
        if not serial:
            raise ValueError("序列号不能为空")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, item, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            if not item["serial_synced_at"]:
                raise InventoryConflict("请先获取账面序列号后再补录")
            carton = self._active_carton(
                connection, task_id, barcode, carton_id,
            )
            self._insert_carton_scan(
                connection, task_id, barcode, carton_id, device_id, actor,
                serial, timestamp,
            )
            range_serials = self._carton_active_serials(
                connection, task_id, barcode, carton_id,
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "carton_serial_added", actor,
                barcode=barcode, device_id=device_id,
                details=self._carton_audit_details(
                    carton, 1, [serial], range_serials,
                ),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def remove_carton_serial(
        self, owner, task_id, barcode, device_id, actor, carton_id, serial,
    ):
        serial = str(serial or "").strip()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, _, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            carton = self._active_carton(
                connection, task_id, barcode, carton_id,
            )
            scan = connection.execute(
                """SELECT * FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND carton_id = ?
                     AND serial = ? AND active = 1""",
                (task_id, barcode, carton_id, serial),
            ).fetchone()
            if scan is None:
                raise InventoryNotFound("箱内序列号不存在或已变更，请刷新")
            connection.execute(
                "UPDATE inventory_serial_scans SET active = 0 WHERE scan_id = ?",
                (scan["scan_id"],),
            )
            range_serials = self._carton_active_serials(
                connection, task_id, barcode, carton_id,
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "carton_serial_removed", actor,
                barcode=barcode, device_id=device_id,
                details=self._carton_audit_details(
                    carton, 1, [serial], range_serials,
                ),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def delete_serial_carton(
        self, owner, task_id, barcode, device_id, actor, carton_id,
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, _, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            carton = self._active_carton(
                connection, task_id, barcode, carton_id,
            )
            scans = connection.execute(
                """SELECT * FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND carton_id = ?
                     AND active = 1
                   ORDER BY scan_id""",
                (task_id, barcode, carton_id),
            ).fetchall()
            connection.execute(
                """UPDATE inventory_serial_scans SET active = 0
                   WHERE task_id = ? AND barcode = ? AND carton_id = ?
                     AND active = 1""",
                (task_id, barcode, carton_id),
            )
            connection.execute(
                """UPDATE inventory_cartons
                   SET active = 0, deleted_by = ?, deleted_device_id = ?,
                       deleted_at = ?
                   WHERE carton_id = ? AND task_id = ? AND barcode = ?
                     AND active = 1""",
                (actor, device_id, timestamp, carton_id, task_id, barcode),
            )
            serials = [scan["serial"] for scan in scans]
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "carton_deleted", actor,
                barcode=barcode, device_id=device_id,
                details=self._carton_audit_details(
                    carton, len(serials), serials,
                ),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def serial_reconciliation(self, owner, task_id, barcode):
        connection = self.connect()
        try:
            task = self._task_for_owner(connection, owner, task_id)
            item = connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            expected_rows = [dict(row) for row in connection.execute(
                """SELECT serial, barcode, name, warehouse, is_checked_out,
                          sync_status, synced_at
                   FROM inventory_serial_expected
                   WHERE task_id = ? AND barcode = ? AND sync_status = 'active'
                   ORDER BY serial""",
                (task_id, barcode),
            )]
            active_scans = [self._serial_scan_dict(row) for row in connection.execute(
                """SELECT * FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND active = 1
                   ORDER BY scan_id DESC""",
                (task_id, barcode),
            )]
            cartons = [dict(row) for row in connection.execute(
                """SELECT * FROM inventory_cartons
                   WHERE task_id = ? AND barcode = ? AND active = 1
                   ORDER BY carton_id DESC""",
                (task_id, barcode),
            )]
            for carton in cartons:
                carton["active"] = bool(carton["active"])
                carton["scans"] = [
                    row for row in active_scans
                    if row["carton_id"] == carton["carton_id"]
                ]
            matched_serials = {
                row["serial"] for row in active_scans
                if row["classification"] == "matched"
            }
            system_only = []
            for row in expected_rows:
                row["shipped"] = bool(row.pop("is_checked_out"))
                row["classification"] = "system_only"
                if row["serial"] not in matched_serials:
                    system_only.append(row)
            public_item = self._item_dict(item)
            public_item["serial_expected_count"] = len(expected_rows)
            result = {
                "task_id": task_id,
                "barcode": barcode,
                "phase": task["phase"],
                "version": task["version"],
                "item": public_item,
                "carton_preset": self._row_dict(connection.execute(
                    "SELECT * FROM inventory_carton_presets WHERE barcode = ?",
                    (barcode,),
                ).fetchone()),
                "cartons": cartons,
                "ungrouped": [
                    row for row in active_scans if row["carton_id"] is None
                ],
                "expected": expected_rows,
                "matched": [
                    row for row in active_scans
                    if row["classification"] == "matched"
                ],
                "system_only": [
                    row for row in active_scans
                    if row["classification"] == "system_only"
                ] or system_only,
                "physical_only": [
                    row for row in active_scans
                    if row["classification"] in {"unknown", "already_shipped"}
                ],
                "other_product": [
                    row for row in active_scans
                    if row["classification"] == "other_product"
                ],
                "duplicates": [],
            }
            result["counts"] = {
                key: len(result[key])
                for key in (
                    "matched", "system_only", "physical_only",
                    "other_product", "duplicates",
                )
            }
            return result
        finally:
            connection.close()

    def reopen_serial_item(self, owner, task_id, barcode, device_id, actor):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = self._now_text()
            task = self._task_for_owner(connection, owner, task_id)
            if task["phase"] not in {"counting", "serial_check"}:
                raise InventoryConflict("当前任务不能核对序列号")
            item = connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
            if item["status"] == "serial_pending":
                connection.rollback()
                return self.serial_reconciliation(owner, task_id, barcode)
            if item["status"] != "serial_complete":
                raise InventoryConflict("商品不能重新核对序列号")
            connection.execute(
                """UPDATE inventory_serial_scans
                   SET active = 0
                   WHERE task_id = ? AND barcode = ? AND active = 1
                     AND source_classification = 'system_only'""",
                (task_id, barcode),
            )
            connection.execute(
                """UPDATE inventory_items
                   SET status = 'serial_pending', completed_at = NULL, updated_at = ?
                   WHERE task_id = ? AND barcode = ?""",
                (timestamp, task_id, barcode),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "serial_item_reopened", actor,
                barcode=barcode, device_id=device_id, created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    def complete_serial_item(
        self, owner, task_id, barcode, device_id, actor, *,
        expected_version=None,
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _, item, timestamp = self._serial_mutation_context(
                connection, owner, task_id, barcode,
            )
            connection.execute(
                """UPDATE inventory_serial_scans AS scans
                   SET classification = CASE
                       WHEN EXISTS (
                           SELECT 1 FROM inventory_serial_expected AS expected
                           WHERE expected.task_id = scans.task_id
                             AND expected.barcode = scans.barcode
                             AND expected.serial = scans.serial
                             AND expected.sync_status = 'active'
                       ) THEN 'matched'
                       WHEN scans.source_classification = 'matched' THEN 'unknown'
                       ELSE scans.source_classification
                   END
                   WHERE scans.task_id = ? AND scans.barcode = ? AND scans.active = 1""",
                (task_id, barcode),
            )
            missing = connection.execute(
                """SELECT expected.* FROM inventory_serial_expected AS expected
                   WHERE expected.task_id = ? AND expected.barcode = ?
                     AND expected.sync_status = 'active'
                     AND NOT EXISTS (
                         SELECT 1 FROM inventory_serial_scans AS scans
                         WHERE scans.task_id = expected.task_id
                           AND scans.barcode = expected.barcode
                           AND scans.serial = expected.serial
                           AND scans.classification = 'matched'
                           AND scans.active = 1
                     )
                   ORDER BY expected.serial""",
                (task_id, barcode),
            ).fetchall()
            for expected in missing:
                if self._active_task_serial_scan(
                    connection, task_id, expected["serial"]
                ) is not None:
                    raise InventoryConflict(
                        f"任务内序列号重复：{expected['serial']}"
                    )
                connection.execute(
                    """INSERT INTO inventory_serial_scans
                       (task_id, barcode, serial, classification,
                        source_classification, lookup_barcode, lookup_name,
                        warehouse, is_checked_out, device_id, actor, scanned_at, active)
                       VALUES (?, ?, ?, 'system_only', 'system_only', ?, ?, ?, 0,
                               ?, ?, ?, 1)""",
                    (
                        task_id, barcode, expected["serial"], barcode,
                        expected["name"], expected["warehouse"],
                        device_id, actor, timestamp,
                    ),
                )
            physical_count = connection.execute(
                """SELECT COUNT(*) FROM inventory_serial_scans
                   WHERE task_id = ? AND barcode = ? AND active = 1
                     AND classification IN (
                         'matched', 'unknown', 'already_shipped', 'physical_only'
                     )""",
                (task_id, barcode),
            ).fetchone()[0]
            book_quantity = normalize_quantity(
                item["latest_book_quantity"] or item["completed_book_quantity"]
            )
            actual_quantity = normalize_quantity(physical_count)
            difference = quantity_difference(actual_quantity, book_quantity)
            connection.execute(
                """UPDATE inventory_items
                   SET counted_quantity = ?, completed_counted_quantity = ?,
                       expected_current_quantity = ?, difference = ?,
                       status = 'serial_complete', completed_at = ?, updated_at = ?
                   WHERE task_id = ? AND barcode = ?""",
                (
                    actual_quantity, actual_quantity, actual_quantity, difference,
                    timestamp, timestamp, task_id, barcode,
                ),
            )
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "serial_item_completed", actor,
                barcode=barcode, device_id=device_id,
                details=(
                    f"system_only={len(missing)};actual_quantity={actual_quantity}"
                ),
                created_at=timestamp,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.serial_reconciliation(owner, task_id, barcode)

    @classmethod
    def _discrepancy_dict(cls, connection, row):
        result = dict(row)
        result["has_serial"] = (
            None if result.get("data_error") else bool(result["has_serial"])
        )
        result["shipped"] = bool(result.pop("is_checked_out", 0))
        notes = [dict(note) for note in connection.execute(
            """SELECT note_id AS id, task_id, barcode, serial, note, actor,
                      created_at
                 FROM inventory_notes
                WHERE task_id = ? AND barcode = ? AND serial IS ?
                ORDER BY note_id""",
            (result["task_id"], result["barcode"], result["serial"]),
        )]
        result["notes"] = notes
        result["note"] = notes[-1]["note"] if notes else None
        return result

    @classmethod
    def _discrepancy_for_owner(cls, connection, owner, discrepancy_id):
        row = connection.execute(
            _DISCREPANCY_SELECT
            + " WHERE tasks.owner = ? AND discrepancies.discrepancy_id = ?",
            (owner, discrepancy_id),
        ).fetchone()
        if row is None:
            raise InventoryNotFound("差异记录不存在")
        return cls._discrepancy_dict(connection, row)

    def complete_task(
        self, owner, task_id, actor, *, allow_unverified_serials=False,
        expected_version=None,
    ):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_for_owner(connection, owner, task_id)
            self._assert_expected_version(connection, task_id, expected_version)
            counts = connection.execute(
                """SELECT
                       SUM(CASE WHEN completed_counted_quantity IS NOT NULL
                                AND status <> 'data_error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN completed_counted_quantity IS NULL
                                AND status <> 'data_error' THEN 1 ELSE 0 END),
                       SUM(CASE WHEN status = 'serial_pending' THEN 1 ELSE 0 END)
                     FROM inventory_items WHERE task_id = ?""",
                (task_id,),
            ).fetchone()
            counted_items = int(counts[0] or 0)
            uncounted_items = int(counts[1] or 0)
            unverified_serial_items = int(counts[2] or 0)
            if task["phase"] == "completed":
                connection.commit()
                result = self._row_dict(task)
                result["completed"] = True
                result["counted_items"] = counted_items
                result["uncounted_items"] = uncounted_items
                result["unverified_serial_items"] = unverified_serial_items
                return result
            if task["phase"] not in {"counting", "serial_check"}:
                raise InventoryConflict("当前任务不能完成")
            if unverified_serial_items and not allow_unverified_serials:
                raise InventoryConfirmationRequired(
                    f"仍有 {unverified_serial_items} 个商品未核对序列号",
                    pending_serial_count=unverified_serial_items,
                )

            timestamp = self._now_text()
            discrepancy_count = 0
            items = connection.execute(
                """SELECT * FROM inventory_items
                   WHERE task_id = ?
                     AND completed_counted_quantity IS NOT NULL
                   ORDER BY barcode""",
                (task_id,),
            ).fetchall()
            for item in items:
                if item["status"] == "data_error":
                    continue
                if item["difference"] != "0":
                    connection.execute(
                        """INSERT INTO inventory_discrepancies
                           (task_id, barcode, serial, kind, book_quantity,
                            counted_quantity, difference, status, created_at)
                           VALUES (?, ?, NULL, 'product_quantity', ?, ?, ?,
                                   'open', ?)""",
                        (
                            task_id, item["barcode"],
                            item["completed_book_quantity"],
                            item["completed_counted_quantity"],
                            item["difference"], timestamp,
                        ),
                    )
                    discrepancy_count += 1
                if item["status"] == "serial_pending":
                    connection.execute(
                        """INSERT INTO inventory_discrepancies
                           (task_id, barcode, serial, kind, book_quantity,
                            counted_quantity, difference, status, created_at)
                           VALUES (?, ?, NULL, 'serial_unverified', ?, ?, ?,
                                   'open', ?)""",
                        (
                            task_id, item["barcode"],
                            item["completed_book_quantity"],
                            item["completed_counted_quantity"],
                            item["difference"], timestamp,
                        ),
                    )
                    discrepancy_count += 1

            scans = connection.execute(
                """SELECT scans.barcode, scans.serial, scans.classification
                   FROM inventory_serial_scans AS scans
                   JOIN inventory_items AS items
                     ON items.task_id = scans.task_id
                    AND items.barcode = scans.barcode
                   WHERE scans.task_id = ? AND scans.active = 1
                     AND items.status = 'serial_complete'
                   ORDER BY scans.scan_id""",
                (task_id,),
            ).fetchall()
            for scan in scans:
                if scan["classification"] == "matched":
                    continue
                kind = _SERIAL_DISCREPANCY_KINDS.get(scan["classification"])
                if kind is None:
                    raise InventoryConflict("存在未完成的序列号分类")
                connection.execute(
                    """INSERT INTO inventory_discrepancies
                       (task_id, barcode, serial, kind, status, created_at)
                       VALUES (?, ?, ?, ?, 'open', ?)""",
                    (
                        task_id, scan["barcode"], scan["serial"], kind,
                        timestamp,
                    ),
                )
                discrepancy_count += 1

            connection.execute(
                """UPDATE inventory_tasks
                   SET phase = 'completed', completed_at = ?
                   WHERE task_id = ?""",
                (timestamp, task_id),
            )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "task_completed", actor,
                details=json.dumps({
                    "allow_unverified_serials": bool(allow_unverified_serials),
                    "counted_items": counted_items,
                    "discrepancies": discrepancy_count,
                    "uncounted_items": uncounted_items,
                    "unverified_serial_items": unverified_serial_items,
                }, ensure_ascii=False, separators=(",", ":")),
                created_at=timestamp,
            )
            connection.commit()
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
            ).fetchone())
            result["completed"] = True
            result["counted_items"] = counted_items
            result["uncounted_items"] = uncounted_items
            result["unverified_serial_items"] = unverified_serial_items
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reopen_task(
        self, owner, task_id, actor, stock_totals, *, synced_at=None
    ):
        if not isinstance(stock_totals, dict):
            raise ValueError("GYJ 库存汇总结果格式不正确")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_for_owner(connection, owner, task_id)
            if task["phase"] != "completed":
                raise InventoryConflict("只能继续已完成的盘点任务")
            active = connection.execute(
                """SELECT 1 FROM inventory_tasks
                   WHERE owner = ? AND task_id <> ?
                     AND phase IN ('loading', 'counting', 'serial_check', 'sync_error')
                   LIMIT 1""",
                (owner, task_id),
            ).fetchone()
            if active:
                raise InventoryConflict("请先完成当前任务，再继续历史盘点")

            timestamp = self._timestamp_text(synced_at or self.now())
            items = connection.execute(
                "SELECT * FROM inventory_items WHERE task_id = ? ORDER BY barcode",
                (task_id,),
            ).fetchall()
            normalized_totals = {}
            missing_barcodes = []
            for item in items:
                if item["status"] == "data_error":
                    continue
                if item["barcode"] not in stock_totals:
                    missing_barcodes.append(item["barcode"])
                    normalized_totals[item["barcode"]] = "0"
                else:
                    normalized_totals[item["barcode"]] = normalize_quantity(
                        stock_totals[item["barcode"]]
                    )

            connection.execute(
                """INSERT INTO inventory_count_entries
                   (task_id, barcode, quantity, version,
                    created_by, created_device_id, created_at,
                    updated_by, updated_device_id, updated_at)
                   SELECT items.task_id, items.barcode,
                          items.completed_counted_quantity, 1,
                          'system:migration', 'system:migration',
                          COALESCE(items.completed_at, items.updated_at),
                          'system:migration', 'system:migration',
                          COALESCE(items.completed_at, items.updated_at)
                     FROM inventory_items AS items
                    WHERE items.task_id = ?
                      AND items.completed_counted_quantity IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM inventory_count_entries AS entries
                           WHERE entries.task_id = items.task_id
                             AND entries.barcode = items.barcode
                      )""",
                (task_id,),
            )
            for item in items:
                if (
                    item["status"] == "data_error"
                    or item["completed_counted_quantity"] is None
                ):
                    continue
                completed_book = normalize_quantity(
                    item["completed_book_quantity"] or item["book_quantity"]
                )
                completed_actual = normalize_quantity(
                    item["completed_counted_quantity"]
                )
                adjusted_actual = _expected_current_quantity(
                    completed_actual,
                    normalized_totals[item["barcode"]],
                    completed_book,
                )
                if Decimal(adjusted_actual) < 0:
                    adjusted_actual = "0"
                if adjusted_actual == completed_actual:
                    continue
                connection.execute(
                    "DELETE FROM inventory_count_entries "
                    "WHERE task_id = ? AND barcode = ?",
                    (task_id, item["barcode"]),
                )
                connection.execute(
                    """INSERT INTO inventory_count_entries
                       (task_id, barcode, quantity, version,
                        created_by, created_device_id, created_at,
                        updated_by, updated_device_id, updated_at)
                       VALUES (?, ?, ?, 1, 'system:stock-movement',
                               'task-reopen', ?, 'system:stock-movement',
                               'task-reopen', ?)""",
                    (
                        task_id, item["barcode"], adjusted_actual,
                        timestamp, timestamp,
                    ),
                )
                self._audit(
                    connection, task_id,
                    "count_adjusted_for_stock_movement", "system",
                    barcode=item["barcode"], device_id="task-reopen",
                    details=json.dumps(
                        {
                            "before": {"quantity": completed_actual},
                            "after": {"quantity": adjusted_actual},
                            "book_before": completed_book,
                            "book_after": normalized_totals[item["barcode"]],
                        },
                        ensure_ascii=False, separators=(",", ":"),
                    ),
                    created_at=timestamp,
                )
            connection.execute(
                """UPDATE inventory_tasks
                   SET phase = 'counting', completed_at = NULL,
                       gyj_status = 'synced', sync_resume_phase = NULL,
                       last_sync_at = ?
                   WHERE task_id = ?""",
                (timestamp, task_id),
            )
            connection.execute(
                "DELETE FROM inventory_discrepancies WHERE task_id = ?",
                (task_id,),
            )
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ?",
                (task_id,),
            )
            for item in items:
                if item["status"] == "data_error":
                    continue
                self._recalculate_item_from_entries(
                    connection, task, item,
                    normalized_totals[item["barcode"]], timestamp,
                )
            self._bump_version(connection, task_id)
            self._audit(
                connection, task_id, "task_reopened", actor,
                details=json.dumps(
                    {"from_phase": "completed", "to_phase": "counting"},
                    ensure_ascii=False, separators=(",", ":"),
                ),
                created_at=timestamp,
            )
            connection.commit()
            result = self._row_dict(connection.execute(
                "SELECT * FROM inventory_tasks WHERE task_id = ?", (task_id,)
            ).fetchone())
            result["missing_barcodes"] = missing_barcodes
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_completed_task(self, owner, task_id):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_for_owner(connection, owner, task_id)
            if task["phase"] != "completed":
                raise InventoryConflict("只能删除已完成的盘点任务")
            connection.execute(
                "DELETE FROM inventory_tasks WHERE task_id = ?", (task_id,)
            )
            connection.commit()
            return {"task_id": task_id}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_task_history(self, owner, limit=20, offset=0):
        """Return one completed-task page with server-side summary counts.

        ``participant_count`` is the number of distinct non-empty device IDs
        recorded in the task audit trail.  For legacy tasks without any such
        device record, a non-empty task creator counts as one participant.
        """
        limit = int(limit)
        offset = int(offset)
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError("历史任务分页参数不正确")
        with closing(self.connect()) as connection:
            total = connection.execute(
                """SELECT COUNT(*) FROM inventory_tasks
                   WHERE owner = ? AND phase = 'completed'""",
                (owner,),
            ).fetchone()[0]
            rows = connection.execute(
                """WITH history_page AS (
                       SELECT tasks.*, tasks.rowid AS history_rowid
                       FROM inventory_tasks AS tasks
                       WHERE tasks.owner = ? AND tasks.phase = 'completed'
                       ORDER BY tasks.completed_at DESC, tasks.rowid DESC
                       LIMIT ? OFFSET ?
                   ), item_counts AS (
                       SELECT items.task_id,
                              COUNT(*) AS product_total,
                              SUM(CASE
                                  WHEN items.completed_counted_quantity IS NOT NULL
                                  THEN 1 ELSE 0
                              END) AS counted_product_count,
                              SUM(CASE
                                  WHEN items.completed_counted_quantity IS NULL
                                  THEN 1 ELSE 0
                              END) AS uncounted_product_count,
                              SUM(CASE
                                  WHEN items.difference IS NOT NULL
                                       AND items.difference <> '0'
                                  THEN 1 ELSE 0
                              END) AS quantity_difference_count
                       FROM inventory_items AS items
                       JOIN history_page USING (task_id)
                       GROUP BY items.task_id
                   ), discrepancy_counts AS (
                       SELECT discrepancies.task_id,
                              SUM(CASE
                                  WHEN (
                                      discrepancies.serial IS NOT NULL
                                      AND TRIM(discrepancies.serial) <> ''
                                  ) OR discrepancies.kind = 'serial_unverified'
                                  THEN 1 ELSE 0
                              END) AS serial_difference_count
                       FROM inventory_discrepancies AS discrepancies
                       JOIN history_page USING (task_id)
                       GROUP BY discrepancies.task_id
                   ), participant_counts AS (
                       SELECT audit_events.task_id,
                              COUNT(DISTINCT CASE
                                  WHEN TRIM(COALESCE(
                                      audit_events.device_id, ''
                                  )) <> 'task-completion'
                                  THEN NULLIF(TRIM(
                                      audit_events.device_id
                                  ), '')
                              END) AS device_count
                       FROM inventory_audit_events AS audit_events
                       JOIN history_page USING (task_id)
                       GROUP BY audit_events.task_id
                   )
                   SELECT history_page.*,
                          COALESCE(item_counts.product_total, 0) AS product_total,
                          COALESCE(
                              item_counts.counted_product_count, 0
                          ) AS counted_product_count,
                          COALESCE(
                              item_counts.uncounted_product_count, 0
                          ) AS uncounted_product_count,
                          COALESCE(
                              item_counts.quantity_difference_count, 0
                          ) AS quantity_difference_count,
                          COALESCE(
                              discrepancy_counts.serial_difference_count, 0
                          ) AS serial_difference_count,
                          CASE
                              WHEN COALESCE(
                                  participant_counts.device_count, 0
                              ) > 0
                              THEN participant_counts.device_count
                              WHEN TRIM(COALESCE(
                                  history_page.created_by, ''
                              )) <> ''
                              THEN 1
                              ELSE 0
                          END AS participant_count
                   FROM history_page
                   LEFT JOIN item_counts USING (task_id)
                   LEFT JOIN discrepancy_counts USING (task_id)
                   LEFT JOIN participant_counts USING (task_id)
                   ORDER BY history_page.completed_at DESC,
                            history_page.history_rowid DESC""",
                (owner, limit, offset),
            ).fetchall()
        result = [self._row_dict(row) for row in rows]
        for task in result:
            task["completed"] = True
            task.pop("history_rowid", None)
            for key in (
                "product_total", "counted_product_count",
                "uncounted_product_count", "quantity_difference_count",
                "serial_difference_count", "participant_count",
            ):
                task[key] = int(task.get(key) or 0)
        return {
            "tasks": result,
            "total": int(total),
            "limit": limit,
            "offset": offset,
        }

    def get_task_history_detail(self, owner, task_id, scope="differences"):
        scope = str(scope or "differences").strip()
        if scope not in {
            "differences", "participants", "all", "counted", "uncounted",
            "quantity", "serial",
        }:
            raise ValueError("历史详情类型不正确")
        with closing(self.connect()) as connection:
            task = self._task_for_owner(connection, owner, task_id)
            if task["phase"] != "completed":
                raise InventoryConflict("盘点任务尚未完成")
            if scope == "participants":
                participant_rows = connection.execute(
                    """SELECT actor, device_id, created_at
                         FROM inventory_audit_events
                        WHERE task_id = ?
                          AND TRIM(COALESCE(device_id, '')) <> ''
                          AND TRIM(device_id) <> 'task-completion'
                        ORDER BY event_id""",
                    (task_id,),
                ).fetchall()
                participants_by_device = {}
                for row in participant_rows:
                    participant = participants_by_device.get(row["device_id"])
                    if participant is None:
                        participant = {
                            "actor": row["actor"],
                            "device_id": row["device_id"],
                            "action_count": 0,
                            "first_activity_at": row["created_at"],
                            "last_activity_at": row["created_at"],
                        }
                        participants_by_device[row["device_id"]] = participant
                    participant["actor"] = row["actor"] or participant["actor"]
                    participant["action_count"] += 1
                    participant["last_activity_at"] = row["created_at"]
                participants = list(participants_by_device.values())
                if not participants and str(task["created_by"] or "").strip():
                    participants.append({
                        "actor": task["created_by"],
                        "device_id": "",
                        "action_count": 0,
                        "first_activity_at": task["started_at"],
                        "last_activity_at": task["completed_at"],
                    })
                return {
                    "task": self._row_dict(task), "scope": scope,
                    "items": [], "participants": participants,
                }
            item_rows = connection.execute(
                """SELECT * FROM inventory_items
                    WHERE task_id = ?
                    ORDER BY barcode""",
                (task_id,),
            ).fetchall()
            serials_by_barcode = {}
            discrepant_barcodes = set()
            if scope in {"differences", "serial"}:
                discrepancy_rows = connection.execute(
                    """SELECT discrepancy_id AS id, task_id, barcode, serial,
                              kind, book_quantity, counted_quantity, difference,
                              status AS state, archived_by, archived_at, created_at
                         FROM inventory_discrepancies
                        WHERE task_id = ?
                        ORDER BY barcode, COALESCE(serial, ''), discrepancy_id""",
                    (task_id,),
                ).fetchall()
                for row in discrepancy_rows:
                    discrepancy = self._row_dict(row)
                    discrepant_barcodes.add(discrepancy["barcode"])
                    if discrepancy["kind"] == "product_quantity":
                        continue
                    serials_by_barcode.setdefault(
                        discrepancy["barcode"], []
                    ).append(discrepancy)
            items = []
            for row in item_rows:
                item = self._item_dict(row)
                include = (
                    scope == "all"
                    or (
                        scope == "counted"
                        and item["completed_actual_qty"] is not None
                    )
                    or (
                        scope == "uncounted"
                        and item["completed_actual_qty"] is None
                    )
                    or (
                        scope == "quantity"
                        and item["diff_qty"] is not None
                        and item["diff_qty"] != "0"
                    )
                    or (
                        scope == "serial"
                        and item["barcode"] in serials_by_barcode
                    )
                    or (
                        scope == "differences"
                        and (
                            item["barcode"] in discrepant_barcodes
                            or (
                                item["diff_qty"] is not None
                                and item["diff_qty"] != "0"
                            )
                        )
                    )
                )
                if not include:
                    continue
                item["serial_discrepancies"] = serials_by_barcode.get(
                    item["barcode"], []
                )
                items.append(item)
        return {
            "task": self._row_dict(task), "scope": scope,
            "items": items, "participants": [],
        }

    def list_audit_events(self, owner, task_id, barcode=None):
        barcode = str(barcode or "").strip()
        with closing(self.connect()) as connection:
            self._task_for_owner(connection, owner, task_id)
            clauses = ["events.task_id = ?"]
            params = [task_id]
            if barcode:
                clauses.append("events.barcode = ?")
                params.append(barcode)
            placeholders = ",".join("?" for _ in _VISIBLE_AUDIT_EVENT_TYPES)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(_VISIBLE_AUDIT_EVENT_TYPES)
            rows = connection.execute(
                """SELECT events.event_id, events.barcode, events.event_type,
                          events.actor, events.device_id, events.details,
                          events.created_at,
                          COALESCE(items.name, '') AS product_name
                     FROM inventory_audit_events AS events
                     LEFT JOIN inventory_items AS items
                       ON items.task_id = events.task_id
                      AND items.barcode = events.barcode
                    WHERE """ + " AND ".join(clauses) + " ORDER BY events.event_id",
                params,
            ).fetchall()
        result = []
        count_entry_numbers = {}
        for row in rows:
            before_quantity = None
            after_quantity = None
            before_serial = None
            after_serial = None
            before_classification = None
            after_classification = None
            entry_number = None
            carton_id = None
            start_serial = None
            end_serial = None
            before_preset_quantity = None
            after_preset_quantity = None
            confirmed_quantity = None
            affected_count = None
            serials = []
            try:
                details = json.loads(row["details"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                details = {}
            if (
                row["event_type"].startswith("count_entry_")
                or row["event_type"] == "count_adjusted_for_stock_movement"
                or row["event_type"] in {
                    "serial_scanned", "serial_scan_reclassified",
                    "serial_scan_removed",
                }
            ):
                before = details.get("before") if isinstance(details, dict) else None
                after = details.get("after") if isinstance(details, dict) else None
                if row["event_type"].startswith("count_entry_"):
                    entry_id = details.get("entry_id") if isinstance(details, dict) else None
                    if isinstance(entry_id, int) and not isinstance(entry_id, bool) and entry_id > 0:
                        numbers = count_entry_numbers.setdefault(row["barcode"], {})
                        entry_number = numbers.setdefault(entry_id, len(numbers) + 1)
                if isinstance(before, dict):
                    before_quantity = before.get("quantity")
                if isinstance(after, dict):
                    after_quantity = after.get("quantity")
                if row["event_type"].startswith("serial_"):
                    if isinstance(before, dict):
                        before_serial = before.get("serial")
                        before_classification = before.get("classification")
                    if isinstance(after, dict):
                        after_serial = after.get("serial")
                        after_classification = after.get("classification")
            if row["event_type"] == "carton_preset_changed" and isinstance(details, dict):
                before_preset_quantity = details.get("before")
                after_preset_quantity = details.get("after")
            if row["event_type"] in {
                "carton_created", "carton_serial_added",
                "carton_serial_removed", "carton_deleted",
            } and isinstance(details, dict):
                if isinstance(details.get("carton_id"), int) and not isinstance(
                    details.get("carton_id"), bool
                ):
                    carton_id = details["carton_id"]
                if isinstance(details.get("start_serial"), str):
                    start_serial = details["start_serial"]
                if isinstance(details.get("end_serial"), str):
                    end_serial = details["end_serial"]
                if isinstance(details.get("confirmed_quantity"), int) and not isinstance(
                    details.get("confirmed_quantity"), bool
                ):
                    confirmed_quantity = details["confirmed_quantity"]
                if isinstance(details.get("affected_count"), int) and not isinstance(
                    details.get("affected_count"), bool
                ):
                    affected_count = details["affected_count"]
                if isinstance(details.get("serials"), list):
                    serials = [
                        serial for serial in details["serials"]
                        if isinstance(serial, str)
                    ]
            result.append({
                "id": int(row["event_id"]),
                "barcode": row["barcode"],
                "product_name": row["product_name"],
                "event_type": row["event_type"],
                "event_label": _AUDIT_EVENT_LABELS[row["event_type"]],
                "entry_number": entry_number,
                "actor": row["actor"],
                "device_id": row["device_id"],
                "created_at": row["created_at"],
                "before_quantity": before_quantity,
                "after_quantity": after_quantity,
                "before_serial": before_serial,
                "after_serial": after_serial,
                "before_classification": before_classification,
                "after_classification": after_classification,
                "carton_id": carton_id,
                "start_serial": start_serial,
                "end_serial": end_serial,
                "before_preset_quantity": before_preset_quantity,
                "after_preset_quantity": after_preset_quantity,
                "confirmed_quantity": confirmed_quantity,
                "affected_count": affected_count,
                "serials": serials,
            })
        return result

    def list_discrepancies(self, owner, state, query=""):
        if state not in {"open", "archived"}:
            raise ValueError("差异状态不正确")
        query = str(query or "").strip()
        sql = _DISCREPANCY_SELECT + """
            WHERE tasks.owner = ? AND discrepancies.status = ?
        """
        params = [owner, state]
        if query:
            sql += """
                AND (
                    LOWER(discrepancies.barcode) LIKE LOWER(?)
                    OR LOWER(items.name) LIKE LOWER(?)
                    OR LOWER(COALESCE(discrepancies.serial, '')) LIKE LOWER(?)
                )
            """
            pattern = f"%{query}%"
            params.extend((pattern, pattern, pattern))
        sql += " ORDER BY discrepancies.created_at DESC, discrepancies.discrepancy_id DESC"
        with closing(self.connect()) as connection:
            return [
                self._discrepancy_dict(connection, row)
                for row in connection.execute(sql, params)
            ]

    def add_discrepancy_note(
        self, owner, discrepancy_id, actor, note, serial=None, *,
        expected_version=None,
    ):
        note = str(note or "").strip()
        if not note:
            raise ValueError("备注不能为空")
        if serial is not None:
            serial = str(serial).strip()
            if not serial:
                raise ValueError("序列号不能为空")

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            discrepancy = self._discrepancy_for_owner(
                connection, owner, discrepancy_id
            )
            self._assert_expected_version(
                connection, discrepancy["task_id"], expected_version
            )
            if serial is not None:
                known = connection.execute(
                    """SELECT 1 FROM inventory_discrepancies
                       WHERE task_id = ? AND barcode = ? AND serial = ?""",
                    (discrepancy["task_id"], discrepancy["barcode"], serial),
                ).fetchone()
                if known is None:
                    raise InventoryNotFound("差异序列号不存在")
            timestamp = self._now_text()
            cursor = connection.execute(
                """INSERT INTO inventory_notes
                   (task_id, barcode, serial, note, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    discrepancy["task_id"], discrepancy["barcode"], serial,
                    note, actor, timestamp,
                ),
            )
            self._bump_version(connection, discrepancy["task_id"])
            self._audit(
                connection, discrepancy["task_id"], "discrepancy_note_added",
                actor, barcode=discrepancy["barcode"],
                details=(
                    f"discrepancy={discrepancy_id};serial={serial or ''};"
                    f"note={note}"
                ),
                created_at=timestamp,
            )
            connection.commit()
            result = self._row_dict(connection.execute(
                """SELECT note_id AS id, task_id, barcode, serial, note,
                          actor, created_at
                   FROM inventory_notes WHERE note_id = ?""",
                (cursor.lastrowid,),
            ).fetchone())
            result["task_version"] = self._task_version(
                connection, discrepancy["task_id"]
            )
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def archive_discrepancy(
        self, owner, discrepancy_id, actor, is_admin, *, expected_version=None
    ):
        if not is_admin:
            raise InventoryPermissionDenied("只有管理员可以归档差异")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            discrepancy = self._discrepancy_for_owner(
                connection, owner, discrepancy_id
            )
            self._assert_expected_version(
                connection, discrepancy["task_id"], expected_version
            )
            if discrepancy["state"] == "archived":
                connection.commit()
                discrepancy["task_version"] = self._task_version(
                    connection, discrepancy["task_id"]
                )
                return discrepancy
            timestamp = self._now_text()
            connection.execute(
                """UPDATE inventory_discrepancies
                   SET status = 'archived', archived_by = ?, archived_at = ?
                   WHERE discrepancy_id = ?""",
                (actor, timestamp, discrepancy_id),
            )
            self._bump_version(connection, discrepancy["task_id"])
            self._audit(
                connection, discrepancy["task_id"], "discrepancy_archived",
                actor, barcode=discrepancy["barcode"],
                details=f"discrepancy={discrepancy_id}", created_at=timestamp,
            )
            connection.commit()
            result = self._discrepancy_for_owner(
                connection, owner, discrepancy_id
            )
            result["task_version"] = self._task_version(
                connection, discrepancy["task_id"]
            )
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def restore_discrepancy(
        self, owner, discrepancy_id, actor, is_admin, *, expected_version=None
    ):
        if not is_admin:
            raise InventoryPermissionDenied("只有管理员可以恢复差异")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            discrepancy = self._discrepancy_for_owner(
                connection, owner, discrepancy_id
            )
            self._assert_expected_version(
                connection, discrepancy["task_id"], expected_version
            )
            if discrepancy["state"] == "open":
                connection.commit()
                discrepancy["task_version"] = self._task_version(
                    connection, discrepancy["task_id"]
                )
                return discrepancy
            timestamp = self._now_text()
            connection.execute(
                """UPDATE inventory_discrepancies
                   SET status = 'open', archived_by = NULL, archived_at = NULL
                   WHERE discrepancy_id = ?""",
                (discrepancy_id,),
            )
            self._bump_version(connection, discrepancy["task_id"])
            self._audit(
                connection, discrepancy["task_id"], "discrepancy_restored",
                actor, barcode=discrepancy["barcode"],
                details=f"discrepancy={discrepancy_id}", created_at=timestamp,
            )
            connection.commit()
            result = self._discrepancy_for_owner(
                connection, owner, discrepancy_id
            )
            result["task_version"] = self._task_version(
                connection, discrepancy["task_id"]
            )
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
