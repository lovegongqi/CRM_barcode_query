"""Service-layer transitions for live GYJ inventory counting."""

from contextlib import contextmanager
from datetime import datetime
import threading

from inventory_store import (
    InventoryConflict,
    InventoryNotFound,
    InventoryPermissionDenied,
    InventoryVersionConflict,
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

    def _call_worker(self, owner, method_name, *args):
        try:
            worker = self.worker_provider(owner)
            result = getattr(worker, method_name)(*args)
        except (
            InventoryConflict,
            InventoryNotFound,
            InventoryPermissionDenied,
            InventoryServiceError,
        ):
            raise
        except Exception as exc:
            raise InventoryServiceError("GYJ 读取失败") from exc
        return self._worker_value(result)

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
        catalog = self._call_worker(owner, "load_inventory_catalog")
        task = self.store.create_task(owner, actor, catalog)
        return self.store.advance_count_phase_if_ready(owner, task["task_id"])

    def _live_stock(self, owner, barcode):
        value = self._call_worker(owner, "read_inventory_stock", barcode)
        try:
            return normalize_quantity(value)
        except ValueError as exc:
            raise InventoryServiceError("GYJ 返回的库存数量无效") from exc

    def _partial_count_item(self, owner, task_id, barcode):
        snapshot = self._snapshot(owner, task_id, {"counting", "serial_check"})
        item = self._item(snapshot, barcode)
        if item.get("data_error"):
            raise InventoryConflict("商品资料异常，不可盘点")
        return snapshot, item

    def open_count_item(
        self, owner, task_id, barcode, device_or_actor, actor=None, *,
        expected_version=None,
    ):
        if actor is None:
            snapshot, item = self._partial_count_item(owner, task_id, barcode)
            book_quantity = self._live_stock(owner, barcode)
            result = dict(item)
            result.update({
                "book_quantity": book_quantity,
                "open_book_qty": book_quantity,
                "latest_book_quantity": book_quantity,
                "latest_book_qty": book_quantity,
                "difference": (
                    quantity_difference(item["count_total"], book_quantity)
                    if item.get("count_total") is not None else None
                ),
            })
            result["diff_qty"] = result["difference"]
            result["task_version"] = snapshot["version"]
            return result

        device_id = device_or_actor
        snapshot = self._snapshot(owner, task_id, {"counting"})
        item = self._item(snapshot, barcode)
        if item.get("data_error"):
            raise InventoryConflict("商品资料异常，不可盘点")
        if item["completed_actual_qty"] is not None:
            raise InventoryConflict("商品数量盘点已完成")
        lock = self.store.claim_item(
            task_id, barcode, device_id, actor, "counting",
            expected_version=expected_version,
        )
        value = self._call_worker(owner, "read_inventory_stock", barcode)
        try:
            book_quantity = normalize_quantity(value)
        except ValueError as exc:
            raise InventoryServiceError("GYJ 返回的库存数量无效") from exc
        return self.store.set_open_book_quantity(
            owner, task_id, barcode, device_id, actor, book_quantity,
            expected_version=lock["task_version"],
        )

    def submit_count(
        self, owner, task_id, barcode, device_id, actor, actual_qty, *,
        expected_version=None,
    ):
        snapshot = self._snapshot(owner, task_id, {"counting"})
        item = self._item(snapshot, barcode)
        if item.get("data_error"):
            raise InventoryConflict("商品资料异常，不可盘点")
        if item["completed_actual_qty"] is not None:
            raise InventoryConflict("商品数量盘点已完成")
        actual_qty = normalize_quantity(actual_qty)
        checked_version = self.store.assert_item_lock(
            task_id, barcode, device_id, "counting", actor,
            expected_version=expected_version,
        )
        value = self._call_worker(owner, "read_inventory_stock", barcode)
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
            expected_version=checked_version,
        )

    def add_count_entry(
        self, owner, task_id, barcode, device_id, actor, quantity
    ):
        self._partial_count_item(owner, task_id, barcode)
        book_quantity = self._live_stock(owner, barcode)
        return self.store.add_count_entry(
            owner, task_id, barcode, actor, device_id,
            quantity, book_quantity,
        )

    def update_count_entry(
        self, owner, task_id, barcode, entry_id, entry_version,
        device_id, actor, quantity
    ):
        self._partial_count_item(owner, task_id, barcode)
        book_quantity = self._live_stock(owner, barcode)
        return self.store.update_count_entry(
            owner, task_id, barcode, entry_id, entry_version,
            actor, device_id, quantity, book_quantity,
        )

    def delete_count_entry(
        self, owner, task_id, barcode, entry_id, entry_version,
        device_id, actor
    ):
        self._partial_count_item(owner, task_id, barcode)
        book_quantity = self._live_stock(owner, barcode)
        return self.store.delete_count_entry(
            owner, task_id, barcode, entry_id, entry_version,
            actor, device_id, book_quantity,
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

        try:
            totals = self._call_worker(owner, "read_inventory_stock_totals")
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
    def _carton_quantity(value):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 999
        ):
            raise ValueError("每箱数量必须是1至999之间的整数")
        return value

    @staticmethod
    def _carton_text(value, label):
        if not isinstance(value, str):
            raise ValueError(f"{label}格式不正确")
        value = value.strip()
        if (
            not value
            or len(value) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"{label}格式不正确")
        return value

    @classmethod
    def _carton_serials(cls, value):
        if not isinstance(value, list) or not value or len(value) > 999:
            raise ValueError("本箱序列号必须是1至999条")
        serials = [cls._carton_text(serial, "序列号") for serial in value]
        if len(set(serials)) != len(serials):
            raise ValueError("本箱序列号存在重复")
        return serials

    def _serial_item(self, owner, task_id, barcode):
        snapshot = self._snapshot(owner, task_id, {"counting", "serial_check"})
        item = self._item(snapshot, barcode)
        if item["state"] != "serial_pending":
            raise InventoryConflict("商品不在待核对序列号状态")
        return item

    def _refresh_serial_item_locked(
        self, owner, task_id, barcode, device_id, actor, force,
        expected_version=None,
    ):
        self._serial_item(owner, task_id, barcode)
        current = self.store.serial_reconciliation(owner, task_id, barcode)
        last_sync_at = current["item"].get("serial_synced_at")
        if not force and last_sync_at:
            current["skipped"] = True
            return current

        value = self._call_worker(owner, "read_inventory_serials", barcode)
        rows = self._validated_expected_serials(value, barcode)
        result = self.store.replace_expected_serials(
            owner, task_id, barcode, device_id, actor, rows,
            synced_at=self.now(),
        )
        result["skipped"] = False
        return result

    def open_serial_item(
        self, owner, task_id, barcode, device_id, actor, *,
        expected_version=None,
    ):
        with self._serial_guard(owner, task_id, barcode):
            snapshot = self._snapshot(owner, task_id, {"counting", "serial_check"})
            item = self._item(snapshot, barcode)
            if item["state"] == "serial_complete":
                self.store.reopen_serial_item(
                    owner, task_id, barcode, device_id, actor,
                )
            elif item["state"] != "serial_pending":
                raise InventoryConflict("商品不在待核对序列号状态")
            return self._refresh_serial_item_locked(
                owner, task_id, barcode, device_id, actor, False,
            )

    def refresh_serial_item(
        self, owner, task_id, barcode, device_id, actor, force=False, *,
        expected_version=None,
    ):
        with self._serial_guard(owner, task_id, barcode):
            return self._refresh_serial_item_locked(
                owner, task_id, barcode, device_id, actor, force,
                expected_version,
            )

    def scan_serial(
        self, owner, task_id, barcode, device_id, actor, serial, *,
        expected_version=None,
    ):
        serial = str(serial or "").strip()
        if not serial:
            raise ValueError("序列号不能为空")
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
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
            classification = "matched" if serial in expected else "unknown"
            return self.store.add_serial_scan(
                owner, task_id, barcode, device_id, actor, serial,
                classification,
            )

    def delete_serial_scan(
        self, owner, task_id, barcode, device_id, actor, serial, *,
        expected_version=None,
    ):
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.remove_serial_scan(
                owner, task_id, barcode, device_id, actor, serial,
            )

    def save_carton_preset(
        self, owner, task_id, barcode, device_id, actor, carton_quantity,
    ):
        carton_quantity = self._carton_quantity(carton_quantity)
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.save_carton_preset(
                owner, task_id, barcode, device_id, actor, carton_quantity,
            )

    def create_serial_carton(
        self, owner, task_id, barcode, device_id, actor, preset_quantity,
        start_serial, serials,
    ):
        preset_quantity = self._carton_quantity(preset_quantity)
        start_serial = self._carton_text(start_serial, "起始序列号")
        serials = self._carton_serials(serials)
        if serials[0] != start_serial:
            raise ValueError("起始序列号必须与预览第一条一致")
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.create_serial_carton(
                owner, task_id, barcode, device_id, actor, preset_quantity,
                start_serial, serials,
            )

    def add_carton_serial(
        self, owner, task_id, barcode, device_id, actor, carton_id, serial,
    ):
        serial = self._carton_text(serial, "序列号")
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.add_carton_serial(
                owner, task_id, barcode, device_id, actor, carton_id, serial,
            )

    def remove_carton_serial(
        self, owner, task_id, barcode, device_id, actor, carton_id, serial,
    ):
        serial = self._carton_text(serial, "序列号")
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.remove_carton_serial(
                owner, task_id, barcode, device_id, actor, carton_id, serial,
            )

    def delete_serial_carton(
        self, owner, task_id, barcode, device_id, actor, carton_id,
    ):
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            return self.store.delete_serial_carton(
                owner, task_id, barcode, device_id, actor, carton_id,
            )

    def finish_serial_item(
        self, owner, task_id, barcode, device_id, actor, *,
        expected_version=None,
    ):
        with self._serial_guard(owner, task_id, barcode):
            self._serial_item(owner, task_id, barcode)
            current = self.store.serial_reconciliation(owner, task_id, barcode)
            if not current["item"].get("serial_synced_at"):
                raise InventoryConflict("请先刷新账面序列号")
            return self.store.complete_serial_item(
                owner, task_id, barcode, device_id, actor,
            )

    def complete_task(
        self, owner, task_id, actor, *, allow_unverified_serials=False,
        expected_version=None,
    ):
        snapshot = self.store.get_task_snapshot(owner, task_id)
        if (
            expected_version is not None
            and int(snapshot["version"]) != int(expected_version)
        ):
            raise InventoryVersionConflict(
                "盘点任务已被其他设备更新，请刷新后重试",
                current_version=int(snapshot["version"]),
            )
        auto_completed = False
        for item in snapshot.get("items", []):
            if item.get("state") != "serial_pending":
                continue
            current = self.store.serial_reconciliation(
                owner, task_id, item["barcode"]
            )
            recorded = (
                current["matched"]
                + current["physical_only"]
                + current["other_product"]
            )
            if not recorded:
                continue
            self.finish_serial_item(
                owner, task_id, item["barcode"], "task-completion", actor,
            )
            auto_completed = True
        return self.store.complete_task(
            owner, task_id, actor,
            allow_unverified_serials=allow_unverified_serials,
            expected_version=None if auto_completed else expected_version,
        )

    def reopen_task(self, owner, task_id, actor):
        totals = self._call_worker(owner, "read_inventory_stock_totals")
        if not isinstance(totals, dict):
            raise InventoryServiceError("GYJ 库存汇总结果格式不正确")
        return self.store.reopen_task(
            owner, task_id, actor, totals, synced_at=self.now()
        )
