import atexit
from datetime import datetime, timedelta
from io import BytesIO
import os
import tempfile
import threading
import unittest
from unittest import mock

from openpyxl import load_workbook


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "0"

import app as app_module
from inventory_service import InventoryService, InventoryServiceError
from inventory_store import (
    InventoryConflict,
    InventoryNotFound,
    InventoryPermissionDenied,
    InventoryStore,
)


class InventoryRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.tempdir = tempfile.TemporaryDirectory()
        self.accounts_file = os.path.join(self.tempdir.name, "accounts.json")
        self.accounts_patch = mock.patch.object(
            app_module, "ACCOUNTS_FILE", self.accounts_file
        )
        self.accounts_patch.start()
        app_module.save_accounts([
            {
                "id": "admin",
                "username": "admin",
                "display_name": "管理员",
                "password": "admin-pass",
                "permissions": [
                    "crm", "results", "transfer", "inbound", "inventory",
                    "accounts", "product-library",
                ],
                "updated_at": "",
            },
            {
                "id": "viewer-id",
                "username": "viewer",
                "display_name": "访客",
                "password": "viewer-pass",
                "permissions": ["results"],
                "updated_at": "",
            },
            {
                "id": "counter-id",
                "username": "counter",
                "display_name": "盘点员",
                "password": "counter-pass",
                "permissions": ["inventory"],
                "updated_at": "",
            },
            {
                "id": "warehouse-account-id",
                "username": "warehouse-user",
                "display_name": "仓库账号",
                "password": "warehouse-pass",
                "permissions": ["inbound", "inventory"],
                "updated_at": "",
            },
        ])
        self.store = mock.Mock()
        self.service = mock.Mock()
        self.production_inventory_service = app_module.inventory_service
        self.store.get_active_task.return_value = None
        self.store.get_task_snapshot.return_value = {
            "success": True,
            "task_id": "task-1",
            "phase": "counting",
            "version": 3,
            "last_sync_at": datetime.now().isoformat(timespec="seconds"),
            "items": [],
        }
        self.store.list_items.return_value = []
        self.store.list_task_history.return_value = []
        self.store.list_discrepancies.return_value = []
        self.service.create_task.return_value = {"task_id": "task-1", "phase": "counting"}
        self.service.open_count_item.return_value = {"barcode": "A/B", "state": "pending"}
        self.store.heartbeat_lock.return_value = {"barcode": "A/B", "device_id": "device-a"}
        self.service.submit_count.return_value = {"barcode": "A/B", "state": "matched"}
        self.service.open_serial_item.return_value = {"barcode": "A/B", "counts": {}}
        self.service.refresh_serial_item.return_value = {"barcode": "A/B", "counts": {}}
        self.service.scan_serial.return_value = {"serial": "SN/1", "classification": "matched"}
        self.service.delete_serial_scan.return_value = {"barcode": "A/B", "removed": "SN/1"}
        self.service.finish_serial_item.return_value = {"barcode": "A/B", "counts": {}}
        self.service.complete_task.return_value = {"task_id": "task-1", "completed": True}
        self.store.admin_unlock.return_value = {"task_id": "task-1", "barcode": "A/B", "unlocked": True}
        self.store.add_discrepancy_note.return_value = {"id": 1, "note": "checked"}
        self.store.archive_discrepancy.return_value = {"id": 1, "state": "archived"}
        self.store.restore_discrepancy.return_value = {"id": 1, "state": "open"}
        self.store_patch = mock.patch.object(
            app_module, "inventory_store", self.store, create=True
        )
        self.service_patch = mock.patch.object(
            app_module, "inventory_service", self.service, create=True
        )
        self.store_patch.start()
        self.service_patch.start()
        if hasattr(app_module, "inventory_sync_threads"):
            app_module.inventory_sync_threads.clear()

    def tearDown(self):
        if hasattr(app_module, "inventory_sync_threads"):
            for thread in list(app_module.inventory_sync_threads.values()):
                thread.join(timeout=1)
            app_module.inventory_sync_threads.clear()
        self.service_patch.stop()
        self.store_patch.stop()
        self.accounts_patch.stop()
        self.tempdir.cleanup()

    def login_account(self, username, password=None):
        client = app_module.app.test_client()
        response = client.post(
            "/api/app-auth/login",
            json={"username": username, "password": password or f"{username}-pass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        return client

    def test_inventory_permission_controls_page_and_api(self):
        viewer = self.login_account("viewer")
        self.assertEqual(viewer.get("/inventory").status_code, 403)
        self.assertEqual(viewer.get("/api/inventory/tasks/active").status_code, 403)

        counter = self.login_account("counter")
        self.assertEqual(counter.get("/inventory").status_code, 200)
        response = counter.get("/api/inventory/tasks/active")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "task": None})

    def test_anonymous_inventory_page_and_api_require_login(self):
        client = app_module.app.test_client()
        page = client.get("/inventory", follow_redirects=False)
        api = client.get("/api/inventory/tasks/active")
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login?next="))
        self.assertEqual(api.status_code, 401)

    def test_admin_navigation_places_inventory_after_inbound(self):
        page = self.login_account("admin", "admin-pass").get("/inventory").get_data(as_text=True)
        self.assertLess(page.index('href="/inbound"'), page.index('href="/inventory"'))
        self.assertLess(page.index('href="/inventory"'), page.index('href="/product-library"'))

    def test_inventory_page_receives_only_public_current_account(self):
        page = self.login_account("counter").get("/inventory").get_data(as_text=True)
        self.assertIn('data-aurora-page="inventory"', page)
        self.assertIn("const CURRENT_ACCOUNT =", page)
        self.assertIn('"is_admin": false', page)
        self.assertNotIn("counter-pass", page)
        self.assertNotIn('value="archive"', page)

    def test_admin_default_and_account_save_allow_inventory_permission(self):
        os.remove(self.accounts_file)
        self.assertIn("inventory", app_module.load_accounts()[0]["permissions"])

        client = self.login_account("admin", "88293529")
        response = client.post(
            "/api/accounts",
            json={
                "username": "new-counter",
                "display_name": "新盘点员",
                "password": "secret",
                "permissions": ["inventory", "not-allowed"],
            },
        )
        self.assertEqual(response.status_code, 200)
        saved = next(row for row in app_module.load_accounts() if row["username"] == "new-counter")
        self.assertEqual(saved["permissions"], ["inventory"])

    def test_inventory_service_uses_canonical_gyj_worker_provider_for_account_alias(self):
        sentinel = object()
        pool = mock.Mock()
        pool.get.return_value = sentinel
        with mock.patch.object(app_module, "gyj_worker", pool):
            result = self.production_inventory_service.worker_provider("warehouse-account-id")
        self.assertIs(result, sentinel)
        pool.get.assert_called_once_with("warehouse-user")

    def test_create_uses_session_owner_and_actor_not_request_overrides(self):
        client = self.login_account("warehouse-user", "warehouse-pass")
        response = client.post(
            "/api/inventory/tasks",
            json={"owner": "admin", "actor": "spoofed", "device_id": "ignored"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {"success", "task"})
        self.service.create_task.assert_called_once_with(
            "warehouse-account-id", "warehouse-user"
        )

    def test_active_query_passes_version_query_and_state_to_store(self):
        self.store.get_active_task.return_value = {"task_id": "task-1"}
        client = self.login_account("counter")
        with mock.patch.object(app_module, "_ensure_inventory_sync") as ensure:
            response = client.get(
                "/api/inventory/tasks/active?version=7&query=Zero%20stock&state=pending"
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(set(payload), {"success", "task"})
        self.store.get_task_snapshot.assert_called_once_with(
            "counter-id", "task-1", known_version=7
        )
        self.store.list_items.assert_called_once_with(
            "task-1", query="Zero stock", state="pending", include_zero=True
        )
        ensure.assert_called_once_with("counter-id", "task-1")

    def test_active_unchanged_snapshot_does_not_reload_items(self):
        self.store.get_active_task.return_value = {"task_id": "task-1"}
        self.store.get_task_snapshot.return_value = {
            "success": True, "unchanged": True, "version": 7
        }
        with mock.patch.object(app_module, "_ensure_inventory_sync"):
            response = self.login_account("counter").get(
                "/api/inventory/tasks/active?version=7"
            )
        self.assertEqual(
            response.get_json()["task"],
            {"success": True, "unchanged": True, "version": 7},
        )
        self.store.list_items.assert_not_called()

    def test_all_json_routes_use_the_declared_methods_and_response_keys(self):
        client = self.login_account("admin", "admin-pass")
        cases = [
            ("post", "/api/inventory/tasks", {}, "task"),
            ("get", "/api/inventory/tasks/history", None, "tasks"),
            ("get", "/api/inventory/tasks/task-1", None, "task"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/claim", {"device_id": "device-a"}, "item"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/heartbeat", {"device_id": "device-a"}, "lock"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/count", {"device_id": "device-a", "actual_qty": "2"}, "item"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/serial/open", {"device_id": "device-a"}, "serial"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/serial/refresh", {"device_id": "device-a"}, "serial"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/serials", {"device_id": "device-a", "serial": "SN/1"}, "scan"),
            ("delete", "/api/inventory/tasks/task-1/items/A%2FB/serials/SN%2F1", {"device_id": "device-a"}, "serial"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/serial/finish", {"device_id": "device-a"}, "serial"),
            ("post", "/api/inventory/tasks/task-1/complete", {}, "task"),
            ("post", "/api/inventory/tasks/task-1/items/A%2FB/unlock", {"is_admin": False}, "result"),
            ("get", "/api/inventory/discrepancies?state=open&query=A%2FB", None, "discrepancies"),
            ("post", "/api/inventory/discrepancies/1/notes", {"note": "checked", "serial": "SN/1", "actor": "spoofed"}, "note"),
            ("post", "/api/inventory/discrepancies/1/archive", {"is_admin": False}, "discrepancy"),
            ("post", "/api/inventory/discrepancies/1/restore", {"is_admin": False}, "discrepancy"),
        ]
        for method, path, body, key in cases:
            with self.subTest(method=method, path=path):
                response = getattr(client, method)(path, json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.get_json()), {"success", key})
                self.assertTrue(response.get_json()["success"])

        self.service.open_count_item.assert_called_once_with(
            "admin", "task-1", "A/B", "device-a", "admin"
        )
        self.service.scan_serial.assert_called_once_with(
            "admin", "task-1", "A/B", "device-a", "admin", "SN/1"
        )
        self.service.delete_serial_scan.assert_called_once_with(
            "admin", "task-1", "A/B", "device-a", "admin", "SN/1"
        )
        self.store.add_discrepancy_note.assert_called_once_with(
            "admin", 1, "admin", "checked", serial="SN/1"
        )
        self.store.archive_discrepancy.assert_called_once_with(
            "admin", 1, "admin", True
        )

    def test_wrong_methods_are_rejected_for_every_inventory_route(self):
        client = self.login_account("admin", "admin-pass")
        cases = [
            ("post", "/api/inventory/tasks/active"),
            ("get", "/api/inventory/tasks"),
            ("post", "/api/inventory/tasks/history"),
            ("post", "/api/inventory/tasks/task-1"),
            ("get", "/api/inventory/tasks/task-1/items/A/claim"),
            ("get", "/api/inventory/tasks/task-1/items/A/heartbeat"),
            ("get", "/api/inventory/tasks/task-1/items/A/count"),
            ("get", "/api/inventory/tasks/task-1/items/A/serial/open"),
            ("get", "/api/inventory/tasks/task-1/items/A/serial/refresh"),
            ("get", "/api/inventory/tasks/task-1/items/A/serials"),
            ("post", "/api/inventory/tasks/task-1/items/A/serials/SN"),
            ("get", "/api/inventory/tasks/task-1/items/A/serial/finish"),
            ("get", "/api/inventory/tasks/task-1/complete"),
            ("get", "/api/inventory/tasks/task-1/items/A/unlock"),
            ("post", "/api/inventory/tasks/task-1/export"),
            ("post", "/api/inventory/discrepancies"),
            ("get", "/api/inventory/discrepancies/1/notes"),
            ("get", "/api/inventory/discrepancies/1/archive"),
            ("get", "/api/inventory/discrepancies/1/restore"),
            ("post", "/api/inventory/discrepancies/export"),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(client, method)(path).status_code, 405)

    def test_inventory_exception_statuses_and_worker_errors_are_sanitized(self):
        client = self.login_account("counter")
        cases = [
            (InventoryConflict("locked"), 409, "locked"),
            (InventoryNotFound("missing"), 404, "missing"),
            (InventoryPermissionDenied("admin only"), 403, "admin only"),
            (ValueError("bad quantity"), 400, "bad quantity"),
        ]
        for error, status, message in cases:
            with self.subTest(error=type(error).__name__):
                self.service.create_task.side_effect = error
                response = client.post("/api/inventory/tasks")
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json(), {"success": False, "error": message})

        secret = "成本价=999; password=secret; <html>private page</html>"
        self.service.create_task.side_effect = InventoryServiceError(secret)
        response = client.post("/api/inventory/tasks")
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["success"])
        self.assertNotIn("999", response.get_data(as_text=True))
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertNotIn("html", response.get_data(as_text=True).lower())

        self.store.get_active_task.return_value = {"task_id": "task-1"}
        self.store.get_task_snapshot.return_value = {
            "success": True,
            "task_id": "task-1",
            "phase": "sync_error",
            "version": 4,
            "gyj_status": secret,
            "items": [],
        }
        with mock.patch.object(app_module, "_ensure_inventory_sync"):
            response = client.get("/api/inventory/tasks/active")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("999", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("html", body.lower())

    def test_lock_owner_is_returned_only_for_real_owner_conflicts(self):
        client = self.login_account("counter")
        owned = InventoryConflict(
            "商品已被其他设备锁定", lock_owner="\x00乙\n操作员\t"
        )

        self.service.open_count_item.side_effect = owned
        claim = client.post(
            "/api/inventory/tasks/task-1/items/A/claim",
            json={"device_id": "device-a"},
        )
        self.assertEqual(claim.status_code, 409)
        self.assertEqual(claim.get_json()["lock_owner"], "乙操作员")

        self.store.heartbeat_lock.side_effect = owned
        heartbeat = client.post(
            "/api/inventory/tasks/task-1/items/A/heartbeat",
            json={"device_id": "device-a"},
        )
        self.assertEqual(heartbeat.status_code, 409)
        self.assertEqual(heartbeat.get_json()["lock_owner"], "乙操作员")

        self.service.submit_count.side_effect = owned
        count = client.post(
            "/api/inventory/tasks/task-1/items/A/count",
            json={"device_id": "device-a", "actual_qty": "1"},
        )
        self.assertEqual(count.status_code, 409)
        self.assertEqual(count.get_json()["lock_owner"], "乙操作员")

        for route, target in (
            ("/api/inventory/tasks/task-1/items/A/claim", self.service.open_count_item),
            ("/api/inventory/tasks/task-1/items/A/count", self.service.submit_count),
        ):
            target.side_effect = InventoryConflict("当前阶段不允许此操作")
            body = {"device_id": "device-a", "actual_qty": "1"}
            response = client.post(route, json=body)
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("lock_owner", response.get_json())

        self.service.open_count_item.side_effect = InventoryConflict(
            "商品数量盘点已完成"
        )
        completed = client.post(
            "/api/inventory/tasks/task-1/items/A/claim",
            json={"device_id": "device-a"},
        )
        self.assertEqual(completed.status_code, 409)
        self.assertNotIn("lock_owner", completed.get_json())

        self.store.heartbeat_lock.side_effect = InventoryConflict("设备未持有商品锁")
        expired = client.post(
            "/api/inventory/tasks/task-1/items/A/heartbeat",
            json={"device_id": "device-a"},
        )
        self.assertEqual(expired.status_code, 409)
        self.assertNotIn("lock_owner", expired.get_json())

    def test_real_service_maps_raised_worker_error_to_redacted_502(self):
        secret = "cost=999; password=secret; <html>private page</html>"

        class RaisingWorker:
            def load_inventory_catalog(self):
                raise RuntimeError(secret)

        store = InventoryStore(
            os.path.join(self.tempdir.name, "route-worker-error.sqlite3")
        )
        service = InventoryService(store, lambda _owner: RaisingWorker())
        with (
            mock.patch.object(app_module, "inventory_store", store),
            mock.patch.object(app_module, "inventory_service", service),
        ):
            response = self.login_account("counter").post("/api/inventory/tasks")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {
                "success": False,
                "error": "GYJ 库存读取失败，请检查登录状态后重试",
            },
        )
        self.assertNotIn("999", body)
        self.assertNotIn("secret", body)
        self.assertNotIn("html", body.lower())

    def test_real_service_background_sync_persists_error_and_active_retry_recovers(self):
        secret = "cost=999; password=secret; <html>private page</html>"
        retry_started = threading.Event()

        class RetryWorker:
            def __init__(self):
                self.calls = 0

            def read_inventory_stock_totals(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError(secret)
                retry_started.set()
                return True, {"A1": "2"}

        store = InventoryStore(
            os.path.join(self.tempdir.name, "route-sync-retry.sqlite3")
        )
        task = store.create_task("counter-id", "counter", [{
            "barcode": "A1",
            "name": "Filter",
            "spec": "",
            "model": "",
            "category": "Parts",
            "unit": "piece",
            "has_serial": False,
            "initial_stock": "2",
        }])
        task_id = task["task_id"]
        store.advance_count_phase_if_ready("counter-id", task_id)
        store.claim_item(task_id, "A1", "device-a", "counter", "counting")
        store.set_open_book_quantity(
            "counter-id", task_id, "A1", "device-a", "counter", "2"
        )
        store.record_count(
            "counter-id",
            task_id,
            "A1",
            "device-a",
            "counter",
            completed_book_qty="2",
            completed_actual_qty="2",
            diff_qty="0",
            state="matched",
        )
        worker = RetryWorker()
        service = InventoryService(store, lambda _owner: worker)

        with (
            mock.patch.object(app_module, "inventory_store", store),
            mock.patch.object(app_module, "inventory_service", service),
        ):
            app_module._run_inventory_sync("counter-id", task_id)
            failed = store.get_task_snapshot("counter-id", task_id)
            self.assertEqual(failed["phase"], "sync_error")
            self.assertIsNone(failed["last_sync_at"])
            self.assertNotIn("999", failed["gyj_status"])
            self.assertNotIn("secret", failed["gyj_status"])
            self.assertNotIn("html", failed["gyj_status"].lower())

            response = self.login_account("counter").get(
                "/api/inventory/tasks/active"
            )
            self.assertEqual(response.status_code, 200)
            response_body = response.get_data(as_text=True)
            self.assertNotIn("999", response_body)
            self.assertNotIn("secret", response_body)
            self.assertNotIn("html", response_body.lower())
            self.assertTrue(retry_started.wait(1))
            with app_module.inventory_sync_lock:
                retry_thread = app_module.inventory_sync_threads.get(task_id)
            if retry_thread is not None:
                retry_thread.join(1)

        recovered = store.get_task_snapshot("counter-id", task_id)
        self.assertEqual(worker.calls, 2)
        self.assertEqual(recovered["phase"], "counting")
        self.assertEqual(recovered["gyj_status"], "synced")
        self.assertIsNotNone(recovered["last_sync_at"])

    def test_invalid_version_quantity_and_missing_device_are_400(self):
        self.store.get_active_task.return_value = {"task_id": "task-1"}
        client = self.login_account("counter")
        self.assertEqual(
            client.get("/api/inventory/tasks/active?version=not-an-int").status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/inventory/tasks/task-1/items/A/count",
                json={"device_id": "device-a", "actual_qty": "not-a-number"},
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/inventory/tasks/task-1/items/A/claim", json={}
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/api/inventory/tasks/task-1/items/A/claim", json=[]
            ).status_code,
            400,
        )
        self.assertEqual(
            client.get(
                "/api/inventory/discrepancies?state=pending"
            ).status_code,
            400,
        )

    def test_non_admin_cannot_unlock_archive_or_restore_even_with_spoofed_json(self):
        client = self.login_account("counter")
        paths = [
            "/api/inventory/tasks/task-1/items/A/unlock",
            "/api/inventory/discrepancies/1/archive",
            "/api/inventory/discrepancies/1/restore",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = client.post(path, json={"is_admin": True, "actor": "admin"})
                self.assertEqual(response.status_code, 403)
                self.assertFalse(response.get_json()["success"])
        self.store.admin_unlock.assert_not_called()
        self.store.archive_discrepancy.assert_not_called()
        self.store.restore_discrepancy.assert_not_called()

    def test_history_and_discrepancy_route_json_contract_matches_store_rows(self):
        history_row = {
            "task_id": "task-history-1", "owner": "counter-id",
            "created_by": "盘点员", "phase": "completed", "version": 8,
            "started_at": "2026-09-01T08:00:00",
            "completed_at": "2026-09-01T09:00:00", "last_sync_at": None,
            "gyj_status": "synced", "sync_resume_phase": None,
            "completed": True,
        }
        discrepancy_row = {
            "id": 9, "task_id": "task-history-1", "barcode": "A/B",
            "serial": "SN/1", "kind": "other_product_serial",
            "book_quantity": None, "counted_quantity": None,
            "difference": None, "state": "open", "archived_by": None,
            "archived_at": None, "created_at": "2026-09-01T09:00:00",
            "name": "滤芯", "spec": "S", "model": "M", "category": "耗材",
            "unit": "支", "has_serial": True,
            "completed_at": "2026-09-01T09:00:00", "scan_actor": "盘点员",
            "scan_device": "device-a", "scanned_at": "2026-09-01T08:30:00",
            "lookup_barcode": "OTHER", "lookup_name": "其他商品",
            "warehouse": "南昌仓", "shipped": False,
            "notes": [{
                "id": 1, "task_id": "task-history-1", "barcode": "A/B",
                "serial": "SN/1", "note": "已复核", "actor": "盘点员",
                "created_at": "2026-09-01T09:10:00",
            }],
            "note": "已复核",
        }
        self.store.list_task_history.return_value = [history_row]
        self.store.list_discrepancies.return_value = [discrepancy_row]
        client = self.login_account("counter")

        history = client.get("/api/inventory/tasks/history").get_json()
        discrepancies = client.get(
            "/api/inventory/discrepancies?state=open&query=SN%2F1"
        ).get_json()

        self.assertEqual(history, {"success": True, "tasks": [history_row]})
        self.assertEqual(
            discrepancies,
            {"success": True, "discrepancies": [discrepancy_row]},
        )
        self.store.list_discrepancies.assert_called_with(
            "counter-id", "open", query="SN/1"
        )

    def test_export_routes_are_owner_scoped_filtered_and_price_free(self):
        self.store.get_task_snapshot.return_value = {
            "success": True,
            "task_id": "task-unsafe\r\nname",
            "phase": "completed",
            "items": [{
                "barcode": "A1", "name": "Filter", "diff_qty": "1",
                "completed_book_qty": "1", "completed_actual_qty": "2",
                "purchase_price": "SECRET-PRICE",
            }],
        }
        self.store.list_discrepancies.side_effect = [
            [{"task_id": "task-unsafe\r\nname", "barcode": "A1", "serial": None, "state": "open", "cost": "SECRET-PRICE"}],
            [],
            [{"task_id": "task-unsafe\r\nname", "barcode": "A1", "serial": "SN1", "state": "open", "price": "SECRET-PRICE"}],
        ]
        client = self.login_account("counter")
        task_response = client.get("/api/inventory/tasks/task-safe/export")
        self.assertEqual(task_response.status_code, 200)
        self.assertNotIn("\r", task_response.headers["Content-Disposition"])
        self.assertNotIn("\n", task_response.headers["Content-Disposition"])
        workbook = load_workbook(BytesIO(task_response.data))
        self.assertNotIn("SECRET-PRICE", repr([
            cell.value for sheet in workbook.worksheets for row in sheet.iter_rows() for cell in row
        ]))
        self.store.get_task_snapshot.assert_called_with(
            "counter-id", "task-safe"
        )

        discrepancy_response = client.get(
            "/api/inventory/discrepancies/export?state=open&query=SN1"
        )
        self.assertEqual(discrepancy_response.status_code, 200)
        discrepancy_workbook = load_workbook(BytesIO(discrepancy_response.data))
        self.assertNotIn("SECRET-PRICE", repr([
            cell.value
            for sheet in discrepancy_workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
        ]))
        self.store.list_discrepancies.assert_called_with(
            "counter-id", "open", query="SN1"
        )

    def test_ensure_inventory_sync_deduplicates_daemons_and_reopens_stale_tasks(self):
        stale = (datetime.now() - timedelta(seconds=61)).isoformat(timespec="seconds")
        self.store.get_task_snapshot.return_value = {
            "task_id": "task-1", "phase": "counting", "last_sync_at": stale
        }
        started = threading.Event()
        release = threading.Event()
        calls = []

        def sync(owner, task_id):
            calls.append((owner, task_id))
            started.set()
            release.wait(2)

        self.service.sync_completed_items.side_effect = sync
        self.assertTrue(app_module._ensure_inventory_sync("counter-id", "task-1"))
        self.assertTrue(started.wait(1))
        thread = app_module.inventory_sync_threads["task-1"]
        self.assertTrue(thread.daemon)
        self.assertFalse(app_module._ensure_inventory_sync("counter-id", "task-1"))
        self.assertEqual(calls, [("counter-id", "task-1")])

        release.set()
        thread.join(1)
        self.assertNotIn("task-1", app_module.inventory_sync_threads)

        started.clear()
        release.clear()
        self.assertTrue(app_module._ensure_inventory_sync("counter-id", "task-1"))
        self.assertTrue(started.wait(1))
        self.assertEqual(len(calls), 2)
        release.set()

    def test_recent_sync_does_not_start_a_daemon_but_sync_error_does(self):
        self.store.get_task_snapshot.return_value = {
            "task_id": "task-1",
            "phase": "counting",
            "last_sync_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.assertFalse(app_module._ensure_inventory_sync("counter-id", "task-1"))
        self.service.sync_completed_items.assert_not_called()

        done = threading.Event()
        self.store.get_task_snapshot.return_value["phase"] = "sync_error"
        self.service.sync_completed_items.side_effect = lambda *_args: done.set()
        self.assertTrue(app_module._ensure_inventory_sync("counter-id", "task-1"))
        self.assertTrue(done.wait(1))

    def test_sync_thread_start_failure_cleans_registry_without_blocking_caller(self):
        self.store.get_task_snapshot.return_value = {
            "task_id": "task-1", "phase": "counting", "last_sync_at": None
        }
        thread = mock.Mock()
        thread.start.side_effect = RuntimeError("thread unavailable")
        with mock.patch.object(app_module.threading, "Thread", return_value=thread):
            self.assertFalse(
                app_module._ensure_inventory_sync("counter-id", "task-1")
            )
        self.assertNotIn("task-1", app_module.inventory_sync_threads)


if __name__ == "__main__":
    unittest.main()
