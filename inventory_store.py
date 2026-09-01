"""Durable persistence primitives for GYJ inventory stocktake."""

from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import os
import sqlite3


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
