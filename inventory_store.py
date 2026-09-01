"""Durable persistence primitives for GYJ inventory stocktake."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import os
import sqlite3
import uuid


_CATALOG_KEYS = frozenset(
    {"barcode", "name", "spec", "model", "category", "unit", "has_serial", "initial_stock"}
)


class InventoryConflict(RuntimeError):
    pass


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
        with self.connect() as connection:
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
                    gyj_status TEXT
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
                    initial_stock TEXT NOT NULL DEFAULT '0',
                    book_quantity TEXT,
                    counted_quantity TEXT,
                    completed_book_quantity TEXT,
                    completed_counted_quantity TEXT,
                    latest_book_quantity TEXT,
                    difference TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
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
                    device_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (task_id, barcode)
                        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
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
                CREATE INDEX IF NOT EXISTS idx_inventory_serial_scans_item
                    ON inventory_serial_scans(task_id, barcode, serial);
                CREATE INDEX IF NOT EXISTS idx_inventory_discrepancies_status
                    ON inventory_discrepancies(status);
                """
            )

    def _now_text(self):
        return self.now().isoformat(timespec="seconds")

    @staticmethod
    def _row_dict(row):
        return dict(row) if row is not None else None

    def create_task(self, owner, actor, catalog):
        for product in catalog:
            if not isinstance(product, dict) or set(product) != _CATALOG_KEYS:
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
                        has_serial, initial_stock, book_quantity, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_id,
                        product["barcode"],
                        product["name"],
                        product["spec"],
                        product["model"],
                        product["category"],
                        product["unit"],
                        1 if product["has_serial"] else 0,
                        normalize_quantity(product["initial_stock"]),
                        normalize_quantity(product["initial_stock"]),
                        timestamp,
                    ),
                )
            connection.execute(
                """INSERT INTO inventory_audit_events
                   (task_id, event_type, actor, details, created_at)
                   VALUES (?, 'task_created', ?, ?, ?)""",
                (task_id, actor, "catalog_loaded", timestamp),
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
        with self.connect() as connection:
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
            snapshot["items"] = [self._row_dict(row) for row in connection.execute(
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
        with self.connect() as connection:
            return [self._row_dict(row) for row in connection.execute(sql, params)]

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

    def claim_item(self, task_id, barcode, device_id, actor, phase):
        now_dt = self.now()
        now = now_dt.isoformat(timespec="seconds")
        expires = (now_dt + timedelta(seconds=120)).isoformat(timespec="seconds")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locks(connection, task_id, now)
            item = connection.execute(
                "SELECT 1 FROM inventory_items WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if item is None:
                raise InventoryNotFound("商品不存在")
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
                raise InventoryConflict("商品已被其他设备锁定")
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
            return self._row_dict(connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone())
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat_lock(self, task_id, barcode, device_id):
        now_dt = self.now()
        now = now_dt.isoformat(timespec="seconds")
        expires = (now_dt + timedelta(seconds=120)).isoformat(timespec="seconds")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locks(connection, task_id, now)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                (task_id, barcode),
            ).fetchone()
            if lock is None or lock["device_id"] != device_id:
                self._audit(connection, task_id, "lock_conflict", device_id, barcode, device_id, "heartbeat_owner_mismatch", now)
                connection.commit()
                raise InventoryConflict("设备未持有商品锁")
            connection.execute(
                "UPDATE inventory_item_locks SET heartbeat_at = ?, expires_at = ? WHERE task_id = ? AND barcode = ?",
                (now, expires, task_id, barcode),
            )
            self._audit(connection, task_id, "lock_heartbeat", lock["actor"], barcode, device_id, "extension", now)
            connection.commit()
            return self._row_dict(connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone())
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def assert_item_lock(self, task_id, barcode, device_id, phase):
        now = self._now_text()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_locks(connection, task_id, now)
            lock = connection.execute(
                "SELECT * FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone()
            if lock is None or lock["device_id"] != device_id or lock["phase"] != phase:
                self._audit(connection, task_id, "lock_conflict", device_id, barcode, device_id, "assertion_failed", now)
                connection.commit()
                raise InventoryConflict("设备未持有当前阶段的商品锁")
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

    def admin_unlock(self, task_id, barcode, actor, is_admin):
        if is_admin is not True:
            raise InventoryPermissionDenied("只有管理员可以强制解锁")
        now = self._now_text()
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lock = connection.execute(
                "SELECT device_id FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            ).fetchone()
            if lock is None:
                self._audit(connection, task_id, "lock_admin_unlocked", actor, barcode, details="no_lock", created_at=now)
                connection.commit()
                return {"unlocked": False, "task_id": task_id, "barcode": barcode}
            connection.execute(
                "DELETE FROM inventory_item_locks WHERE task_id = ? AND barcode = ?", (task_id, barcode)
            )
            self._bump_version(connection, task_id)
            self._audit(connection, task_id, "lock_admin_unlocked", actor, barcode, lock["device_id"], "manual_unlock", now)
            connection.commit()
            return {"unlocked": True, "task_id": task_id, "barcode": barcode}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
