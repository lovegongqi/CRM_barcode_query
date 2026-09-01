import atexit
import os
import tempfile
import threading
import unittest
from unittest import mock


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "0"

import app as app_module
from gyj_inventory import GYJInventoryReadError


class _TrackingLock:
    def __init__(self):
        self.held = False

    def __enter__(self):
        self.held = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.held = False


class InventoryWorkerTests(unittest.TestCase):
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
