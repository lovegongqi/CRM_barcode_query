import atexit
import os
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "0"

import app as app_module
from gyj_inventory import GYJInventoryReadError
from inventory_service import InventoryService
from inventory_store import InventoryStore


class _TrackingLock:
    def __init__(self):
        self.held = False

    def __enter__(self):
        self.held = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.held = False


class _SerialLookupPage:
    def __init__(self):
        self.filled = []
        self.selected = []
        self.serial_state = None

    def goto(self, _url, **_kwargs):
        pass

    def expand_filters(self):
        pass

    def fill_field(self, label, value):
        self.filled.append((label, value))

    def select_label(self, label, value):
        self.selected.append((label, value))
        if label == "已出库":
            self.serial_state = value

    def click_query(self):
        pass

    def read_table_page(self, _report):
        rows = []
        if self.serial_state == "是":
            rows = [["SHIPPED-1", "B2", "序列商品", "已出库仓", "999", "是"]]
        return {
            "headers": ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"],
            "rows": rows,
            "total": len(rows),
            "has_next": False,
        }


class InventoryWorkerTests(unittest.TestCase):
    def test_shipped_lookup_flows_through_worker_session_to_serial_service(self):
        with tempfile.TemporaryDirectory() as tempdir:
            store = InventoryStore(
                os.path.join(tempdir, "inventory.sqlite3"),
                now=lambda: datetime(2026, 9, 1, 10, 0, 0),
            )
            task = store.create_task("admin", "管理员", [{
                "barcode": "B2", "name": "序列商品", "spec": "", "model": "",
                "category": "整机", "unit": "台", "has_serial": True,
                "initial_stock": "0",
            }])
            store.advance_count_phase_if_ready("admin", task["task_id"])
            store.claim_item(
                task["task_id"], "B2", "count-device", "甲", "counting"
            )
            store.set_open_book_quantity(
                "admin", task["task_id"], "B2", "count-device", "甲", "0"
            )
            store.record_count(
                "admin", task["task_id"], "B2", "count-device", "甲",
                completed_book_qty="0", completed_actual_qty="1",
                diff_qty="1", state="serial_pending",
            )

            page = _SerialLookupPage()
            session = object.__new__(app_module.GYJSession)
            session.lock = threading.RLock()
            session.page = object()
            session.check_login_status = mock.Mock(
                return_value=(True, "GYJ 已登录")
            )
            worker = object.__new__(app_module.GYJWorker)
            worker._call = lambda method, *args: getattr(session, method)(*args)
            service = InventoryService(
                store, lambda _owner: worker,
                now=lambda: datetime(2026, 9, 1, 10, 0, 0),
            )

            with mock.patch.object(
                app_module, "GYJPlaywrightPage", return_value=page
            ):
                service.open_serial_item(
                    "admin", task["task_id"], "B2", "device-a", "甲"
                )
                result = service.scan_serial(
                    "admin", task["task_id"], "B2", "device-a", "甲",
                    "SHIPPED-1",
                )

            self.assertEqual(result["classification"], "already_shipped")
            self.assertEqual(result["warehouse"], "已出库仓")
            self.assertTrue(result["shipped"])
            self.assertEqual(
                page.selected,
                [("已出库", "否"), ("已出库", "否"), ("已出库", "是")],
            )
            self.assertFalse(
                any(label == "仓库" for label, _value in page.filled + page.selected)
            )
            self.assertNotIn("999", repr(result))

    def test_worker_delegates_every_inventory_read_to_its_single_thread(self):
        cases = [
            ("load_inventory_catalog", (), "load_inventory_catalog", ()),
            ("read_inventory_stock", ("996041496",), "read_inventory_stock", ("996041496",)),
            ("read_inventory_stock_totals", (), "read_inventory_stock_totals", ()),
            ("read_inventory_serials", ("996041496",), "read_inventory_serials", ("996041496",)),
            ("lookup_inventory_serial", ("SN-1",), "lookup_inventory_serial", ("SN-1",)),
        ]

        for public_name, args, session_name, session_args in cases:
            with self.subTest(method=public_name):
                worker = object.__new__(app_module.GYJWorker)
                worker._call = mock.Mock(return_value=(True, "result"))

                result = getattr(worker, public_name)(*args)

                self.assertEqual(result, (True, "result"))
                worker._call.assert_called_once_with(session_name, *session_args)

    def test_session_inventory_reads_recheck_login_and_use_locked_current_page(self):
        cases = [
            ("load_inventory_catalog", (), "load_catalog", (), [{"barcode": "A1"}]),
            ("read_inventory_stock", ("A1",), "read_total_stock", ("A1",), "2"),
            ("read_inventory_stock_totals", (), "read_stock_totals", (), {"A1": "2"}),
            ("read_inventory_serials", ("A1",), "read_unshipped_serials", ("A1",), [{"serial": "SN-1"}]),
            ("lookup_inventory_serial", ("SN-1",), "lookup_serial", ("SN-1",), {"serial": "SN-1"}),
        ]

        for public_name, args, reader_name, reader_args, expected in cases:
            with self.subTest(method=public_name):
                session = object.__new__(app_module.GYJSession)
                session.lock = _TrackingLock()
                session.page = object()
                session.check_login_status = mock.Mock(
                    side_effect=lambda: (
                        self.assertTrue(session.lock.held) or True,
                        "GYJ 已登录",
                    )
                )
                reader = mock.Mock()
                getattr(reader, reader_name).side_effect = lambda *unused: (
                    self.assertTrue(session.lock.held) or expected
                )

                with mock.patch.object(
                    app_module, "GYJPlaywrightPage", return_value="current-page-adapter"
                ) as adapter_class, mock.patch.object(
                    app_module, "GYJInventoryReader", return_value=reader
                ) as reader_class:
                    result = getattr(session, public_name)(*args)

                self.assertEqual(result, (True, expected))
                session.check_login_status.assert_called_once_with()
                adapter_class.assert_called_once_with(session.page)
                reader_class.assert_called_once_with("current-page-adapter")
                getattr(reader, reader_name).assert_called_once_with(*reader_args)

    def test_session_inventory_reads_return_stable_reader_errors(self):
        cases = [
            ("load_inventory_catalog", (), "load_catalog"),
            ("read_inventory_stock", ("A1",), "read_total_stock"),
            ("read_inventory_stock_totals", (), "read_stock_totals"),
            ("read_inventory_serials", ("A1",), "read_unshipped_serials"),
            ("lookup_inventory_serial", ("SN-1",), "lookup_serial"),
        ]

        for public_name, args, reader_name in cases:
            with self.subTest(method=public_name):
                session = object.__new__(app_module.GYJSession)
                session.lock = threading.RLock()
                session.page = object()
                session.check_login_status = mock.Mock(return_value=(True, "GYJ 已登录"))
                reader = mock.Mock()
                getattr(reader, reader_name).side_effect = GYJInventoryReadError("库存报表读取失败")

                with mock.patch.object(app_module, "GYJPlaywrightPage"), mock.patch.object(
                    app_module, "GYJInventoryReader", return_value=reader
                ):
                    result = getattr(session, public_name)(*args)

                self.assertEqual(result, (False, "库存报表读取失败"))

    def test_session_inventory_read_stops_when_login_recheck_fails(self):
        session = object.__new__(app_module.GYJSession)
        session.lock = threading.RLock()
        session.page = object()
        session.check_login_status = mock.Mock(return_value=(False, "请先登录 GYJ"))

        with mock.patch.object(app_module, "GYJInventoryReader") as reader_class:
            result = session.read_inventory_stock("A1")

        self.assertEqual(result, (False, "请先登录 GYJ"))
        reader_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
