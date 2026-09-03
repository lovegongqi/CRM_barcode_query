"""Service-layer transitions for live GYJ inventory counting."""

from contextlib import contextmanager
from datetime import datetime
import threading

from inventory_store import (
    InventoryConflict,
    InventoryNotFound,
    normalize_quantity,
    quantity_difference,
)


class InventoryServiceError(RuntimeError):
    pass


class InventoryService:
    def __init__(self, store, worker_provider, now=None):
        self.store = store
        self.worker_provider = worker_provider
        self.now = now or datetime.now
        self._sync_guards_lock = threading.Lock()
        self._sync_guards = {}

    @contextmanager
    def _operation_guard(self, key):
        with self._sync_guards_lock:
            entry = self._sync_guards.get(key)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._sync_guards[key] = entry
            entry[1] += 1
        entry[0].acquire()
        try:
            yield
        finally:
            entry[0].release()
            with self._sync_guards_lock:
                entry[1] -= 1
                if entry[1] == 0:
                    del self._sync_guards[key]

    @contextmanager
    def _completed_sync_guard(self, owner, task_id):
        with self._operation_guard(("completed_stock", owner, task_id)):
            yield

    @contextmanager
    def _serial_guard(self, owner, task_id, barcode):
        with self._operation_guard(("serial", owner, task_id, barcode)):
            yield

    @staticmethod
    def _worker_value(result):
        if not isinstance(result, tuple) or len(result) != 2:
            raise InventoryServiceError("GYJ 返回结果格式不正确")
        ok, value = result
        if not ok:
            raise InventoryServiceError(str(value or "GYJ 读取失败"))
        return value

    def _snapshot(self, owner, task_id, phases=None):
        snapshot = self.store.get_task_snapshot(owner, task_id)
        if phases is not None and snapshot["phase"] not in phases:
            raise InventoryConflict("当前任务阶段不允许此操作")
        return snapshot

    @staticmethod
    def _item(snapshot, barcode):
        for item in snapshot["items"]:
            if item["barcode"] == barcode:
                return item
        raise InventoryNotFound("商品不存在")

    def create_task(self, owner, actor):
        worker = self.worker_provider(owner)
        catalog = self._worker_value(worker.load_inventory_catalog())
        task = self.store.create_task(owner, actor, catalog)
        return self.store.advance_count_phase_if_ready(owner, task["task_id"])

    def open_count_item(self, owner, task_id, barcode, device_id, actor):
        snapshot = self._snapshot(owner, task_id, {"counting"})
        item = self._item(snapshot, barcode)
        if item["completed_actual_qty"] is not None:
            raise InventoryConflict("商品数量盘点已完成")
        self.store.claim_item(task_id, barcode, device_id, actor, "counting")
        worker = self.worker_provider(owner)
        value = self._worker_value(worker.read_inventory_stock(barcode))
        try:
            book_quantity = normalize_quantity(value)
        except ValueError as exc:
            raise InventoryServiceError("GYJ 返回的库存数量无效") from exc
        return self.store.set_open_book_quantity(
            owner, task_id, barcode, device_id, actor, book_quantity
        )

    def submit_count(
        self, owner, task_id, barcode, device_id, actor, actual_qty
    ):
        snapshot = self._snapshot(owner, task_id, {"counting"})
        item = self._item(snapshot, barcode)
        if item["completed_actual_qty"] is not None:
            raise InventoryConflict("商品数量盘点已完成")
        actual_qty = normalize_quantity(actual_qty)
        self.store.assert_item_lock(task_id, barcode, device_id, "counting")
        worker = self.worker_provider(owner)
        value = self._worker_value(worker.read_inventory_stock(barcode))
        try:
            latest_book_qty = normalize_quantity(value)
        except ValueError as exc:
            raise InventoryServiceError("GYJ 返回的库存数量无效") from exc
        diff_qty = quantity_difference(actual_qty, latest_book_qty)
        state = (
            "matched" if diff_qty == "0"
            else ("serial_pending" if item["has_serial"] else "variance")
        )
        return self.store.record_count(
            owner, task_id, barcode, device_id, actor,
            completed_book_qty=latest_book_qty,
            completed_actual_qty=actual_qty,
            diff_qty=diff_qty,
            state=state,
        )

    def _mark_sync_error(self, owner, task_id, message, timestamp):
        self.store.update_completed_stock(
            owner, task_id, error=message, synced_at=timestamp
        )
        raise InventoryServiceError(message)

    def sync_completed_items(self, owner, task_id, force=False):
        with self._completed_sync_guard(owner, task_id):
            return self._sync_completed_items_locked(owner, task_id, force)

    def _sync_completed_items_locked(self, owner, task_id, force):
        snapshot = self._snapshot(
            owner, task_id, {"counting", "serial_check", "sync_error"}
        )
        check_time = self.now()
        if (
            not force
            and snapshot["phase"] != "sync_error"
            and snapshot["last_sync_at"]
        ):
            last_sync = datetime.fromisoformat(snapshot["last_sync_at"])
            if (check_time - last_sync).total_seconds() < 60:
                return {
                    "skipped": True,
                    "phase": snapshot["phase"],
                    "last_sync_at": snapshot["last_sync_at"],
                    "items": snapshot["items"],
                }

        worker = self.worker_provider(owner)
        try:
            totals = self._worker_value(worker.read_inventory_stock_totals())
        except InventoryServiceError as exc:
            self._mark_sync_error(owner, task_id, str(exc), self.now())
        if not isinstance(totals, dict):
            self._mark_sync_error(
                owner, task_id, "GYJ 库存汇总结果格式不正确", self.now()
            )

        normalized_totals = {}
        for barcode, value in totals.items():
            try:
                normalized_totals[barcode] = normalize_quantity(value)
            except ValueError:
                self._mark_sync_error(
                    owner, task_id, f"GYJ 返回的库存数量无效: {barcode}", self.now()
                )
        try:
            return self.store.update_completed_stock(
                owner, task_id, normalized_totals, synced_at=self.now()
            )
        except ValueError as exc:
            self._mark_sync_error(owner, task_id, str(exc), self.now())

    @staticmethod
    def _validated_expected_serials(value, barcode):
        if not isinstance(value, list):
            raise InventoryServiceError("GYJ 序列号库存结果格式不正确")
        allowed_keys = {"serial", "barcode", "name", "warehouse", "shipped"}
        rows = []
        seen = set()
        for raw in value:
            if not isinstance(raw, dict) or set(raw) != allowed_keys:
                raise InventoryServiceError("GYJ 序列号库存字段不正确")
            serial = str(raw["serial"] or "").strip()
            row_barcode = str(raw["barcode"] or "").strip()
            if (
                not serial
                or row_barcode != barcode
                or raw["shipped"] is not False
                or serial in seen
            ):
                raise InventoryServiceError("GYJ 序列号库存内容不正确")
            seen.add(serial)
            rows.append({
                "serial": serial,
                "barcode": row_barcode,
                "name": str(raw["name"] or ""),
                "warehouse": str(raw["warehouse"] or ""),
                "shipped": False,
            })
        return rows

    @staticmethod
    def _validated_serial_lookup(value, serial):
        if value is None:
            return None
        allowed_keys = {"serial", "barcode", "name", "warehouse", "shipped"}
        if not isinstance(value, dict) or set(value) != allowed_keys:
            raise InventoryServiceError("GYJ 序列号查询结果格式不正确")
        result_serial = str(value["serial"] or "").strip()
        result_barcode = str(value["barcode"] or "").strip()
        if (
            result_serial != serial
            or not result_barcode
            or not isinstance(value["shipped"], bool)
        ):
            raise InventoryServiceError("GYJ 序列号查询结果内容不正确")
        return {
            "serial": result_serial,
            "barcode": result_barcode,
            "name": str(value["name"] or ""),
            "warehouse": str(value["warehouse"] or ""),
            "shipped": value["shipped"],
        }

    def _serial_item(self, owner, task_id, barcode):
        snapshot = self._snapshot(owner, task_id, {"serial_check"})
        item = self._item(snapshot, barcode)
        if item["state"] != "serial_pending":
            raise InventoryConflict("商品不在待核对序列号状态")
        return item

    def _refresh_serial_item_locked(
        self, owner, task_id, barcode, device_id, actor, force
    ):
        self._serial_item(owner, task_id, barcode)
        self.store.assert_item_lock(task_id, barcode, device_id, "serial_check")
        current = self.store.serial_reconciliation(owner, task_id, barcode)
        check_time = self.now()
        last_sync_at = current["item"].get("serial_synced_at")
        if not force and last_sync_at:
            last_sync = datetime.fromisoformat(last_sync_at)
            if (check_time - last_sync).total_seconds() < 60:
                current["skipped"] = True
                return current

        worker = self.worker_provider(owner)
        value = self._worker_value(worker.read_inventory_serials(barcode))
        rows = self._validated_expected_serials(value, barcode)
        result = self.store.replace_expected_serials(
            owner, task_id, barcode, device_id, actor, rows,
            synced_at=self.now(),
        )
        result["skipped"] = False
        return result

    def open_serial_item(self, owner, task_id, barcode, device_id, actor):
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            self.store.claim_item(
                task_id, barcode, device_id, actor, "serial_check"
            )
            try:
                return self._refresh_serial_item_locked(
                    owner, task_id, barcode, device_id, actor, True
                )
            except Exception:
                self.store.release_item_lock(
                    owner, task_id, barcode, device_id, actor,
                    "serial_check", "serial_open_failed",
                )
                raise

    def refresh_serial_item(
        self, owner, task_id, barcode, device_id, actor, force=False
    ):
        with self._serial_guard(owner, task_id, barcode):
            return self._refresh_serial_item_locked(
                owner, task_id, barcode, device_id, actor, force
            )

    def scan_serial(
        self, owner, task_id, barcode, device_id, actor, serial
    ):
        serial = str(serial or "").strip()
        if not serial:
            raise ValueError("序列号不能为空")
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            self.store.assert_item_lock(
                task_id, barcode, device_id, "serial_check"
            )
            current = self.store.serial_reconciliation(owner, task_id, barcode)
            active = (
                current["matched"]
                + current["physical_only"]
                + current["other_product"]
            )
            for row in active:
                if row["serial"] == serial:
                    return self.store.add_serial_scan(
                        owner, task_id, barcode, device_id, actor, serial,
                        row["classification"],
                    )

            expected = {
                row["serial"] for row in current["expected"]
            }
            lookup = None
            if serial in expected:
                classification = "matched"
            else:
                worker = self.worker_provider(owner)
                value = self._worker_value(
                    worker.lookup_inventory_serial(serial)
                )
                lookup = self._validated_serial_lookup(value, serial)
                if lookup is None:
                    classification = "unknown"
                elif lookup["barcode"] != barcode:
                    classification = "other_product"
                elif lookup["shipped"]:
                    classification = "already_shipped"
                else:
                    classification = "unknown"
            return self.store.add_serial_scan(
                owner, task_id, barcode, device_id, actor, serial,
                classification, lookup,
            )

    def delete_serial_scan(
        self, owner, task_id, barcode, device_id, actor, serial
    ):
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            self.store.assert_item_lock(
                task_id, barcode, device_id, "serial_check"
            )
            return self.store.remove_serial_scan(
                owner, task_id, barcode, device_id, actor, serial
            )

    def finish_serial_item(
        self, owner, task_id, barcode, device_id, actor
    ):
        with self._serial_guard(owner, task_id, barcode):
            self._refresh_serial_item_locked(
                owner, task_id, barcode, device_id, actor, True
            )
            return self.store.complete_serial_item(
                owner, task_id, barcode, device_id, actor
            )
