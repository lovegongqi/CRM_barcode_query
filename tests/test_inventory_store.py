import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from inventory_store import (
    InventoryConflict,
    InventoryPermissionDenied,
    InventoryStore,
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
