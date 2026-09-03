import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta

from inventory_service import InventoryService, InventoryServiceError
from inventory_store import InventoryConflict, InventoryNotFound, InventoryStore


class FakeInventoryWorker:
    def __init__(self):
        self.stock = {"A1": "2", "B2": "0", "C3": "5"}
        self.catalog = [
            {
                "barcode": "A1", "name": "普通商品", "spec": "", "model": "",
                "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "2",
            },
            {
                "barcode": "B2", "name": "序列商品", "spec": "", "model": "",
                "category": "整机", "unit": "台", "has_serial": True, "initial_stock": "0",
            },
        ]
        self.catalog_result = None
        self.stock_failures = {}
        self.totals_result = None
        self.serials = {}
        self.lookup = {}
        self.serial_failures = {}
        self.lookup_failures = {}
        self.catalog_reads = 0
        self.stock_reads = []
        self.totals_reads = 0
        self.serial_reads = []
        self.lookup_reads = []
        self.on_stock_read = None
        self.on_totals_read = None
        self.on_serial_read = None

    def load_inventory_catalog(self):
        self.catalog_reads += 1
        return self.catalog_result or (True, self.catalog)

    def read_inventory_stock(self, barcode):
        self.stock_reads.append(barcode)
        if self.on_stock_read:
            self.on_stock_read(barcode)
        if barcode in self.stock_failures:
            return False, self.stock_failures[barcode]
        return True, self.stock[barcode]

    def read_inventory_stock_totals(self):
        self.totals_reads += 1
        if self.on_totals_read:
            self.on_totals_read()
        return self.totals_result or (True, dict(self.stock))

    def read_inventory_serials(self, barcode):
        self.serial_reads.append(barcode)
        if self.on_serial_read:
            self.on_serial_read(barcode)
        if barcode in self.serial_failures:
            return False, self.serial_failures[barcode]
        return True, list(self.serials.get(barcode, []))

    def lookup_inventory_serial(self, serial):
        self.lookup_reads.append(serial)
        if serial in self.lookup_failures:
            return False, self.lookup_failures[serial]
        return True, self.lookup.get(serial)


class InventoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "inventory.sqlite3")
        self.current = [datetime(2026, 9, 1, 10, 0, 0)]
        self.now = lambda: self.current[0]
        self.store = InventoryStore(self.db_path, now=self.now)
        self.worker = FakeInventoryWorker()
        self.service = InventoryService(self.store, lambda owner: self.worker, now=self.now)

    def tearDown(self):
        self.tempdir.cleanup()

    def create_task(self):
        return self.service.create_task("admin", "管理员")

    def submit(self, task_id, barcode, actual_qty, device_id="device-a"):
        self.service.open_count_item("admin", task_id, barcode, device_id, "甲")
        return self.service.submit_count(
            "admin", task_id, barcode, device_id, "甲", actual_qty
        )

    def create_serial_task(self):
        task = self.create_task()
        self.submit(task["task_id"], "B2", "1", device_id="device-b")
        self.submit(task["task_id"], "A1", "1")
        return task

    def test_serial_phase_classifies_missing_extra_other_and_duplicate(self):
        task = self.create_serial_task()
        self.worker.serials = {"B2": [
            {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
        ]}
        self.worker.lookup = {
            "OTHER-1": {"serial": "OTHER-1", "barcode": "C3", "name": "其他商品", "warehouse": "沈桥仓", "shipped": False},
        }

        detail = self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertEqual(
            [(row["serial"], row["warehouse"]) for row in detail["system_only"]],
            [("B-1", "沈桥仓"), ("B-2", "其他仓")],
        )
        self.assertEqual(
            self.service.scan_serial(
                "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
            )["classification"],
            "matched",
        )
        self.assertEqual(
            self.service.scan_serial(
                "admin", task["task_id"], "B2", "device-a", "甲", "OTHER-1"
            )["classification"],
            "other_product",
        )
        duplicate = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
        )
        self.assertEqual(duplicate["classification"], "duplicate")
        result = self.service.finish_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertIn("B-2", [row["serial"] for row in result["system_only"]])

    def test_duplicate_serial_does_not_lookup_or_insert_second_active_row(self):
        task = self.create_serial_task()
        self.worker.serials = {"B2": [
            {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
        ]}
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
        )
        version_after_first = self.store.get_task_snapshot(
            "admin", task["task_id"]
        )["version"]

        duplicate = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
        )

        self.assertEqual(duplicate["classification"], "duplicate")
        self.assertEqual(self.worker.lookup_reads, [])
        self.assertEqual(
            self.store.get_task_snapshot("admin", task["task_id"])["version"],
            version_after_first,
        )
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_scans "
                "WHERE task_id = ? AND barcode = 'B2' AND serial = 'B-1' AND active = 1",
                (task["task_id"],),
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'serial_scan_duplicate'",
                (task["task_id"],),
            ).fetchone()[0], 1)

    def test_serial_open_claims_before_read_and_failure_releases_without_progress(self):
        task = self.create_serial_task()

        def assert_serial_lock(barcode):
            with sqlite3.connect(self.db_path) as connection:
                lock = connection.execute(
                    "SELECT device_id, phase FROM inventory_item_locks "
                    "WHERE task_id = ? AND barcode = ?",
                    (task["task_id"], barcode),
                ).fetchone()
            self.assertEqual(lock, ("device-a", "serial_check"))

        self.worker.on_serial_read = assert_serial_lock
        self.worker.serial_failures["B2"] = "序列号报表暂时不可用"
        version_before = self.store.get_task_snapshot(
            "admin", task["task_id"]
        )["version"]

        with self.assertRaisesRegex(InventoryServiceError, "序列号报表暂时不可用"):
            self.service.open_serial_item(
                "admin", task["task_id"], "B2", "device-a", "甲"
            )

        snapshot = self.store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(snapshot["phase"], "serial_check")
        self.assertEqual(
            next(row for row in snapshot["items"] if row["barcode"] == "B2")["state"],
            "serial_pending",
        )
        self.assertEqual(snapshot["version"], version_before + 2)
        with sqlite3.connect(self.db_path) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM inventory_item_locks WHERE task_id = ? AND barcode = 'B2'",
                (task["task_id"],),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_serial_expected WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0], 0)
            released = connection.execute(
                "SELECT details FROM inventory_audit_events "
                "WHERE task_id = ? AND event_type = 'lock_released'",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(released, ("serial_open_failed",))

    def test_serial_guards_reject_invalid_phase_owner_item_and_device_before_reads(self):
        task = self.create_task()
        with self.assertRaises(InventoryConflict):
            self.service.open_serial_item(
                "admin", task["task_id"], "B2", "device-a", "甲"
            )
        self.assertEqual(self.worker.serial_reads, [])

        self.submit(task["task_id"], "B2", "1", device_id="device-b")
        self.submit(task["task_id"], "A1", "1")
        self.worker.serials["B2"] = []
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        reads_before = len(self.worker.serial_reads)
        with self.assertRaises(InventoryNotFound):
            self.service.refresh_serial_item(
                "other", task["task_id"], "B2", "device-a", "乙"
            )
        with self.assertRaises(InventoryConflict):
            self.service.refresh_serial_item(
                "admin", task["task_id"], "B2", "device-b", "乙"
            )
        with self.assertRaises(InventoryConflict):
            self.service.open_serial_item(
                "admin", task["task_id"], "A1", "device-c", "丙"
            )
        self.assertEqual(len(self.worker.serial_reads), reads_before)

    def test_serial_refresh_uses_59_60_boundary_reclassifies_and_finish_forces(self):
        task = self.create_serial_task()
        self.worker.serials["B2"] = [
            {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
        ]
        opened = self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertFalse(opened["skipped"])
        self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
        )
        self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "NEW-1"
        )
        self.worker.serials["B2"] = [
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
            {"serial": "NEW-1", "barcode": "B2", "name": "序列商品", "warehouse": "新仓", "shipped": False},
        ]

        self.current[0] += timedelta(seconds=59)
        skipped = self.service.refresh_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertTrue(skipped["skipped"])
        self.assertEqual(self.worker.serial_reads, ["B2"])
        self.assertEqual([row["serial"] for row in skipped["matched"]], ["B-1"])
        self.assertEqual([row["serial"] for row in skipped["physical_only"]], ["NEW-1"])

        self.current[0] += timedelta(seconds=1)
        refreshed = self.service.refresh_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertFalse(refreshed["skipped"])
        self.assertEqual(self.worker.serial_reads, ["B2", "B2"])
        self.assertEqual([row["serial"] for row in refreshed["matched"]], ["NEW-1"])
        self.assertEqual([row["serial"] for row in refreshed["physical_only"]], ["B-1"])
        self.assertEqual(
            [(row["serial"], row["warehouse"]) for row in refreshed["system_only"]],
            [("B-2", "其他仓")],
        )

        self.current[0] += timedelta(seconds=1)
        self.worker.serials["B2"] = [
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "完成仓", "shipped": False},
        ]
        finished = self.service.finish_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.assertEqual(self.worker.serial_reads, ["B2", "B2", "B2"])
        self.assertEqual(
            [(row["serial"], row["warehouse"]) for row in finished["system_only"]],
            [("B-2", "完成仓")],
        )
        self.assertEqual(
            [row["serial"] for row in finished["physical_only"]],
            ["B-1", "NEW-1"],
        )
        self.assertEqual(finished["item"]["state"], "serial_complete")
        self.assertEqual(finished["phase"], "serial_check")
        with self.assertRaises(InventoryConflict):
            self.service.delete_serial_scan(
                "admin", task["task_id"], "B2", "device-a", "甲", "B-1"
            )

    def test_serial_delete_rescan_and_lookup_failures_preserve_active_state(self):
        task = self.create_serial_task()
        self.worker.serials["B2"] = []
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        first = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1"
        )
        self.assertEqual(first["classification"], "unknown")
        removed = self.service.delete_serial_scan(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1"
        )
        self.assertEqual(removed["physical_only"], [])
        second = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "UNKNOWN-1"
        )
        self.assertEqual(second["classification"], "unknown")
        self.assertEqual(self.worker.lookup_reads, ["UNKNOWN-1", "UNKNOWN-1"])

        version_before_error = self.store.get_task_snapshot(
            "admin", task["task_id"]
        )["version"]
        self.worker.lookup_failures["BROKEN-1"] = "序列号查询失败"
        with self.assertRaisesRegex(InventoryServiceError, "序列号查询失败"):
            self.service.scan_serial(
                "admin", task["task_id"], "B2", "device-a", "甲", "BROKEN-1"
            )
        self.assertEqual(
            self.store.get_task_snapshot("admin", task["task_id"])["version"],
            version_before_error,
        )
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute(
                "SELECT serial, active FROM inventory_serial_scans "
                "WHERE task_id = ? ORDER BY scan_id",
                (task["task_id"],),
            ).fetchall()
        self.assertEqual(rows, [("UNKNOWN-1", 0), ("UNKNOWN-1", 1)])

    def test_serial_lookup_categories_retain_source_fields(self):
        task = self.create_serial_task()
        self.worker.serials["B2"] = []
        self.worker.lookup = {
            "OTHER-1": {"serial": "OTHER-1", "barcode": "C3", "name": "其他商品", "warehouse": "其他商品仓", "shipped": False},
            "SHIPPED-1": {"serial": "SHIPPED-1", "barcode": "B2", "name": "序列商品", "warehouse": "已出库仓", "shipped": True},
        }
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        other = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "OTHER-1"
        )
        shipped = self.service.scan_serial(
            "admin", task["task_id"], "B2", "device-a", "甲", "SHIPPED-1"
        )
        self.assertEqual(
            (other["classification"], other["lookup_barcode"], other["lookup_name"], other["warehouse"], other["shipped"]),
            ("other_product", "C3", "其他商品", "其他商品仓", False),
        )
        self.assertEqual(
            (shipped["classification"], shipped["lookup_barcode"], shipped["warehouse"], shipped["shipped"]),
            ("already_shipped", "B2", "已出库仓", True),
        )
        detail = self.store.serial_reconciliation("admin", task["task_id"], "B2")
        self.assertEqual([row["serial"] for row in detail["other_product"]], ["OTHER-1"])
        self.assertEqual([row["serial"] for row in detail["physical_only"]], ["SHIPPED-1"])

    def test_finish_serial_item_refuses_stale_data_when_forced_refresh_fails(self):
        task = self.create_serial_task()
        self.worker.serials["B2"] = []
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        self.worker.serial_failures["B2"] = "完成前刷新失败"

        with self.assertRaisesRegex(InventoryServiceError, "完成前刷新失败"):
            self.service.finish_serial_item(
                "admin", task["task_id"], "B2", "device-a", "甲"
            )

        snapshot = self.store.get_task_snapshot("admin", task["task_id"])
        item = next(row for row in snapshot["items"] if row["barcode"] == "B2")
        self.assertEqual(item["state"], "serial_pending")
        self.assertEqual(snapshot["phase"], "serial_check")
        self.store.assert_item_lock(
            task["task_id"], "B2", "device-a", "serial_check"
        )

    def test_create_task_reads_catalog_and_enters_counting(self):
        task = self.create_task()
        self.assertEqual(task["phase"], "counting")
        self.assertEqual(self.worker.catalog_reads, 1)
        self.assertEqual(
            [row["barcode"] for row in self.store.get_task_snapshot("admin", task["task_id"])["items"]],
            ["A1", "B2"],
        )

    def test_worker_false_results_abort_without_false_progress(self):
        self.worker.catalog_result = (False, "GYJ 未登录")
        with self.assertRaisesRegex(InventoryServiceError, "GYJ 未登录"):
            self.create_task()
        self.assertIsNone(self.store.get_active_task("admin"))

        self.worker.catalog_result = None
        task = self.create_task()
        self.worker.stock_failures["A1"] = "库存查询失败"
        with self.assertRaisesRegex(InventoryServiceError, "库存查询失败"):
            self.service.open_count_item("admin", task["task_id"], "A1", "device-a", "甲")
        item = self.store.get_task_snapshot("admin", task["task_id"])["items"][0]
        self.assertIsNone(item["completed_counted_quantity"])

    def test_open_claims_before_live_read_and_saves_open_book(self):
        task = self.create_task()

        def assert_claimed(barcode):
            with sqlite3.connect(self.db_path) as connection:
                lock = connection.execute(
                    "SELECT device_id, phase FROM inventory_item_locks WHERE task_id = ? AND barcode = ?",
                    (task["task_id"], barcode),
                ).fetchone()
            self.assertEqual(lock, ("device-a", "counting"))

        self.worker.on_stock_read = assert_claimed
        row = self.service.open_count_item(
            "admin", task["task_id"], "A1", "device-a", "甲"
        )
        self.assertEqual(row["open_book_qty"], "2")

    def test_owner_and_task_are_validated_before_worker_reads(self):
        task = self.create_task()
        with self.assertRaises(InventoryNotFound):
            self.service.open_count_item(
                "other", task["task_id"], "A1", "device-a", "乙"
            )
        with self.assertRaises(InventoryNotFound):
            self.service.open_count_item(
                "admin", "missing-task", "A1", "device-a", "甲"
            )
        self.assertEqual(self.worker.stock_reads, [])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM inventory_item_locks").fetchone()[0], 0)

    def test_submit_uses_a_second_live_read_and_routes_variances(self):
        task = self.create_task()
        self.service.open_count_item("admin", task["task_id"], "A1", "device-a", "甲")
        self.worker.stock["A1"] = "1"
        row = self.service.submit_count(
            "admin", task["task_id"], "A1", "device-a", "甲", "1"
        )
        self.assertEqual(self.worker.stock_reads, ["A1", "A1"])
        self.assertEqual(row["completed_book_qty"], "1")
        self.assertEqual(row["state"], "matched")

        self.worker.stock["A1"] = "2"
        task2_store = InventoryStore(
            os.path.join(self.tempdir.name, "inventory-2.sqlite3"), now=self.now
        )
        service2 = InventoryService(task2_store, lambda owner: self.worker, now=self.now)
        task2 = service2.create_task("admin", "管理员")
        service2.open_count_item("admin", task2["task_id"], "A1", "device-a", "甲")
        mismatch = service2.submit_count(
            "admin", task2["task_id"], "A1", "device-a", "甲", "1"
        )
        self.assertEqual(mismatch["state"], "variance")
        self.assertEqual(mismatch["diff_qty"], "-1")

    def test_precise_decimal_difference_is_preserved(self):
        self.worker.catalog[0]["initial_stock"] = "0.2"
        self.worker.stock["A1"] = "0.2"
        task = self.create_task()
        row = self.submit(task["task_id"], "A1", "0.3")
        self.assertEqual(row["completed_book_qty"], "0.2")
        self.assertEqual(row["completed_actual_qty"], "0.3")
        self.assertEqual(row["diff_qty"], "0.1")

    def test_serial_phase_waits_for_every_quantity_and_only_includes_serial_mismatch(self):
        task = self.create_task()
        first = self.submit(task["task_id"], "B2", "1", device_id="device-b")
        self.assertEqual(first["state"], "serial_pending")
        self.assertEqual(
            self.store.get_task_snapshot("admin", task["task_id"])["phase"],
            "counting",
        )
        second = self.submit(task["task_id"], "A1", "1")
        self.assertEqual(second["state"], "variance")
        self.assertEqual(
            self.store.get_task_snapshot("admin", task["task_id"])["phase"],
            "serial_check",
        )

    def test_all_submitted_without_serial_pending_remains_counting(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        self.submit(task["task_id"], "B2", "0", device_id="device-b")
        snapshot = self.store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual([row["state"] for row in snapshot["items"]], ["matched", "matched"])
        self.assertEqual(snapshot["phase"], "counting")

    def test_complete_task_allows_counting_without_serial_pending_and_is_idempotent(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        self.submit(task["task_id"], "B2", "0", device_id="device-b")
        before = self.store.get_task_snapshot("admin", task["task_id"])

        completed = self.service.complete_task(
            "admin", task["task_id"], "管理员"
        )
        repeated = self.service.complete_task(
            "admin", task["task_id"], "管理员"
        )

        self.assertTrue(completed["completed"])
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(completed["version"], before["version"] + 1)
        self.assertEqual(repeated["version"], completed["version"])
        self.assertEqual(self.store.list_discrepancies("admin", "open"), [])
        self.assertIsNone(self.store.get_active_task("admin"))

    def test_complete_task_rejects_unfinished_quantity_or_serial_work(self):
        task = self.create_task()
        with self.assertRaises(InventoryConflict):
            self.service.complete_task("admin", task["task_id"], "管理员")
        self.submit(task["task_id"], "B2", "1", device_id="device-b")
        with self.assertRaises(InventoryConflict):
            self.service.complete_task("admin", task["task_id"], "管理员")
        self.submit(task["task_id"], "A1", "2")
        with self.assertRaises(InventoryConflict):
            self.service.complete_task("admin", task["task_id"], "管理员")
        self.assertEqual(
            self.store.get_task_snapshot("admin", task["task_id"])["phase"],
            "serial_check",
        )
        self.assertEqual(self.store.list_discrepancies("admin", "open"), [])

    def test_completion_preserves_service_serial_classifications(self):
        task = self.create_serial_task()
        self.worker.serials["B2"] = [{
            "serial": "SYSTEM-1", "barcode": "B2", "name": "序列商品",
            "warehouse": "沈桥仓", "shipped": False,
        }]
        self.worker.lookup = {
            "OTHER-1": {
                "serial": "OTHER-1", "barcode": "C3", "name": "其他商品",
                "warehouse": "其他仓", "shipped": False,
            },
            "SHIPPED-1": {
                "serial": "SHIPPED-1", "barcode": "B2", "name": "序列商品",
                "warehouse": "沈桥仓", "shipped": True,
            },
        }
        self.service.open_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )
        for serial in ("OTHER-1", "SHIPPED-1", "UNKNOWN-1"):
            self.service.scan_serial(
                "admin", task["task_id"], "B2", "device-a", "甲", serial
            )
        self.service.finish_serial_item(
            "admin", task["task_id"], "B2", "device-a", "甲"
        )

        self.service.complete_task("admin", task["task_id"], "管理员")
        rows = self.store.list_discrepancies("admin", "open")

        self.assertTrue({
            "product_quantity", "system_only_serial", "other_product_serial",
            "already_shipped_serial", "unknown_serial",
        }.issubset({row["kind"] for row in rows}))

    def test_history_is_owner_scoped_newest_first_and_survives_restart(self):
        first = self.create_task()
        self.submit(first["task_id"], "A1", "2")
        self.submit(first["task_id"], "B2", "0", device_id="device-b")
        first_completed = self.service.complete_task(
            "admin", first["task_id"], "管理员"
        )
        self.current[0] += timedelta(seconds=1)
        second = self.create_task()
        self.submit(second["task_id"], "A1", "2")
        self.submit(second["task_id"], "B2", "0", device_id="device-b")
        second_completed = self.service.complete_task(
            "admin", second["task_id"], "管理员"
        )

        history = InventoryStore(
            self.db_path, now=self.now
        ).list_task_history("admin")

        self.assertEqual(
            [row["task_id"] for row in history],
            [second["task_id"], first["task_id"]],
        )
        self.assertEqual(history[0]["version"], second_completed["version"])
        self.assertEqual(history[1]["version"], first_completed["version"])
        self.assertTrue(all(row["completed"] for row in history))
        self.assertEqual(
            InventoryStore(self.db_path).list_task_history("other"), []
        )

    def test_submit_failure_keeps_lock_and_success_releases_it(self):
        task = self.create_task()
        self.service.open_count_item("admin", task["task_id"], "A1", "device-a", "甲")
        self.worker.stock_failures["A1"] = "暂时无法读取"
        with self.assertRaisesRegex(InventoryServiceError, "暂时无法读取"):
            self.service.submit_count(
                "admin", task["task_id"], "A1", "device-a", "甲", "2"
            )
        self.store.assert_item_lock(task["task_id"], "A1", "device-a", "counting")

        del self.worker.stock_failures["A1"]
        self.service.submit_count(
            "admin", task["task_id"], "A1", "device-a", "甲", "2"
        )
        with self.assertRaises(InventoryConflict):
            self.store.assert_item_lock(task["task_id"], "A1", "device-a", "counting")

    def test_submit_rejects_wrong_device_before_second_read(self):
        task = self.create_task()
        self.service.open_count_item("admin", task["task_id"], "A1", "device-a", "甲")
        reads_before = len(self.worker.stock_reads)
        with self.assertRaises(InventoryConflict):
            self.service.submit_count(
                "admin", task["task_id"], "A1", "device-b", "乙", "2"
            )
        self.assertEqual(len(self.worker.stock_reads), reads_before)

    def test_sync_uses_59_60_second_boundary_and_force_override(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        first = self.service.sync_completed_items("admin", task["task_id"])
        self.assertFalse(first["skipped"])
        self.assertEqual(self.worker.totals_reads, 1)

        self.current[0] += timedelta(seconds=59)
        skipped = self.service.sync_completed_items("admin", task["task_id"])
        self.assertTrue(skipped["skipped"])
        self.assertEqual(self.worker.totals_reads, 1)

        self.current[0] += timedelta(seconds=1)
        boundary = self.service.sync_completed_items("admin", task["task_id"])
        self.assertFalse(boundary["skipped"])
        self.assertEqual(self.worker.totals_reads, 2)

        self.current[0] += timedelta(seconds=1)
        forced = self.service.sync_completed_items("admin", task["task_id"], force=True)
        self.assertFalse(forced["skipped"])
        self.assertEqual(self.worker.totals_reads, 3)

    def test_sync_updates_expected_quantity_and_records_only_real_movements(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "1")
        unchanged = self.service.sync_completed_items("admin", task["task_id"])
        self.assertEqual(unchanged["movement_count"], 0)

        self.worker.stock["A1"] = "1"
        changed = self.service.sync_completed_items("admin", task["task_id"], force=True)
        self.assertEqual(changed["movement_count"], 1)
        item = next(row for row in changed["items"] if row["barcode"] == "A1")
        self.assertEqual(item["latest_book_qty"], "1")
        self.assertEqual(item["expected_current_qty"], "0")
        self.assertEqual(item["diff_qty"], "-1")

        repeated = self.service.sync_completed_items("admin", task["task_id"], force=True)
        self.assertEqual(repeated["movement_count"], 0)
        with sqlite3.connect(self.db_path) as connection:
            movement = connection.execute(
                "SELECT before_quantity, after_quantity, change_quantity "
                "FROM inventory_stock_movements WHERE task_id = ?",
                (task["task_id"],),
            ).fetchall()
        self.assertEqual(movement, [("2", "1", "-1")])

    def test_reader_omitted_zero_stock_row_syncs_as_exact_zero(self):
        task = self.create_task()
        self.submit(task["task_id"], "B2", "1", device_id="device-b")
        self.worker.totals_result = (True, {})
        result = self.service.sync_completed_items("admin", task["task_id"], force=True)
        item = next(row for row in result["items"] if row["barcode"] == "B2")
        self.assertFalse(result["skipped"])
        self.assertEqual(item["latest_book_qty"], "0")
        self.assertEqual(item["expected_current_qty"], "1")
        self.assertEqual(result["movement_count"], 0)

    def test_malformed_totals_fail_closed_and_success_recovers_phase(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "1")
        self.submit(task["task_id"], "B2", "1", device_id="device-b")
        before = self.store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(before["phase"], "serial_check")
        before_by_barcode = {row["barcode"]: row for row in before["items"]}

        self.worker.totals_result = (True, {"A1": "1", "B2": "not-a-number"})
        with self.assertRaisesRegex(InventoryServiceError, "B2"):
            self.service.sync_completed_items("admin", task["task_id"], force=True)
        failed = self.store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(failed["phase"], "sync_error")
        self.assertIsNone(failed["last_sync_at"])
        for row in failed["items"]:
            self.assertEqual(
                row["latest_book_quantity"],
                before_by_barcode[row["barcode"]]["latest_book_quantity"],
            )
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM inventory_stock_movements WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()[0], 0)

        self.worker.totals_result = (True, {"A1": "1"})
        recovered = self.service.sync_completed_items("admin", task["task_id"])
        self.assertFalse(recovered["skipped"])
        self.assertEqual(recovered["phase"], "serial_check")

    def test_failed_totals_read_preserves_progress_and_marks_sync_error(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        self.worker.totals_result = (False, "GYJ 库存报表暂时不可用")
        with self.assertRaisesRegex(InventoryServiceError, "GYJ 库存报表暂时不可用"):
            self.service.sync_completed_items("admin", task["task_id"], force=True)
        failed = self.store.get_task_snapshot("admin", task["task_id"])
        self.assertEqual(failed["phase"], "sync_error")
        self.assertEqual(failed["gyj_status"], "GYJ 库存报表暂时不可用")
        self.assertEqual(
            next(row for row in failed["items"] if row["barcode"] == "A1")["state"],
            "matched",
        )

    def test_sync_includes_item_completed_while_totals_are_being_read(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        self.service.open_count_item(
            "admin", task["task_id"], "B2", "device-b", "乙"
        )

        def finish_second_item():
            self.store.record_count(
                "admin", task["task_id"], "B2", "device-b", "乙",
                completed_book_qty="0", completed_actual_qty="1",
                diff_qty="1", state="serial_pending",
            )

        self.worker.on_totals_read = finish_second_item
        result = self.service.sync_completed_items(
            "admin", task["task_id"], force=True
        )
        self.assertFalse(result["skipped"])
        self.assertEqual(result["phase"], "serial_check")
        self.assertEqual(self.worker.totals_reads, 1)

    def test_sync_window_starts_when_the_live_read_finishes(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")

        def finish_slow_read():
            self.current[0] += timedelta(seconds=10)

        self.worker.on_totals_read = finish_slow_read
        result = self.service.sync_completed_items("admin", task["task_id"])
        self.assertEqual(result["last_sync_at"], "2026-09-01T10:00:10")

        self.worker.on_totals_read = None
        self.current[0] += timedelta(seconds=59)
        self.assertTrue(
            self.service.sync_completed_items("admin", task["task_id"])["skipped"]
        )

    def test_concurrent_non_forced_syncs_share_one_eligibility_check(self):
        task = self.create_task()
        self.submit(task["task_id"], "A1", "2")
        start = threading.Barrier(3)
        second_read_entered = threading.Event()
        call_lock = threading.Lock()
        call_count = [0]

        def hold_first_read_until_second_attempts():
            with call_lock:
                call_count[0] += 1
                position = call_count[0]
            if position == 1:
                second_read_entered.wait(0.5)
            else:
                second_read_entered.set()

        self.worker.on_totals_read = hold_first_read_until_second_attempts
        results = []
        errors = []

        def sync():
            start.wait()
            try:
                results.append(
                    self.service.sync_completed_items("admin", task["task_id"])
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=sync) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(2)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.worker.totals_reads, 1)
        self.assertEqual(sorted(row["skipped"] for row in results), [False, True])

    def test_different_tasks_can_sync_while_another_task_read_is_blocked(self):
        other_worker = FakeInventoryWorker()
        workers = {"admin": self.worker, "other": other_worker}
        service = InventoryService(self.store, workers.__getitem__, now=self.now)
        first_task = service.create_task("admin", "管理员")
        second_task = service.create_task("other", "仓管员")
        for owner, task, device in (
            ("admin", first_task, "device-a"),
            ("other", second_task, "device-b"),
        ):
            service.open_count_item(owner, task["task_id"], "A1", device, owner)
            service.submit_count(owner, task["task_id"], "A1", device, owner, "2")

        first_read_started = threading.Event()
        release_first_read = threading.Event()

        def block_first_read():
            first_read_started.set()
            release_first_read.wait(1)

        self.worker.on_totals_read = block_first_read
        errors = []
        second_finished = threading.Event()

        def first_sync():
            try:
                service.sync_completed_items("admin", first_task["task_id"])
            except Exception as exc:
                errors.append(exc)

        def second_sync():
            try:
                service.sync_completed_items("other", second_task["task_id"])
            except Exception as exc:
                errors.append(exc)
            finally:
                second_finished.set()

        first_thread = threading.Thread(target=first_sync)
        second_thread = threading.Thread(target=second_sync)
        first_thread.start()
        self.assertTrue(first_read_started.wait(1))
        second_thread.start()
        try:
            self.assertTrue(second_finished.wait(0.5))
        finally:
            release_first_read.set()
            first_thread.join(2)
            second_thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(self.worker.totals_reads, 1)
        self.assertEqual(other_worker.totals_reads, 1)


if __name__ == "__main__":
    unittest.main()
