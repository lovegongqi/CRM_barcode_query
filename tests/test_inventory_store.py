import os
import sqlite3
import tempfile
import unittest

from inventory_store import InventoryConflict, InventoryStore, normalize_quantity, quantity_difference


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
        }.issubset(names))

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
