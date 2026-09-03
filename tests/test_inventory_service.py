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
        self.catalog_reads = 0
        self.stock_reads = []
        self.totals_reads = 0
        self.on_stock_read = None
        self.on_totals_read = None

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
