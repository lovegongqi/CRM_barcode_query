import os
import sqlite3
import tempfile
import unittest

from inventory_store import InventoryStore, normalize_quantity, quantity_difference


class InventoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "inventory.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

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
