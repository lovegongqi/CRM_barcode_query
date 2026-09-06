import os
import inspect
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from unittest import mock
from datetime import datetime, timedelta

from inventory_store import (
    InventoryConfirmationRequired,
    InventoryConflict,
    InventoryNotFound,
    InventoryPermissionDenied,
    InventoryStore,
    InventoryVersionConflict,
    normalize_quantity,
    quantity_difference,
)


class InventoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "inventory.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def catalog(self):
        return [
            {"barcode": "A1", "name": "有库存商品", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "2"},
            {"barcode": "B2", "name": "零库存商品", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": True, "initial_stock": "0"},
        ]

    def serial_ready_store(self, catalog=None):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        task = store.create_task("admin", "管理员", catalog or self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        for index, product in enumerate(catalog or self.catalog()):
            device = f"count-device-{index}"
            store.claim_item(
                task["task_id"], product["barcode"], device, device, "counting"
            )
            book = product["initial_stock"]
            actual = str(int(book) + 1) if product["has_serial"] else book
            state = "serial_pending" if product["has_serial"] else "matched"
            store.set_open_book_quantity(
                "admin", task["task_id"], product["barcode"], device, device, book
            )
            store.record_count(
                "admin", task["task_id"], product["barcode"], device, device,
                completed_book_qty=book, completed_actual_qty=actual,
                diff_qty="1" if product["has_serial"] else "0", state=state,
            )
        return store, task

    def test_one_active_task_per_owner_and_restart_persistence(self):
        store = InventoryStore(self.db_path)
        first = store.create_task("admin", "管理员", self.catalog())
        with self.assertRaises(InventoryConflict):
            store.create_task("admin", "管理员", self.catalog())
        reloaded = InventoryStore(self.db_path).get_active_task("admin")
        self.assertEqual(reloaded["task_id"], first["task_id"])

    def test_zero_stock_is_hidden_until_search(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        self.assertEqual([row["barcode"] for row in store.list_items(task["task_id"])], ["A1"])
        self.assertEqual([row["barcode"] for row in store.list_items(task["task_id"], query="B2")], ["B2"])

    def test_snapshot_omits_items_when_version_is_unchanged(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        snapshot = store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(snapshot["version"], 1)
        self.assertIn("items", snapshot)
        unchanged = store.get_task_snapshot("admin", task["task_id"], known_version=1)
        self.assertEqual(unchanged, {"success": True, "unchanged": True, "version": 1})

    def test_task_creation_inserts_audit_event(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        with sqlite3.connect(self.db_path) as connection:
            event = connection.execute(
                "SELECT event_type, actor FROM inventory_audit_events WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(event, ("task_created", "管理员"))

    def _assert_catalog_rejected_without_records(self, catalog):
        store = InventoryStore(self.db_path)
        with self.assertRaisesRegex(ValueError, "catalog item must contain exactly the allowed keys"):
            store.create_task("admin", "管理员", catalog)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inventory_tasks").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inventory_audit_events").fetchone()[0], 0)

    def test_catalog_missing_key_is_rejected_without_persisting_records(self):
        product = self.catalog()[0].copy()
        del product["unit"]
        self._assert_catalog_rejected_without_records([product])

    def test_catalog_extra_price_key_is_rejected_without_persisting_records(self):
        product = self.catalog()[0].copy()
        product["cost_price"] = "100"
        self._assert_catalog_rejected_without_records([product])

    def test_catalog_anomaly_requires_a_nonempty_data_error(self):
        anomaly = {
            "barcode": "ORPHAN", "name": "无商品信息", "spec": "", "model": "",
            "category": "", "unit": "个", "initial_stock": "1", "data_error": "",
        }
        self._assert_catalog_rejected_without_records([anomaly])

    def test_catalog_data_error_is_persisted_as_unknown_serial_and_not_countable(self):
        anomaly = {
            "barcode": "ORPHAN", "name": "无商品信息", "spec": "", "model": "",
            "category": "", "unit": "个", "initial_stock": "1",
            "data_error": "无法确认商品序列号设置",
        }
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", [anomaly])
        store.advance_count_phase_if_ready("admin", task["task_id"])
        item = store.get_task_snapshot("admin", task["task_id"])["items"][0]

        self.assertEqual(item["state"], "data_error")
        self.assertEqual(item["data_error"], "无法确认商品序列号设置")
        self.assertIsNone(item["has_serial"])
        with self.assertRaisesRegex(InventoryConflict, "资料异常"):
            store.claim_item(
                task["task_id"], "ORPHAN", "device-a", "甲", "counting"
            )

        completed = store.complete_task("admin", task["task_id"], "管理员")
        self.assertTrue(completed["completed"])
        self.assertEqual(store.list_discrepancies("admin", "open"), [])

    def test_mixed_catalog_skips_data_error_without_hiding_normal_differences(self):
        anomaly = {
            "barcode": "ORPHAN", "name": "无商品信息", "spec": "", "model": "",
            "category": "", "unit": "个", "initial_stock": "9",
            "data_error": "无法确认商品序列号设置",
        }
        store = InventoryStore(self.db_path)
        task = store.create_task(
            "admin", "管理员", [self.catalog()[0], anomaly]
        )
        task = store.advance_count_phase_if_ready("admin", task["task_id"])
        lock = store.claim_item(
            task["task_id"], "A1", "device-a", "甲", "counting",
            expected_version=task["version"],
        )
        counted = store.record_count(
            "admin", task["task_id"], "A1", "device-a", "甲",
            completed_book_qty="2", completed_actual_qty="1",
            diff_qty="-1", state="variance",
            expected_version=lock["task_version"],
        )

        completed = store.complete_task(
            "admin", task["task_id"], "管理员",
            expected_version=counted["task_version"],
        )
        self.assertTrue(completed["completed"])
        items = store.get_task_snapshot("admin", task["task_id"])["items"]
        error_item = next(row for row in items if row["barcode"] == "ORPHAN")
        self.assertEqual(error_item["state"], "data_error")
        self.assertIsNone(error_item["completed_actual_qty"])
        self.assertIsNone(error_item["has_serial"])
        discrepancies = store.list_discrepancies("admin", "open")
        self.assertEqual(
            [(row["barcode"], row["kind"]) for row in discrepancies],
            [("A1", "product_quantity")],
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            events = connection.execute(
                "SELECT event_type, details FROM inventory_audit_events "
                "WHERE task_id = ? ORDER BY event_id",
                (task["task_id"],),
            ).fetchall()
        self.assertIn(("task_created", "catalog_loaded;data_errors=1"), events)
        completion = json.loads(next(
            details for event_type, details in events
            if event_type == "task_completed"
        ))
        self.assertEqual(completion["discrepancies"], 1)
        self.assertEqual(completion["counted_items"], 1)

    def test_initialize_migrates_existing_items_with_empty_data_error(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.executescript(
                """CREATE TABLE inventory_tasks (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    created_by TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'loading',
                    version INTEGER NOT NULL DEFAULT 1, started_at TEXT NOT NULL,
                    completed_at TEXT, last_sync_at TEXT, gyj_status TEXT
                );
                CREATE TABLE inventory_items (
                    task_id TEXT NOT NULL, barcode TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '', spec TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
                    unit TEXT NOT NULL DEFAULT '', has_serial INTEGER NOT NULL DEFAULT 0,
                    initial_stock TEXT NOT NULL DEFAULT '0', book_quantity TEXT,
                    counted_quantity TEXT, completed_book_quantity TEXT,
                    completed_counted_quantity TEXT, latest_book_quantity TEXT,
                    difference TEXT, status TEXT NOT NULL DEFAULT 'pending',
                    completed_at TEXT, updated_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, barcode)
                );
                INSERT INTO inventory_tasks
                    (task_id, owner, created_by, phase, version, started_at)
                    VALUES ('legacy-task', 'admin', '管理员', 'counting', 2,
                            '2026-08-31T10:00:00');
                INSERT INTO inventory_items
                    (task_id, barcode, name, unit, has_serial, initial_stock,
                     book_quantity, status, updated_at)
                    VALUES ('legacy-task', 'A1', '旧库商品', '个', 0, '2', '2',
                            'pending', '2026-08-31T10:00:00');
                """
            )
        store = InventoryStore(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as connection:
            columns = {row[1]: row for row in connection.execute(
                "PRAGMA table_info(inventory_items)"
            )}
        self.assertIn("data_error", columns)
        self.assertEqual(columns["data_error"][3], 1)
        self.assertEqual(columns["data_error"][4], "''")
        item = store.get_task_snapshot("admin", "legacy-task")["items"][0]
        self.assertEqual(item["name"], "旧库商品")
        self.assertEqual(item["data_error"], "")
        self.assertFalse(item["has_serial"])
        self.assertEqual(item["open_book_qty"], "2")

    def test_quantity_helpers_do_not_use_binary_float_rounding(self):
        self.assertEqual(normalize_quantity("10.00"), "10")
        self.assertEqual(normalize_quantity("0.125"), "0.125")
        self.assertEqual(quantity_difference("9.5", "10"), "-0.5")

    def test_quantity_helpers_preserve_more_than_decimal_context_precision(self):
        value = "123456789012345678901234567890.123456789"
        self.assertEqual(normalize_quantity(value), value)
        self.assertEqual(
            quantity_difference("123456789012345678901234567890", "1"),
            "123456789012345678901234567889",
        )

    def test_initialize_creates_every_inventory_table(self):
        InventoryStore(self.db_path).initialize()
        with sqlite3.connect(self.db_path) as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({
            "inventory_tasks", "inventory_items", "inventory_item_locks",
            "inventory_serial_expected", "inventory_serial_scans",
            "inventory_stock_movements", "inventory_discrepancies",
            "inventory_notes", "inventory_audit_events",
            "inventory_carton_presets", "inventory_cartons",
        }.issubset(names))

    def test_carton_groups_do_not_have_a_business_carton_code(self):
        store = InventoryStore(self.db_path)
        store.initialize()
        with sqlite3.connect(self.db_path) as connection:
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(inventory_cartons)"
                )
            }
        self.assertNotIn("carton_code", columns)
        self.assertNotIn(
            "carton_code",
            inspect.signature(store.create_serial_carton).parameters,
        )

    def test_initialize_adds_carton_link_to_serial_scans(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """CREATE TABLE inventory_serial_scans (
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
                    active INTEGER NOT NULL DEFAULT 1
                )"""
            )
            connection.executemany(
                """INSERT INTO inventory_serial_scans
                   (task_id, barcode, serial, classification, device_id, actor,
                    scanned_at, active)
                   VALUES (?, ?, 'LEGACY-DUPLICATE', 'unknown', 'd1', '甲',
                           '2026-09-06T10:00:00', 1)""",
                [("legacy-task", "B2"), ("legacy-task", "C3")],
            )
        InventoryStore(self.db_path).initialize()
        with sqlite3.connect(self.db_path) as connection:
            columns = {
                row[1]: row for row in connection.execute(
                    "PRAGMA table_info(inventory_serial_scans)"
                )
            }
            active_duplicates = connection.execute(
                """SELECT barcode, serial FROM inventory_serial_scans
                   WHERE task_id = 'legacy-task' AND active = 1
                   ORDER BY barcode"""
            ).fetchall()
        self.assertIn("carton_id", columns)
        self.assertEqual(columns["carton_id"][3], 0)
        self.assertEqual(
            active_duplicates,
            [("B2", "LEGACY-DUPLICATE"), ("C3", "LEGACY-DUPLICATE")],
        )

    def test_carton_preset_is_shared_editable_and_audited(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        store.save_carton_preset("admin", task["task_id"], "B2", "d1", "甲", 20)
        result = store.save_carton_preset(
            "admin", task["task_id"], "B2", "d2", "乙", 12
        )
        self.assertEqual(result["carton_quantity"], 12)
        self.assertEqual(
            store.get_carton_preset("admin", task["task_id"], "B2")["updated_by"],
            "乙",
        )
        with sqlite3.connect(self.db_path) as connection:
            details = connection.execute(
                "SELECT details FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'carton_preset_changed' "
                "ORDER BY event_id DESC LIMIT 1",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(json.loads(details), {"after": 12, "before": 20})

    def test_duplicate_serial_rolls_back_the_entire_carton(self):
        store, task = self.serial_ready_store()
        store.replace_expected_serials(
            "admin", task["task_id"], "B2", "d1", "甲", [{
                "serial": "S001", "barcode": "B2", "name": "零库存商品",
                "warehouse": "沈桥仓", "shipped": False,
            }],
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "d1", "甲", "S001", "matched"
        )

        with self.assertRaisesRegex(InventoryConflict, "S001"):
            store.create_serial_carton(
                "admin", task["task_id"], "B2", "d2", "乙",
                2, "S001", ["S001", "S002"],
            )

        with store.connect() as connection:
            carton_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_cartons WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0]
            carton_scan_count = connection.execute(
                """SELECT COUNT(*) FROM inventory_serial_scans
                   WHERE task_id = ? AND carton_id IS NOT NULL""",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(carton_count, 0)
        self.assertEqual(carton_scan_count, 0)

    def test_carton_mutations_group_and_order_active_scans(self):
        catalog = self.catalog() + [{
            "barcode": "C3", "name": "另一序列商品", "spec": "", "model": "",
            "category": "配件", "unit": "个", "has_serial": True,
            "initial_stock": "0",
        }]
        store, task = self.serial_ready_store(catalog)
        task_id = task["task_id"]
        with self.assertRaisesRegex(InventoryConflict, "账面序列号"):
            store.create_serial_carton(
                "admin", task_id, "B2", "d1", "甲",
                1, "S000", ["S000"],
            )
        store.replace_expected_serials(
            "admin", task_id, "B2", "d1", "甲", [
                {"serial": "S001", "barcode": "B2", "name": "零库存商品", "warehouse": "沈桥仓", "shipped": False},
                {"serial": "S003", "barcode": "B2", "name": "零库存商品", "warehouse": "沈桥仓", "shipped": False},
                {"serial": "S005", "barcode": "B2", "name": "零库存商品", "warehouse": "沈桥仓", "shipped": False},
                {"serial": "S004", "barcode": "B2", "name": "零库存商品", "warehouse": "沈桥仓", "shipped": False},
            ],
        )
        store.replace_expected_serials(
            "admin", task_id, "C3", "d1", "甲", [],
        )
        store.save_carton_preset("admin", task_id, "B2", "d1", "甲", 2)
        store.add_serial_scan(
            "admin", task_id, "B2", "d1", "甲", "LEGACY-1", "unknown"
        )

        created = store.create_serial_carton(
            "admin", task_id, "B2", "d2", "乙",
            2, "S001", ["S001", "S002"],
        )
        carton_id = created["cartons"][0]["carton_id"]
        self.assertEqual(created["carton_preset"]["carton_quantity"], 2)
        self.assertEqual(
            [(row["serial"], row["classification"])
             for row in created["cartons"][0]["scans"]],
            [("S002", "unknown"), ("S001", "matched")],
        )
        self.assertEqual([row["serial"] for row in created["ungrouped"]], ["LEGACY-1"])

        added = store.add_carton_serial(
            "admin", task_id, "B2", "d3", "丙", carton_id, "S003"
        )
        self.assertEqual(
            [row["serial"] for row in added["cartons"][0]["scans"]],
            ["S003", "S002", "S001"],
        )
        self.assertEqual(added["cartons"][0]["confirmed_quantity"], 2)
        with self.assertRaisesRegex(InventoryNotFound, "箱"):
            store.add_carton_serial(
                "admin", task_id, "C3", "d3", "丙", carton_id, "C-1"
            )
        store.remove_carton_serial(
            "admin", task_id, "B2", "d4", "丁", carton_id, "S002"
        )
        store.add_serial_scan(
            "admin", task_id, "B2", "d5", "戊", "LEGACY-2", "unknown"
        )
        reconciled = store.serial_reconciliation("admin", task_id, "B2")
        self.assertEqual(
            [row["serial"] for row in reconciled["cartons"][0]["scans"]],
            ["S003", "S001"],
        )
        self.assertEqual(
            [row["serial"] for row in reconciled["ungrouped"]],
            ["LEGACY-2", "LEGACY-1"],
        )
        self.assertEqual(
            [row["serial"] for row in reconciled["matched"]],
            ["S003", "S001"],
        )
        self.assertEqual(
            [row["serial"] for row in reconciled["system_only"]],
            ["S004", "S005"],
        )

        deleted = store.delete_serial_carton(
            "admin", task_id, "B2", "d6", "己", carton_id
        )
        self.assertEqual(deleted["cartons"], [])
        self.assertEqual(
            [row["serial"] for row in deleted["ungrouped"]],
            ["LEGACY-2", "LEGACY-1"],
        )
        audit_rows = [
            row for row in store.list_audit_events("admin", task_id, barcode="B2")
            if row["event_type"].startswith("carton_")
        ]
        self.assertEqual(
            [(row["event_type"], row["actor"], row["device_id"])
             for row in audit_rows],
            [
                ("carton_preset_changed", "甲", "d1"),
                ("carton_created", "乙", "d2"),
                ("carton_serial_added", "丙", "d3"),
                ("carton_serial_removed", "丁", "d4"),
                ("carton_deleted", "己", "d6"),
            ],
        )
        carton_events = {row["event_type"]: row for row in audit_rows}
        self.assertEqual(
            (carton_events["carton_serial_added"]["start_serial"],
             carton_events["carton_serial_added"]["end_serial"],
             carton_events["carton_serial_added"]["serials"]),
            ("S001", "S003", ["S003"]),
        )
        self.assertEqual(
            (carton_events["carton_serial_removed"]["start_serial"],
             carton_events["carton_serial_removed"]["end_serial"],
             carton_events["carton_serial_removed"]["serials"]),
            ("S001", "S003", ["S002"]),
        )
        self.assertEqual(audit_rows[0]["before_preset_quantity"], None)
        self.assertEqual(audit_rows[0]["after_preset_quantity"], 2)
        self.assertEqual(audit_rows[1]["start_serial"], "S001")
        self.assertEqual(audit_rows[1]["end_serial"], "S002")
        self.assertEqual(audit_rows[1]["confirmed_quantity"], 2)
        self.assertEqual(audit_rows[1]["affected_count"], 2)
        self.assertEqual(audit_rows[1]["serials"], ["S001", "S002"])
        self.assertEqual(audit_rows[2]["serials"], ["S003"])
        self.assertEqual(audit_rows[3]["serials"], ["S002"])
        self.assertEqual(audit_rows[4]["affected_count"], 2)
        self.assertEqual(audit_rows[4]["serials"], ["S001", "S003"])
        with store.connect() as connection:
            carton = connection.execute(
                "SELECT active, deleted_by, deleted_device_id FROM inventory_cartons "
                "WHERE carton_id = ?",
                (carton_id,),
            ).fetchone()
            active_linked = connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_scans "
                "WHERE carton_id = ? AND active = 1",
                (carton_id,),
            ).fetchone()[0]
        self.assertEqual(tuple(carton), (0, "己", "d6"))
        self.assertEqual(active_linked, 0)

    def test_cross_product_active_duplicate_is_controlled(self):
        catalog = self.catalog() + [{
            "barcode": "C3", "name": "另一序列商品", "spec": "", "model": "",
            "category": "配件", "unit": "个", "has_serial": True,
            "initial_stock": "0",
        }]
        store, task = self.serial_ready_store(catalog)
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "d1", "甲", "DUPLICATE", "unknown"
        )
        duplicate = store.add_serial_scan(
            "admin", task["task_id"], "C3", "d2", "乙", "DUPLICATE", "unknown"
        )
        self.assertEqual(duplicate["classification"], "duplicate")
        self.assertEqual(duplicate["barcode"], "B2")
        with store.connect() as connection:
            count = connection.execute(
                """SELECT COUNT(*) FROM inventory_serial_scans
                   WHERE task_id = ? AND serial = 'DUPLICATE' AND active = 1""",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_system_scan_cross_product_duplicate_is_controlled(self):
        catalog = self.catalog() + [{
            "barcode": "C3", "name": "另一序列商品", "spec": "", "model": "",
            "category": "配件", "unit": "个", "has_serial": True,
            "initial_stock": "0",
        }]
        store, task = self.serial_ready_store(catalog)
        store.replace_expected_serials(
            "admin", task["task_id"], "C3", "d1", "甲", [{
                "serial": "DUPLICATE", "barcode": "C3", "name": "另一序列商品",
                "warehouse": "沈桥仓", "shipped": False,
            }],
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "d1", "甲", "DUPLICATE", "unknown"
        )
        with self.assertRaisesRegex(InventoryConflict, "DUPLICATE"):
            store.complete_serial_item(
                "admin", task["task_id"], "C3", "d2", "乙"
            )

    def test_initialize_creates_count_entries_and_backfills_legacy_count_once(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        with store.connect() as connection:
            connection.execute(
                "UPDATE inventory_items SET completed_counted_quantity = '25', "
                "counted_quantity = '25', completed_at = updated_at "
                "WHERE task_id = ? AND barcode = 'A1'",
                (task["task_id"],),
            )
            connection.commit()

        InventoryStore(self.db_path)
        migrated = InventoryStore(self.db_path).get_task_snapshot(
            "admin", task["task_id"]
        )

        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("inventory_count_entries", tables)
            rows = connection.execute(
                "SELECT quantity, version, created_by "
                "FROM inventory_count_entries "
                "WHERE task_id = ? AND barcode = 'A1'",
                (task["task_id"],),
            ).fetchall()
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(inventory_count_entries)"
                )
            }
        self.assertEqual(rows, [("25", 1, "system:migration")])
        self.assertFalse({"price", "amount", "cost"} & columns)
        self.assertEqual(migrated["items"][0]["count_total"], "25")
        self.assertEqual(migrated["items"][0]["count_expression"], "25")
        self.assertEqual(migrated["items"][1]["count_entries"], [])

    def test_context_managed_read_closes_its_sqlite_connection(self):
        store = InventoryStore(self.db_path)
        connection = store.connect()
        with mock.patch.object(store, "connect", return_value=connection):
            store.get_active_task("admin")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_quantity_columns_have_text_affinity(self):
        InventoryStore(self.db_path)
        with sqlite3.connect(self.db_path) as connection:
            quantity_columns = []
            for table in ("inventory_items", "inventory_stock_movements", "inventory_discrepancies"):
                quantity_columns.extend(
                    (table, row[1], row[2])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                    if any(token in row[1] for token in ("stock", "quantity", "difference"))
                )
        self.assertTrue(quantity_columns)
        self.assertTrue(all(column_type == "TEXT" for _, _, column_type in quantity_columns))

    def test_first_device_wins_and_only_admin_can_force_unlock(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        lock = store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        self.assertEqual(lock["device_id"], "device-a")
        with self.assertRaises(InventoryConflict):
            store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")
        with self.assertRaises(InventoryPermissionDenied):
            store.admin_unlock(task["task_id"], "A1", "乙", False)

    def test_lock_expires_after_two_minutes_without_heartbeat(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=121)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 1)
        self.assertEqual(store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")["device_id"], "device-b")

    def test_lock_conflict_carries_only_the_current_replacement_owner(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=121)
        store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")

        with self.assertRaises(InventoryConflict) as heartbeat_conflict:
            store.heartbeat_lock(task["task_id"], "A1", "device-a")
        self.assertEqual(heartbeat_conflict.exception.lock_owner, "乙")

        with self.assertRaises(InventoryConflict) as count_conflict:
            store.assert_item_lock(task["task_id"], "A1", "device-a", "counting")
        self.assertEqual(count_conflict.exception.lock_owner, "乙")

        current[0] += timedelta(seconds=121)
        with self.assertRaises(InventoryConflict) as expired_conflict:
            store.heartbeat_lock(task["task_id"], "A1", "device-b")
        self.assertIsNone(expired_conflict.exception.lock_owner)

    def test_claim_reclaim_heartbeat_and_assertion_use_second_precision(self):
        current = [datetime(2026, 9, 1, 10, 0, 0, 500000)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        first = store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        self.assertEqual(first["expires_at"], "2026-09-01T10:02:00.500000")
        version_after_claim = store.get_task_snapshot("admin", task["task_id"])["version"]
        current[0] = datetime(2026, 9, 1, 10, 1, 0)
        renewed = store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        self.assertEqual(renewed["heartbeat_at"], "2026-09-01T10:01:00")
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["version"], version_after_claim)
        current[0] = datetime(2026, 9, 1, 10, 1, 30)
        heartbeated = store.heartbeat_lock(task["task_id"], "A1", "device-a")
        self.assertEqual(heartbeated["expires_at"], "2026-09-01T10:03:30")
        store.assert_item_lock(task["task_id"], "A1", "device-a", "counting")
        with self.assertRaises(InventoryConflict):
            store.assert_item_lock(task["task_id"], "A1", "device-a", "serial_check")
        with self.assertRaises(InventoryConflict):
            store.heartbeat_lock(task["task_id"], "A1", "device-b")

    def test_heartbeat_audit_uses_authenticated_actor_not_device_id(self):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        task = store.create_task("owner-1", "建立者", self.catalog())
        store.claim_item(
            task["task_id"], "A1", "spoofed-operator", "建立者", "counting"
        )
        store.heartbeat_lock(
            task["task_id"], "A1", "spoofed-operator", "登录用户",
            owner="owner-1",
        )
        with sqlite3.connect(self.db_path) as connection:
            actor, device = connection.execute(
                """SELECT actor, device_id FROM inventory_audit_events
                   WHERE task_id = ? AND event_type = 'lock_heartbeat'
                   ORDER BY event_id DESC LIMIT 1""",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(actor, "登录用户")
        self.assertEqual(device, "spoofed-operator")

    def test_heartbeat_rejects_stale_version_and_does_not_self_increment(self):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        task = store.create_task("owner-1", "甲", self.catalog())
        lock = store.claim_item(
            task["task_id"], "A1", "device-a", "甲", "counting",
            expected_version=task["version"],
        )
        with self.assertRaises(InventoryVersionConflict) as caught:
            store.heartbeat_lock(
                task["task_id"], "A1", "device-a", "甲", owner="owner-1",
                expected_version=task["version"],
            )
        self.assertEqual(caught.exception.current_version, lock["task_version"])
        heartbeat = store.heartbeat_lock(
            task["task_id"], "A1", "device-a", "甲", owner="owner-1",
            expected_version=lock["task_version"],
        )
        self.assertEqual(heartbeat["task_version"], lock["task_version"])
        self.assertEqual(
            store.get_task_version("owner-1", task["task_id"]),
            lock["task_version"],
        )

    def test_expiry_boundary_version_and_audit_semantics(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        claimed_version = store.get_task_snapshot("admin", task["task_id"])["version"]
        current[0] += timedelta(seconds=120)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 1)
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["version"], claimed_version + 1)
        with sqlite3.connect(self.db_path) as connection:
            events = [row[0] for row in connection.execute(
                "SELECT event_type FROM inventory_audit_events WHERE task_id = ? ORDER BY event_id",
                (task["task_id"],),
            )]
        self.assertIn("lock_claimed", events)
        self.assertIn("lock_expired", events)

    def test_admin_unlock_changes_version_and_writes_audit(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        before = store.get_task_snapshot("admin", task["task_id"])["version"]
        result = store.admin_unlock(task["task_id"], "A1", "管理员", True)
        self.assertEqual(result["unlocked"], True)
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["version"], before + 1)
        self.assertEqual(store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")["device_id"], "device-b")
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_audit_events WHERE task_id = ? AND event_type = 'lock_admin_unlocked'",
                (task["task_id"],),
            ).fetchone()[0], 1)

    def test_stale_admin_unlock_is_rejected_before_mutation_and_refresh_retry_succeeds(self):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        task = store.create_task("admin", "管理员", self.catalog())
        stale_version = task["version"]
        claimed = store.claim_item(
            task["task_id"], "A1", "device-a", "甲", "counting",
            expected_version=stale_version,
        )

        with self.assertRaises(InventoryVersionConflict) as caught:
            store.admin_unlock(
                task["task_id"], "A1", "管理员", True,
                expected_version=stale_version,
            )
        self.assertEqual(caught.exception.current_version, claimed["task_version"])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT device_id FROM inventory_item_locks "
                    "WHERE task_id = ? AND barcode = 'A1'",
                    (task["task_id"],),
                ).fetchone()[0],
                "device-a",
            )

        refreshed = store.get_task_snapshot("admin", task["task_id"])["version"]
        result = store.admin_unlock(
            task["task_id"], "A1", "管理员", True,
            expected_version=refreshed,
        )
        self.assertTrue(result["unlocked"])
        self.assertEqual(result["task_version"], refreshed + 1)

    def test_two_device_stale_count_conflicts_but_serial_writes_merge(self):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        task = store.create_task("admin", "管理员", self.catalog())
        task = store.advance_count_phase_if_ready("admin", task["task_id"])
        device_b_version = task["version"]
        lock_b = store.claim_item(
            task["task_id"], "B2", "device-b", "乙", "counting",
            expected_version=device_b_version,
        )
        lock_a = store.claim_item(
            task["task_id"], "A1", "device-a", "甲", "counting",
            expected_version=lock_b["task_version"],
        )
        with self.assertRaises(InventoryVersionConflict):
            store.record_count(
                "admin", task["task_id"], "B2", "device-b", "乙",
                completed_book_qty="0", completed_actual_qty="1",
                diff_qty="1", state="serial_pending",
                expected_version=lock_b["task_version"],
            )
        counted_b = store.record_count(
            "admin", task["task_id"], "B2", "device-b", "乙",
            completed_book_qty="0", completed_actual_qty="1",
            diff_qty="1", state="serial_pending",
            expected_version=lock_a["task_version"],
        )
        counted_a = store.record_count(
            "admin", task["task_id"], "A1", "device-a", "甲",
            completed_book_qty="2", completed_actual_qty="2",
            diff_qty="0", state="matched",
            expected_version=counted_b["task_version"],
        )
        expected = store.replace_expected_serials(
            "admin", task["task_id"], "B2", "device-b", "乙", [{
                "serial": "SN-1", "barcode": "B2", "name": "零库存商品",
                "warehouse": "一仓", "shipped": False,
            }], expected_version=counted_a["task_version"],
        )
        scan = store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-b", "乙", "SN-1",
            "matched", expected_version=1,
        )
        deleted = store.remove_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "SN-1",
            expected_version=1,
        )
        self.assertEqual(deleted["removed"], "SN-1")
        self.assertGreater(scan["task_version"], expected["version"])
        with store.connect() as connection:
            audit = connection.execute(
                "SELECT actor, device_id FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'serial_scan_removed'",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(tuple(audit), ("甲", "device-a"))

    def test_two_device_stale_note_archive_and_restore_retry_after_refresh(self):
        store = InventoryStore(
            self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0)
        )
        catalog = [self.catalog()[0]]
        task = store.create_task("admin", "管理员", catalog)
        task = store.advance_count_phase_if_ready("admin", task["task_id"])
        lock = store.claim_item(
            task["task_id"], "A1", "device-a", "甲", "counting",
            expected_version=task["version"],
        )
        counted = store.record_count(
            "admin", task["task_id"], "A1", "device-a", "甲",
            completed_book_qty="2", completed_actual_qty="1",
            diff_qty="-1", state="variance",
            expected_version=lock["task_version"],
        )
        completed = store.complete_task(
            "admin", task["task_id"], "管理员",
            expected_version=counted["task_version"],
        )
        discrepancy_id = store.list_discrepancies("admin", "open")[0]["id"]
        device_b_version = completed["version"]
        note_a = store.add_discrepancy_note(
            "admin", discrepancy_id, "甲", "甲备注",
            expected_version=device_b_version,
        )
        with self.assertRaises(InventoryVersionConflict):
            store.add_discrepancy_note(
                "admin", discrepancy_id, "乙", "乙备注",
                expected_version=device_b_version,
            )
        note_b = store.add_discrepancy_note(
            "admin", discrepancy_id, "乙", "乙备注",
            expected_version=note_a["task_version"],
        )
        note_c = store.add_discrepancy_note(
            "admin", discrepancy_id, "甲", "归档前备注",
            expected_version=note_b["task_version"],
        )
        with self.assertRaises(InventoryVersionConflict):
            store.archive_discrepancy(
                "admin", discrepancy_id, "乙", True,
                expected_version=note_b["task_version"],
            )
        archived = store.archive_discrepancy(
            "admin", discrepancy_id, "乙", True,
            expected_version=note_c["task_version"],
        )
        bumped = store.add_discrepancy_note(
            "admin", discrepancy_id, "甲", "恢复前备注",
            expected_version=archived["task_version"],
        )
        with self.assertRaises(InventoryVersionConflict):
            store.restore_discrepancy(
                "admin", discrepancy_id, "乙", True,
                expected_version=archived["task_version"],
            )
        restored = store.restore_discrepancy(
            "admin", discrepancy_id, "乙", True,
            expected_version=bumped["task_version"],
        )
        self.assertEqual(restored["state"], "open")

    def test_release_expired_locks_does_not_delete_future_lock(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=119)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 0)
        self.assertEqual(store.heartbeat_lock(task["task_id"], "A1", "device-a")["device_id"], "device-a")

    def test_conflict_writes_audit_without_incrementing_version(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        before = store.get_task_snapshot("admin", task["task_id"])["version"]
        with self.assertRaises(InventoryConflict):
            store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["version"], before)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_audit_events WHERE task_id = ? AND event_type = 'lock_conflict'",
                (task["task_id"],),
            ).fetchone()[0], 1)

    def test_subsecond_claim_keeps_lock_for_at_least_120_seconds(self):
        current = [datetime(2026, 9, 1, 10, 0, 0, 500000)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        lock = store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        self.assertEqual(lock["expires_at"], "2026-09-01T10:02:00.500000")
        current[0] = datetime(2026, 9, 1, 10, 2, 0, 499999)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 0)
        current[0] = datetime(2026, 9, 1, 10, 2, 0, 500000)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 1)

    def test_claim_derives_lease_time_after_waiting_for_write_lock(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        now_called = threading.Event()

        def clock():
            now_called.set()
            return current[0]

        store = InventoryStore(self.db_path, now=clock)
        task = store.create_task("admin", "管理员", self.catalog())
        now_called.clear()
        blocker = store.connect()
        blocker.execute("BEGIN IMMEDIATE")
        result = []

        def claim():
            result.append(store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting"))

        worker = threading.Thread(target=claim)
        worker.start()
        self.assertFalse(now_called.wait(0.1))
        current[0] += timedelta(seconds=5)
        blocker.commit()
        blocker.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["expires_at"], "2026-09-01T10:02:05")

    def test_asserting_valid_item_commits_expiry_cleanup_for_another_item(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=60)
        store.claim_item(task["task_id"], "B2", "device-b", "乙", "counting")
        current[0] += timedelta(seconds=61)
        store.assert_item_lock(task["task_id"], "B2", "device-b", "counting")
        with sqlite3.connect(self.db_path) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM inventory_item_locks WHERE task_id = ? AND barcode = 'A1'", (task["task_id"],)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_audit_events WHERE task_id = ? AND event_type = 'lock_expired'",
                (task["task_id"],),
            ).fetchone()[0], 1)

    def test_assertion_waiting_for_write_lock_rechecks_expiry_after_acquiring_it(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=119)
        blocker = store.connect()
        blocker.execute("BEGIN IMMEDIATE")
        result = []
        assertion_ready = threading.Event()
        version_before = store.get_task_snapshot("admin", task["task_id"])["version"]

        def assert_lock():
            assertion_ready.set()
            try:
                store.assert_item_lock(task["task_id"], "A1", "device-a", "counting")
                result.append("accepted")
            except InventoryConflict:
                result.append("conflict")

        worker = threading.Thread(target=assert_lock)
        worker.start()
        self.assertTrue(assertion_ready.wait(1))
        # The worker is about to attempt BEGIN IMMEDIATE while the blocker remains open.
        current[0] += timedelta(seconds=2)
        blocker.commit()
        blocker.close()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["conflict"])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT version FROM inventory_tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()[0], version_before + 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_audit_events WHERE task_id = ? AND event_type = 'lock_expired'",
                (task["task_id"],),
            ).fetchone()[0], 1)

    def test_record_count_is_atomic_releases_lock_and_gates_serial_phase(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        store.claim_item(task["task_id"], "B2", "device-b", "乙", "counting")
        store.set_open_book_quantity(
            "admin", task["task_id"], "B2", "device-b", "乙", "0"
        )
        first = store.record_count(
            "admin", task["task_id"], "B2", "device-b", "乙",
            completed_book_qty="0", completed_actual_qty="1", diff_qty="1",
            state="serial_pending",
        )
        self.assertEqual(first["state"], "serial_pending")
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["phase"], "counting")
        with self.assertRaises(InventoryConflict):
            store.assert_item_lock(task["task_id"], "B2", "device-b", "counting")

        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        store.set_open_book_quantity(
            "admin", task["task_id"], "A1", "device-a", "甲", "2"
        )
        store.record_count(
            "admin", task["task_id"], "A1", "device-a", "甲",
            completed_book_qty="2", completed_actual_qty="2", diff_qty="0",
            state="matched",
        )
        self.assertEqual(store.get_task_snapshot("admin", task["task_id"])["phase"], "serial_check")

    def test_bulk_completed_stock_update_rolls_back_when_one_total_is_invalid(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        for barcode, device, book, actual, state in (
            ("A1", "device-a", "2", "1", "variance"),
            ("B2", "device-b", "0", "1", "serial_pending"),
        ):
            store.claim_item(task["task_id"], barcode, device, device, "counting")
            store.set_open_book_quantity("admin", task["task_id"], barcode, device, device, book)
            store.record_count(
                "admin", task["task_id"], barcode, device, device,
                completed_book_qty=book, completed_actual_qty=actual,
                diff_qty="-1" if barcode == "A1" else "1", state=state,
            )
        before = store.get_task_snapshot("admin", task["task_id"])
        with self.assertRaises(ValueError):
            store.update_completed_stock(
                "admin", task["task_id"], {"A1": "1", "B2": "not-a-number"},
                synced_at=datetime(2026, 9, 1, 10, 1, 0),
            )
        after = store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(
            [row["latest_book_quantity"] for row in after["items"]],
            [row["latest_book_quantity"] for row in before["items"]],
        )
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_stock_movements WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0], 0)

    def test_serial_store_guards_owner_phase_and_allows_any_device(self):
        store = InventoryStore(
            os.path.join(self.tempdir.name, "phase.sqlite3"),
            now=lambda: datetime(2026, 9, 1, 10, 0, 0),
        )
        task = store.create_task("admin", "管理员", self.catalog())
        with self.assertRaises(InventoryConflict):
            store.replace_expected_serials(
                "admin", task["task_id"], "B2", "device-a", "甲", []
            )

        store, task = self.serial_ready_store()
        expected = [
            {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
        ]
        refreshed = store.replace_expected_serials(
            "admin", task["task_id"], "B2", "device-a", "甲", expected
        )
        self.assertEqual(
            [(row["serial"], row["warehouse"]) for row in refreshed["system_only"]],
            [("B-1", "沈桥仓"), ("B-2", "其他仓")],
        )
        version = store.get_task_snapshot("admin", task["task_id"])["version"]
        invalid = [dict(expected[0], cost_price="100")]
        with self.assertRaisesRegex(ValueError, "字段"):
            store.replace_expected_serials(
                "admin", task["task_id"], "B2", "device-a", "甲", invalid
            )
        self.assertEqual(
            store.get_task_snapshot("admin", task["task_id"])["version"], version
        )
        with self.assertRaises(InventoryNotFound):
            store.add_serial_scan(
                "other", task["task_id"], "B2", "device-a", "乙",
                "UNKNOWN-1", "unknown",
            )
        scan = store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-b", "乙",
            "UNKNOWN-1", "unknown", expected_version=1,
        )
        self.assertEqual(scan["device_id"], "device-b")
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT serial, warehouse, sync_status FROM inventory_serial_expected "
                "WHERE task_id = ? ORDER BY serial",
                (task["task_id"],),
            ).fetchall()
            scan_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_scans WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(
            rows,
            [("B-1", "沈桥仓", "active"), ("B-2", "其他仓", "active")],
        )
        self.assertEqual(scan_count, 1)

    def test_serial_store_delete_rescan_completion_is_versioned_audited_and_immutable(self):
        store, task = self.serial_ready_store()
        store.claim_item(
            task["task_id"], "B2", "device-a", "甲", "serial_check"
        )
        version_before = store.get_task_snapshot("admin", task["task_id"])["version"]
        store.replace_expected_serials(
            "admin", task["task_id"], "B2", "device-a", "甲", [
                {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
                {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
            ],
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "B-1", "matched"
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "OTHER-1",
            "other_product",
            {"barcode": "C3", "name": "其他商品", "warehouse": "其他商品仓", "shipped": False},
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1", "unknown"
        )
        store.remove_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1"
        )
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1", "unknown"
        )
        finished = store.complete_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertEqual(finished["item"]["state"], "serial_complete")
        self.assertEqual([row["serial"] for row in finished["matched"]], ["B-1"])
        self.assertEqual([row["serial"] for row in finished["system_only"]], ["B-2"])
        self.assertEqual([row["serial"] for row in finished["physical_only"]], ["UNKNOWN-1"])
        self.assertEqual([row["serial"] for row in finished["other_product"]], ["OTHER-1"])
        self.assertEqual(
            store.get_task_snapshot("admin", task["task_id"])["version"],
            version_before + 7,
        )
        with self.assertRaises(InventoryConflict):
            store.remove_serial_scan(
                "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
            )
        with self.assertRaises(InventoryConflict):
            store.replace_expected_serials(
                "admin", task["task_id"], "B2", "device-a", "甲", []
            )
        self.assertEqual(
            store.serial_reconciliation("admin", task["task_id"], "B2")["counts"],
            finished["counts"],
        )
        with sqlite3.connect(self.db_path) as connection:
            active_counts = connection.execute(
                "SELECT serial, COUNT(*) FROM inventory_serial_scans "
                "WHERE task_id = ? AND active = 1 GROUP BY serial ORDER BY serial",
                (task["task_id"],),
            ).fetchall()
            events = {row[0] for row in connection.execute(
                "SELECT event_type FROM inventory_audit_events WHERE task_id = ?",
                (task["task_id"],),
            )}
            lock = connection.execute(
                "SELECT 1 FROM inventory_item_locks WHERE task_id = ? AND barcode = 'B2'",
                (task["task_id"],),
            ).fetchone()
        self.assertTrue(all(count == 1 for _, count in active_counts))
        self.assertTrue({
            "serial_expected_refreshed", "serial_scanned",
            "serial_scan_removed", "serial_item_completed",
        }.issubset(events))
        self.assertIsNone(lock)

    def test_completing_one_serial_item_keeps_task_open_while_another_is_pending(self):
        catalog = self.catalog() + [{
            "barcode": "C3", "name": "另一序列商品", "spec": "", "model": "",
            "category": "整机", "unit": "台", "has_serial": True,
            "initial_stock": "0",
        }]
        store, task = self.serial_ready_store(catalog)
        store.claim_item(
            task["task_id"], "B2", "device-a", "甲", "serial_check"
        )
        store.replace_expected_serials(
            "admin", task["task_id"], "B2", "device-a", "甲", []
        )
        store.complete_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        snapshot = store.get_task_snapshot("admin", task["task_id"])
        states = {row["barcode"]: row["state"] for row in snapshot["items"]}
        self.assertEqual(snapshot["phase"], "serial_check")
        self.assertEqual(states["B2"], "serial_complete")
        self.assertEqual(states["C3"], "serial_pending")

    def test_partial_counts_are_shared_summed_and_row_versioned(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        self.assertTrue(hasattr(store, "add_count_entry"))

        first = store.add_count_entry(
            "admin", task["task_id"], "A1", "甲", "device-a", "12", "30"
        )
        second = store.add_count_entry(
            "admin", task["task_id"], "A1", "乙", "device-b", "13", "30"
        )

        self.assertEqual(second["count_total"], "25")
        self.assertEqual(second["count_expression"], "12 + 13 = 25")
        self.assertEqual(
            [row["created_by"] for row in second["count_entries"]],
            ["甲", "乙"],
        )

        first_entry = first["count_entries"][0]
        edited = store.update_count_entry(
            "admin", task["task_id"], "A1",
            first_entry["entry_id"], first_entry["version"],
            "乙", "device-b", "14", "30",
        )
        self.assertEqual(edited["count_expression"], "14 + 13 = 27")
        self.assertEqual(edited["difference"], "-3")
        with self.assertRaises(InventoryVersionConflict):
            store.update_count_entry(
                "admin", task["task_id"], "A1",
                first_entry["entry_id"], first_entry["version"],
                "甲", "device-a", "15", "30",
            )

    def test_any_device_can_delete_count_entry_with_audit(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        self.assertTrue(hasattr(store, "add_count_entry"))
        created = store.add_count_entry(
            "admin", task["task_id"], "A1", "甲", "device-a", "12", "30"
        )
        entry = created["count_entries"][0]

        deleted = store.delete_count_entry(
            "admin", task["task_id"], "A1",
            entry["entry_id"], entry["version"],
            "乙", "device-b", "30",
        )

        self.assertEqual(deleted["count_entries"], [])
        self.assertIsNone(deleted["count_total"])
        self.assertEqual(deleted["state"], "pending")
        with sqlite3.connect(self.db_path) as connection:
            events = connection.execute(
                "SELECT event_type, actor, device_id, details "
                "FROM inventory_audit_events WHERE task_id = ? "
                "AND event_type LIKE 'count_entry_%' ORDER BY event_id",
                (task["task_id"],),
            ).fetchall()
        self.assertEqual(
            [(row[0], row[1], row[2]) for row in events],
            [
                ("count_entry_added", "甲", "device-a"),
                ("count_entry_deleted", "乙", "device-b"),
            ],
        )
        self.assertEqual(json.loads(events[0][3])["after"]["quantity"], "12")
        self.assertEqual(json.loads(events[1][3])["before"]["quantity"], "12")
        self.assertIsNone(json.loads(events[1][3])["after"])

    def test_audit_view_returns_allowlisted_before_after_fields(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        created = store.add_count_entry(
            "admin", task["task_id"], "A1", "甲", "device-a", "12", "2"
        )
        entry = created["count_entries"][0]
        store.update_count_entry(
            "admin", task["task_id"], "A1", entry["entry_id"],
            entry["version"], "乙", "device-b", "13", "2",
        )

        rows = store.list_audit_events("admin", task["task_id"], barcode="A1")

        self.assertEqual(
            [(row["event_label"], row["actor"], row["device_id"])
             for row in rows],
            [("新增分次数量", "甲", "device-a"),
             ("修改分次数量", "乙", "device-b")],
        )
        self.assertIsNone(rows[0]["before_quantity"])
        self.assertEqual(rows[0]["after_quantity"], "12")
        self.assertEqual(rows[1]["before_quantity"], "12")
        self.assertEqual(rows[1]["after_quantity"], "13")
        self.assertEqual([row["entry_number"] for row in rows], [1, 1])
        self.assertEqual(set(rows[0]), {
            "id", "barcode", "event_type", "event_label", "entry_number", "actor",
            "device_id", "created_at", "before_quantity", "after_quantity",
            "before_serial", "after_serial", "before_classification",
            "after_classification", "carton_id", "start_serial", "end_serial",
            "before_preset_quantity", "after_preset_quantity",
            "confirmed_quantity", "affected_count", "serials",
        })

    def test_audit_view_numbers_count_entries_stably(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", task["task_id"])
        first = store.add_count_entry(
            "admin", task["task_id"], "A1", "甲", "device-a", "12", "2"
        )["count_entries"][0]
        second = store.add_count_entry(
            "admin", task["task_id"], "A1", "乙", "device-b", "13", "2"
        )["count_entries"][1]
        updated_first = store.update_count_entry(
            "admin", task["task_id"], "A1", first["entry_id"],
            first["version"], "丙", "device-c", "14", "2",
        )["count_entries"]
        first_after_update = next(
            entry for entry in updated_first if entry["entry_id"] == first["entry_id"]
        )
        store.delete_count_entry(
            "admin", task["task_id"], "A1", first["entry_id"],
            first_after_update["version"], "丁", "device-d", "2",
        )
        remaining = store.get_task_snapshot("admin", task["task_id"])["items"][0]
        store.update_count_entry(
            "admin", task["task_id"], "A1", second["entry_id"],
            remaining["count_entries"][0]["version"], "戊", "device-e", "15", "2",
        )

        rows = store.list_audit_events("admin", task["task_id"], barcode="A1")

        self.assertEqual([row.get("entry_number") for row in rows], [1, 2, 1, 1, 2])

    def completed_difference_store(self):
        store, task = self.serial_ready_store()
        task_id = task["task_id"]
        store.claim_item(task_id, "B2", "device-a", "甲", "serial_check")
        store.replace_expected_serials(
            "admin", task_id, "B2", "device-a", "甲", [{
                "serial": "SYSTEM-1", "barcode": "B2", "name": "零库存商品",
                "warehouse": "沈桥仓", "shipped": False,
            }]
        )
        for serial, classification, lookup in (
            (
                "OTHER-1", "other_product",
                {"barcode": "C3", "name": "其他商品", "warehouse": "其他仓", "shipped": False},
            ),
            (
                "SHIPPED-1", "already_shipped",
                {"barcode": "B2", "name": "零库存商品", "warehouse": "沈桥仓", "shipped": True},
            ),
            ("UNKNOWN-1", "unknown", None),
        ):
            store.add_serial_scan(
                "admin", task_id, "B2", "device-a", "甲", serial,
                classification, lookup,
            )
        with store.connect() as connection:
            connection.execute(
                """INSERT INTO inventory_serial_scans
                   (task_id, barcode, serial, classification,
                    source_classification, device_id, actor, scanned_at, active)
                   VALUES (?, 'B2', 'PHYSICAL-1', 'physical_only',
                           'physical_only', 'device-a', '甲', ?, 1)""",
                (task_id, "2026-09-01T10:00:00"),
            )
        store.complete_serial_item(
            "admin", task_id, "B2", "device-a", "甲"
        )
        return store, task

    def test_completion_materializes_every_final_kind_once_and_omits_zero_rows(self):
        store, task = self.completed_difference_store()

        completed = store.complete_task("admin", task["task_id"], "管理员")
        first_rows = store.list_discrepancies("admin", "open")
        repeated = store.complete_task("admin", task["task_id"], "管理员")
        second_rows = store.list_discrepancies("admin", "open")

        self.assertTrue(completed["completed"])
        self.assertEqual(repeated["version"], completed["version"])
        self.assertEqual(
            {row["kind"] for row in first_rows},
            {
                "product_quantity", "system_only_serial",
                "physical_only_serial", "other_product_serial",
                "already_shipped_serial", "unknown_serial",
            },
        )
        self.assertEqual(
            [row["id"] for row in second_rows],
            [row["id"] for row in first_rows],
        )
        product_rows = [
            row for row in first_rows if row["kind"] == "product_quantity"
        ]
        self.assertEqual(len(product_rows), 1)
        self.assertEqual(
            (
                product_rows[0]["barcode"], product_rows[0]["book_quantity"],
                product_rows[0]["counted_quantity"], product_rows[0]["difference"],
            ),
            ("B2", "0", "1", "1"),
        )
        self.assertNotIn("price", "|".join(
            key.lower() for row in first_rows for key in row
        ))

    def test_partial_completion_requires_serial_confirmation_and_excludes_uncounted(self):
        catalog = [
            {"barcode": "U", "name": "未盘", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "1"},
            {"barcode": "M", "name": "一致", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "1"},
            {"barcode": "V", "name": "数量差异", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "2"},
            {"barcode": "S", "name": "序列号未核对", "spec": "", "model": "", "category": "设备", "unit": "台", "has_serial": True, "initial_stock": "2"},
        ]
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", catalog)
        store.advance_count_phase_if_ready("admin", task["task_id"])
        for barcode, quantity in (("M", "1"), ("V", "1"), ("S", "1")):
            store.add_count_entry(
                "admin", task["task_id"], barcode,
                "盘点员", "device-a", quantity, catalog[[
                    row["barcode"] for row in catalog
                ].index(barcode)]["initial_stock"],
            )

        with self.assertRaises(InventoryConfirmationRequired) as raised:
            store.complete_task("admin", task["task_id"], "盘点员")
        self.assertEqual(raised.exception.pending_serial_count, 1)
        self.assertNotEqual(
            store.get_task_snapshot("admin", task["task_id"])["phase"],
            "completed",
        )
        self.assertEqual(store.list_discrepancies("admin", "open"), [])

        completed = store.complete_task(
            "admin", task["task_id"], "盘点员",
            allow_unverified_serials=True,
        )
        self.assertTrue(completed["completed"])
        self.assertEqual(completed["counted_items"], 3)
        self.assertEqual(completed["uncounted_items"], 1)
        self.assertEqual(completed["unverified_serial_items"], 1)
        discrepancies = store.list_discrepancies("admin", "open")
        self.assertNotIn("U", {row["barcode"] for row in discrepancies})
        self.assertEqual(
            {(row["barcode"], row["kind"], row["serial"]) for row in discrepancies},
            {
                ("V", "product_quantity", None),
                ("S", "product_quantity", None),
                ("S", "serial_unverified", None),
            },
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            details = connection.execute(
                "SELECT details FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'task_completed'",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(
            json.loads(details),
            {
                "allow_unverified_serials": True,
                "counted_items": 3,
                "discrepancies": 3,
                "uncounted_items": 1,
                "unverified_serial_items": 1,
            },
        )

    def test_completion_ignores_stale_scans_after_serial_item_returns_to_matched(self):
        catalog = [{
            "barcode": "S", "name": "序列号商品", "spec": "", "model": "",
            "category": "设备", "unit": "台", "has_serial": True,
            "initial_stock": "1",
        }]
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", catalog)
        store.advance_count_phase_if_ready("admin", task["task_id"])
        counted = store.add_count_entry(
            "admin", task["task_id"], "S", "甲", "device-a", "0", "1"
        )
        store.add_serial_scan(
            "admin", task["task_id"], "S", "device-a", "甲", "OLD-SCAN",
            "unknown",
        )
        entry = counted["count_entries"][0]
        store.update_count_entry(
            "admin", task["task_id"], "S", entry["entry_id"], entry["version"],
            "乙", "device-b", "1", "1",
        )

        store.complete_task("admin", task["task_id"], "管理员")

        self.assertEqual(store.list_discrepancies("admin", "open"), [])

    def test_unverified_completion_does_not_emit_incomplete_scan_classifications(self):
        catalog = [{
            "barcode": "S", "name": "序列号商品", "spec": "", "model": "",
            "category": "设备", "unit": "台", "has_serial": True,
            "initial_stock": "2",
        }]
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", catalog)
        store.advance_count_phase_if_ready("admin", task["task_id"])
        store.add_count_entry(
            "admin", task["task_id"], "S", "甲", "device-a", "1", "2"
        )
        store.add_serial_scan(
            "admin", task["task_id"], "S", "device-a", "甲", "UNKNOWN-1",
            "unknown",
        )

        store.complete_task(
            "admin", task["task_id"], "管理员",
            allow_unverified_serials=True,
        )

        self.assertEqual(
            {row["kind"] for row in store.list_discrepancies("admin", "open")},
            {"product_quantity", "serial_unverified"},
        )

    def test_serial_audit_includes_before_after_for_add_reclassify_and_remove(self):
        store, task = self.serial_ready_store()
        store.add_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "SN-1",
            "unknown",
        )
        store.replace_expected_serials(
            "admin", task["task_id"], "B2", "device-b", "乙", [{
                "serial": "SN-1", "barcode": "B2", "name": "零库存商品",
                "warehouse": "沈桥仓", "shipped": False,
            }],
        )
        store.remove_serial_scan(
            "admin", task["task_id"], "B2", "device-c", "丙", "SN-1"
        )

        rows = [
            row for row in store.list_audit_events(
                "admin", task["task_id"], barcode="B2"
            ) if row["event_type"] in {
                "serial_scanned", "serial_scan_reclassified",
                "serial_scan_removed",
            }
        ]

        self.assertEqual(
            [(row["event_type"], row["actor"], row["device_id"])
             for row in rows],
            [
                ("serial_scanned", "甲", "device-a"),
                ("serial_scan_reclassified", "乙", "device-b"),
                ("serial_scan_removed", "丙", "device-c"),
            ],
        )
        self.assertIsNone(rows[0]["before_serial"])
        self.assertEqual(rows[0]["after_serial"], "SN-1")
        self.assertEqual(rows[0]["after_classification"], "unknown")
        self.assertEqual(rows[1]["before_classification"], "unknown")
        self.assertEqual(rows[1]["after_classification"], "matched")
        self.assertEqual(rows[2]["before_serial"], "SN-1")
        self.assertIsNone(rows[2]["after_serial"])

    def test_admin_reopens_completed_task_without_losing_entries(self):
        store, task = self.completed_difference_store()
        store.complete_task("admin", task["task_id"], "管理员")
        discrepancy = store.list_discrepancies("admin", "open")[0]
        store.add_discrepancy_note(
            "admin", discrepancy["id"], "管理员", "保留备注"
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            scan_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_scans WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0]

        reopened = store.reopen_task(
            "admin", task["task_id"], "管理员",
            {"A1": "3", "B2": "1"},
            synced_at=datetime(2026, 9, 1, 11, 0, 0),
        )

        self.assertEqual(reopened["phase"], "counting")
        self.assertIsNone(reopened["completed_at"])
        self.assertEqual(reopened["last_sync_at"], "2026-09-01T11:00:00")
        restarted = InventoryStore(self.db_path).get_active_task("admin")
        self.assertEqual(restarted["task_id"], task["task_id"])
        snapshot = store.get_task_snapshot("admin", task["task_id"])
        items = {row["barcode"]: row for row in snapshot["items"]}
        self.assertEqual(items["A1"]["count_expression"], "2")
        self.assertEqual(items["A1"]["latest_book_qty"], "3")
        self.assertEqual(items["A1"]["diff_qty"], "-1")
        self.assertEqual(items["B2"]["count_expression"], "1")
        self.assertEqual(items["B2"]["latest_book_qty"], "1")
        self.assertEqual(items["B2"]["state"], "matched")
        self.assertEqual(store.list_discrepancies("admin", "open"), [])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_scans WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0], scan_count)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_notes WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0], 1)
            audit = connection.execute(
                "SELECT details FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'task_reopened'",
                (task["task_id"],),
            ).fetchone()[0]
        self.assertEqual(
            json.loads(audit),
            {"from_phase": "completed", "to_phase": "counting"},
        )

    def test_reopen_is_atomic_when_another_task_is_active(self):
        store = InventoryStore(self.db_path)
        target = store.create_task("admin", "管理员", self.catalog())
        store.advance_count_phase_if_ready("admin", target["task_id"])
        store.complete_task("admin", target["task_id"], "管理员")
        active = store.create_task("admin", "管理员", self.catalog())
        before = store.get_task_snapshot("admin", target["task_id"])

        with self.assertRaisesRegex(InventoryConflict, "先完成当前任务"):
            store.reopen_task(
                "admin", target["task_id"], "管理员",
                {"A1": "2", "B2": "0"},
            )

        after = store.get_task_snapshot("admin", target["task_id"])
        self.assertEqual(after["phase"], "completed")
        self.assertEqual(after["version"], before["version"])
        self.assertEqual(store.get_active_task("admin")["task_id"], active["task_id"])

    def test_notes_are_append_only_and_archive_restore_only_change_metadata(self):
        store, task = self.completed_difference_store()
        completed = store.complete_task("admin", task["task_id"], "管理员")
        rows = store.list_discrepancies("admin", "open")
        product = next(row for row in rows if row["kind"] == "product_quantity")
        serial_row = next(
            row for row in rows if row["kind"] == "system_only_serial"
        )
        original = {
            key: serial_row[key]
            for key in (
                "task_id", "barcode", "serial", "kind", "book_quantity",
                "counted_quantity", "difference", "created_at",
            )
        }

        product_note = store.add_discrepancy_note(
            "admin", product["id"], "仓管员", "商品位置待核对"
        )
        serial_note = store.add_discrepancy_note(
            "admin", product["id"], "仓管员", "已找到，放错仓位",
            serial="SYSTEM-1",
        )
        store.add_discrepancy_note(
            "admin", serial_row["id"], "仓管员", "等待管理员确认",
            serial="SYSTEM-1",
        )
        version_after_notes = store.get_task_snapshot(
            "admin", task["task_id"]
        )["version"]
        with self.assertRaises(InventoryPermissionDenied):
            store.archive_discrepancy(
                "admin", serial_row["id"], "仓管员", False
            )
        before_archive = store.get_task_snapshot(
            "admin", task["task_id"]
        )["version"]
        archived = store.archive_discrepancy(
            "admin", serial_row["id"], "管理员", True
        )
        repeated_archive = store.archive_discrepancy(
            "admin", serial_row["id"], "管理员", True
        )
        with self.assertRaises(InventoryPermissionDenied):
            store.restore_discrepancy(
                "admin", serial_row["id"], "仓管员", False
            )
        restored = store.restore_discrepancy(
            "admin", serial_row["id"], "管理员", True
        )
        repeated_restore = store.restore_discrepancy(
            "admin", serial_row["id"], "管理员", True
        )

        self.assertEqual(product_note["serial"], None)
        self.assertEqual(serial_note["serial"], "SYSTEM-1")
        self.assertEqual(serial_note["note"], "已找到，放错仓位")
        self.assertEqual(version_after_notes, completed["version"] + 3)
        self.assertEqual(before_archive, version_after_notes)
        self.assertEqual(archived["state"], "archived")
        self.assertEqual(repeated_archive, archived)
        self.assertEqual(restored["state"], "open")
        self.assertEqual(repeated_restore, restored)
        self.assertEqual(restored["archived_by"], None)
        self.assertEqual(restored["archived_at"], None)
        self.assertEqual(
            store.get_task_snapshot("admin", task["task_id"])["version"],
            before_archive + 2,
        )
        reloaded = InventoryStore(self.db_path, now=store.now)
        reloaded_serial = next(
            row for row in reloaded.list_discrepancies("admin", "open")
            if row["id"] == serial_row["id"]
        )
        self.assertEqual(
            {key: reloaded_serial[key] for key in original}, original
        )
        self.assertEqual(
            [note["note"] for note in reloaded_serial["notes"]],
            ["已找到，放错仓位", "等待管理员确认"],
        )
        reloaded_product = next(
            row for row in reloaded.list_discrepancies("admin", "open")
            if row["id"] == product["id"]
        )
        self.assertEqual(
            [note["note"] for note in reloaded_product["notes"]],
            ["商品位置待核对"],
        )
        with sqlite3.connect(self.db_path) as connection:
            events = connection.execute(
                """SELECT event_type FROM inventory_audit_events
                   WHERE task_id = ? AND event_type IN (
                       'discrepancy_archived', 'discrepancy_restored')
                   ORDER BY event_id""",
                (task["task_id"],),
            ).fetchall()
        self.assertEqual(
            events, [("discrepancy_archived",), ("discrepancy_restored",)]
        )

    def test_discrepancy_inputs_queries_and_owner_boundaries_are_strict(self):
        store, task = self.completed_difference_store()
        store.complete_task("admin", task["task_id"], "管理员")
        rows = store.list_discrepancies("admin", "open")
        product = next(row for row in rows if row["kind"] == "product_quantity")

        self.assertEqual(store.list_discrepancies("other", "open"), [])
        self.assertEqual(
            [row["serial"] for row in store.list_discrepancies(
                "admin", "open", " system-1 "
            )],
            ["SYSTEM-1"],
        )
        self.assertEqual(
            {row["barcode"] for row in store.list_discrepancies(
                "admin", "open", "零库存"
            )},
            {"B2"},
        )
        with self.assertRaisesRegex(ValueError, "差异状态"):
            store.list_discrepancies("admin", "pending")
        with self.assertRaisesRegex(ValueError, "备注不能为空"):
            store.add_discrepancy_note(
                "admin", product["id"], "仓管员", "   "
            )
        with self.assertRaises(InventoryNotFound):
            store.add_discrepancy_note(
                "admin", product["id"], "仓管员", "找到了",
                serial="MISSING",
            )
        with self.assertRaises(InventoryNotFound):
            store.add_discrepancy_note(
                "other", product["id"], "其他用户", "越权备注"
            )
        with self.assertRaises(InventoryNotFound):
            store.archive_discrepancy(
                "other", product["id"], "其他管理员", True
            )
        for missing_id in (0, 999999):
            with self.assertRaises(InventoryNotFound):
                store.add_discrepancy_note(
                    "admin", missing_id, "仓管员", "不存在"
                )
            with self.assertRaises(InventoryNotFound):
                store.archive_discrepancy(
                    "admin", missing_id, "管理员", True
                )

    def test_history_page_aggregates_counts_and_distinct_participating_devices(self):
        store = InventoryStore(self.db_path)
        with store.connect() as connection:
            tasks = [
                ("older", "admin", "创建者", "2026-09-01T08:00:00", "2026-09-01T09:00:00"),
                ("newer", "admin", "创建者", "2026-09-02T08:00:00", "2026-09-02T09:00:00"),
                ("other", "other", "其他人", "2026-09-03T08:00:00", "2026-09-03T09:00:00"),
            ]
            connection.executemany(
                """INSERT INTO inventory_tasks
                   (task_id, owner, created_by, phase, started_at, completed_at)
                   VALUES (?, ?, ?, 'completed', ?, ?)""",
                tasks,
            )
            items = [
                ("newer", "A1", "商品一", "1"),
                ("newer", "B2", "商品二", "0"),
                ("older", "A1", "商品一", "1"),
            ]
            connection.executemany(
                """INSERT INTO inventory_items
                   (task_id, barcode, name, difference, updated_at)
                   VALUES (?, ?, ?, ?, '2026-09-03T10:00:00')""",
                items,
            )
            connection.executemany(
                """INSERT INTO inventory_discrepancies
                   (task_id, barcode, serial, kind, status, created_at)
                   VALUES (?, ?, ?, ?, 'open', '2026-09-03T10:00:00')""",
                [
                    ("newer", "A1", "SN-1", "system_only_serial"),
                    ("newer", "A1", "SN-2", "system_only_serial"),
                    ("newer", "B2", None, "serial_unverified"),
                ],
            )
            connection.executemany(
                """INSERT INTO inventory_audit_events
                   (task_id, event_type, actor, device_id, created_at)
                   VALUES ('newer', 'item_counted', ?, ?, '2026-09-03T10:00:00')""",
                [("甲", "device-a"), ("乙", "device-b"), ("丙", "device-a"), ("丁", "")],
            )

        first_page = store.list_task_history("admin", limit=1, offset=0)
        second_page = store.list_task_history("admin", limit=1, offset=1)

        self.assertEqual(first_page["total"], 2)
        self.assertEqual(first_page["limit"], 1)
        self.assertEqual(first_page["offset"], 0)
        self.assertEqual([row["task_id"] for row in first_page["tasks"]], ["newer"])
        self.assertEqual(
            {
                key: first_page["tasks"][0][key]
                for key in (
                    "product_total", "quantity_difference_count",
                    "serial_difference_count", "participant_count",
                )
            },
            {
                "product_total": 2,
                "quantity_difference_count": 1,
                "serial_difference_count": 3,
                "participant_count": 2,
            },
        )
        self.assertEqual(second_page["tasks"][0]["participant_count"], 1)
        self.assertEqual(store.list_task_history("other")["total"], 1)
