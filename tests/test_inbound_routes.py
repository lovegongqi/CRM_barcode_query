import atexit
import os
import tempfile
import threading
import time
import unittest
from unittest import mock


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "0"

import app as app_module


PACKING_SLIP_NO = "SH202607210002"
FIXED_ROW = {
    "page": 1,
    "row_index": 1,
    "order_number": "210524",
    "product_code": "916000024",
    "description": "中央净水机",
    "expected_quantity": "1",
    "serial": "SN00000001",
}


class FakeInboundWorker:
    def __init__(self, success=True, result=None):
        self.slot_id = "query-2"
        self.busy = False
        self.logged_in = True
        self.remembered_logged_in = True
        self.success = success
        self.result = result

    def extract_packing_slip(self, packing_slip_no, log=None, progress=None):
        if log:
            log("正在打开 B2B 装箱单页面")
            log(f"正在查询装箱单 {packing_slip_no}")
        if not self.success:
            return False, self.result or "分页跳号：期望第 2 页，实际第 3 页"
        page_count = {"page": 1, "row_count": 1}
        if progress:
            progress(page_count)
        return True, {
            "rows": [dict(FIXED_ROW)],
            "page_counts": [page_count],
        }


class InboundRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True)

    def setUp(self):
        self._original_accounts = app_module.load_accounts()
        app_module.save_accounts([
            {
                "id": "admin",
                "username": "admin",
                "display_name": "管理员",
                "password": "88293529",
                "permissions": [
                    "crm",
                    "results",
                    "transfer",
                    "inbound",
                    "accounts",
                    "product-library",
                ],
                "updated_at": "",
            },
            {
                "id": "transfer-only",
                "username": "transfer-only",
                "display_name": "移库账号",
                "password": "transfer-pass",
                "permissions": ["transfer"],
                "updated_at": "",
            },
            {
                "id": "inbound-other",
                "username": "inbound-other",
                "display_name": "其他入库账号",
                "password": "inbound-pass",
                "permissions": ["inbound"],
                "updated_at": "",
            },
        ])
        if hasattr(app_module, "inbound_job_lock"):
            with app_module.inbound_job_lock:
                app_module.inbound_jobs.clear()
                app_module.latest_inbound_job_by_owner.clear()
                app_module.latest_inbound_job_by_slot.clear()
        with app_module.batch_job_lock:
            app_module.batch_jobs.clear()
            app_module.latest_batch_job_by_slot.clear()
        with app_module.background_query_job_lock:
            app_module.background_query_jobs.clear()
            app_module.latest_background_query_job_by_owner.clear()

    def tearDown(self):
        if hasattr(app_module, "inbound_job_lock"):
            with app_module.inbound_job_lock:
                app_module.inbound_jobs.clear()
                app_module.latest_inbound_job_by_owner.clear()
                app_module.latest_inbound_job_by_slot.clear()
        with app_module.batch_job_lock:
            app_module.batch_jobs.clear()
            app_module.latest_batch_job_by_slot.clear()
        with app_module.background_query_job_lock:
            app_module.background_query_jobs.clear()
            app_module.latest_background_query_job_by_owner.clear()
        app_module.save_accounts(self._original_accounts)

    @staticmethod
    def _login(username, password):
        client = app_module.app.test_client()
        response = client.post(
            "/api/app-auth/login",
            json={"username": username, "password": password},
        )
        if response.status_code != 200 or not response.get_json().get("success"):
            raise AssertionError(f"login failed for {username}")
        return client

    def _wait_for_job(self, client, job_id):
        deadline = time.time() + 2
        while time.time() < deadline:
            response = client.get(f"/api/inbound/status?job_id={job_id}")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            if payload["done"]:
                return payload
            time.sleep(0.01)
        self.fail("inbound job did not finish")

    def test_inbound_requires_its_own_permission(self):
        anonymous = app_module.app.test_client()
        page = anonymous.get("/inbound", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["Location"].startswith("/login?next="))
        start = anonymous.post(
            "/api/inbound/start",
            json={"packing_slip_no": PACKING_SLIP_NO},
        )
        self.assertEqual(start.status_code, 401)

        transfer_only = self._login("transfer-only", "transfer-pass")
        self.assertEqual(transfer_only.get("/inbound").status_code, 403)
        denied_api = transfer_only.post(
            "/api/inbound/start",
            json={"packing_slip_no": PACKING_SLIP_NO},
        )
        self.assertEqual(denied_api.status_code, 403)
        transfer_page = transfer_only.get("/transfer")
        self.assertEqual(transfer_page.status_code, 200)
        self.assertNotIn("入库".encode("utf-8"), transfer_page.data)

        inbound_user = self._login("inbound-other", "inbound-pass")
        with mock.patch.object(app_module, "render_template", return_value="inbound page"):
            allowed_page = inbound_user.get("/inbound")
        self.assertEqual(allowed_page.status_code, 200)

    def test_invalid_number_is_rejected_before_selecting_a_channel(self):
        client = self._login("admin", "88293529")
        with mock.patch.object(
            app_module,
            "_select_idle_query_worker_desc",
            side_effect=AssertionError("channel selection must not run"),
        ):
            response = client.post(
                "/api/inbound/start",
                json={"packing_slip_no": "210524"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    def test_session_revalidates_login_before_reading_packing_slip(self):
        crm_session = app_module.CRMSession()
        crm_session.logged_in = True
        crm_session.is_alive = lambda: True
        crm_session._is_current_page_logged_in = lambda: False

        success, error = crm_session.extract_packing_slip(PACKING_SLIP_NO)

        self.assertFalse(success)
        self.assertEqual(error, "CRM 当前未登录，请先登录 CRM")

    def test_successful_job_is_owner_isolated_and_downloads_server_result(self):
        client = self._login("admin", "88293529")
        worker = FakeInboundWorker()
        with mock.patch.object(
            app_module,
            "_select_idle_query_worker_desc",
            return_value=(worker, "query-2", "查询2", ""),
        ):
            started = client.post(
                "/api/inbound/start",
                json={"packing_slip_no": PACKING_SLIP_NO},
            )
        self.assertEqual(started.status_code, 200)
        job_id = started.get_json()["job_id"]
        status = self._wait_for_job(client, job_id)
        self.assertTrue(status["success"])
        self.assertEqual(status["stage"], "success")
        self.assertEqual(status["result"]["pages_read"], [1])
        self.assertEqual(status["download_url"], f"/api/inbound/export?job_id={job_id}")
        latest = client.get("/api/inbound/status?latest=1")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.get_json()["job_id"], job_id)

        other = self._login("inbound-other", "inbound-pass")
        self.assertEqual(
            other.get(f"/api/inbound/status?job_id={job_id}").status_code,
            404,
        )
        self.assertEqual(
            other.get(f"/api/inbound/export?job_id={job_id}").status_code,
            404,
        )

        download = client.get(
            "/api/inbound/export",
            query_string={
                "job_id": job_id,
                "rows": "client rows must be ignored",
            },
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(
            download.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        disposition = download.headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn(PACKING_SLIP_NO, disposition)
        self.assertTrue(download.data.startswith(b"PK"))

    def test_new_start_removes_prior_completed_owner_job_and_result(self):
        client = self._login("admin", "88293529")
        with mock.patch.object(
            app_module,
            "_select_idle_query_worker_desc",
            return_value=(FakeInboundWorker(), "query-2", "查询2", ""),
        ):
            first = client.post(
                "/api/inbound/start",
                json={"packing_slip_no": PACKING_SLIP_NO},
            )
        first_job_id = first.get_json()["job_id"]
        self.assertTrue(self._wait_for_job(client, first_job_id)["success"])
        self.assertEqual(
            client.get(f"/api/inbound/export?job_id={first_job_id}").status_code,
            200,
        )

        with mock.patch.object(
            app_module,
            "_select_idle_query_worker_desc",
            return_value=(FakeInboundWorker(), "query-2", "查询2", ""),
        ):
            second = client.post(
                "/api/inbound/start",
                json={"packing_slip_no": "SH202607210003"},
            )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            client.get(f"/api/inbound/status?job_id={first_job_id}").status_code,
            404,
        )
        self.assertEqual(
            client.get(f"/api/inbound/export?job_id={first_job_id}").status_code,
            404,
        )

    def test_explicit_batch_start_refuses_an_inbound_reserved_slot(self):
        with app_module.inbound_job_lock:
            inbound_job = app_module._empty_inbound_job(
                "admin", PACKING_SLIP_NO, "query-2", "查询2"
            )
            inbound_job["running"] = True
            app_module.inbound_jobs[inbound_job["job_id"]] = inbound_job
            app_module.latest_inbound_job_by_slot["query-2"] = inbound_job["job_id"]

        client = self._login("admin", "88293529")
        with (
            mock.patch.object(app_module, "_request_slot_id", return_value="query-2"),
            mock.patch.object(app_module.crm_pool, "get", return_value=object()),
            mock.patch.object(app_module.threading, "Thread"),
        ):
            response = client.post(
                "/api/crm/batch/start",
                json={"barcodes": ["7925000000001"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["success"])
        self.assertIn("入库", response.get_json()["error"])

    def test_background_batch_start_skips_an_inbound_reserved_slot(self):
        class IdleWorker:
            def clear_stop(self):
                pass

        with app_module.inbound_job_lock:
            inbound_job = app_module._empty_inbound_job(
                "admin", PACKING_SLIP_NO, "query-2", "查询2"
            )
            inbound_job["running"] = True
            app_module.inbound_jobs[inbound_job["job_id"]] = inbound_job
            app_module.latest_inbound_job_by_slot["query-2"] = inbound_job["job_id"]

        client = self._login("admin", "88293529")
        with (
            mock.patch.object(
                app_module,
                "configured_query_slot_ids",
                return_value=["query-1", "query-2"],
            ),
            mock.patch.object(app_module.crm_pool, "get", return_value=IdleWorker()),
            mock.patch.object(app_module.threading, "Thread"),
        ):
            response = client.post(
                "/api/crm/background-batch/start",
                json={"barcodes": ["7925000000001"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["slot_ids"], ["query-1"])

    def test_concurrent_inbound_and_batch_start_claim_only_one_contested_slot(self):
        class BlockingQueryWorker:
            def __init__(self):
                self.slot_id = "query-2"
                self.busy = False
                self.logged_in = True
                self.remembered_logged_in = True
                self.batch_query_started = threading.Event()
                self.release_batch_query = threading.Event()
                self.inbound_calls = 0

            def clear_stop(self):
                pass

            def is_stop_requested(self):
                return False

            def query_barcode(self, barcode, log=None, output_dir=None):
                self.batch_query_started.set()
                self.release_batch_query.wait(2)
                return True, f"{barcode}.html"

            def extract_packing_slip(self, packing_slip_no, log=None, progress=None):
                self.inbound_calls += 1
                return True, {
                    "rows": [dict(FIXED_ROW)],
                    "page_counts": [{"page": 1, "row_count": 1}],
                }

        worker = BlockingQueryWorker()
        batch_checked = threading.Event()
        release_batch_check = threading.Event()
        batch_done = threading.Event()
        inbound_started = threading.Event()
        inbound_done = threading.Event()
        responses = {}
        original_inbound_check = app_module._query_slot_has_running_inbound

        def coordinate_inbound_check(slot_id):
            if threading.current_thread().name == "batch-start":
                batch_checked.set()
                release_batch_check.wait(2)
                return False
            return original_inbound_check(slot_id)

        batch_client = self._login("admin", "88293529")
        inbound_client = self._login("admin", "88293529")

        def start_batch():
            responses["batch"] = batch_client.post(
                "/api/crm/batch/start",
                json={"slot_id": "query-2", "barcodes": ["7925000000001"]},
            )
            batch_done.set()

        def start_inbound():
            inbound_started.set()
            responses["inbound"] = inbound_client.post(
                "/api/inbound/start",
                json={"packing_slip_no": PACKING_SLIP_NO},
            )
            inbound_done.set()

        batch_thread = threading.Thread(target=start_batch, name="batch-start")
        inbound_thread = threading.Thread(target=start_inbound, name="inbound-start")
        try:
            with (
                mock.patch.object(app_module, "_query_slot_has_running_inbound", side_effect=coordinate_inbound_check),
                mock.patch.object(app_module.crm_pool, "query_slots", ["query-2"]),
                mock.patch.object(app_module.crm_pool, "get", return_value=worker),
                mock.patch.object(app_module, "_query_slot_cooldown_message", return_value=""),
                mock.patch.object(app_module, "_query_slot_has_running_service_close", return_value=False),
            ):
                batch_thread.start()
                self.assertTrue(batch_checked.wait(1))
                inbound_thread.start()
                self.assertTrue(inbound_started.wait(1))
                inbound_finished_before_batch_claim = inbound_done.wait(0.1)
                release_batch_check.set()
                self.assertTrue(batch_done.wait(1))
                self.assertTrue(worker.batch_query_started.wait(1))
                self.assertTrue(inbound_done.wait(1))
        finally:
            release_batch_check.set()
            worker.release_batch_query.set()
            batch_thread.join(2)
            inbound_thread.join(2)

        self.assertFalse(inbound_finished_before_batch_claim)
        self.assertTrue(responses["batch"].get_json()["success"])
        self.assertEqual(responses["inbound"].status_code, 409)
        self.assertFalse(responses["inbound"].get_json()["success"])
        self.assertEqual(worker.inbound_calls, 0)

    def test_start_reserves_slot_before_worker_runs_and_rejects_owner_conflict(self):
        class BlockingWorker(FakeInboundWorker):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()
                self.reservation_seen = False

            def extract_packing_slip(self, packing_slip_no, log=None, progress=None):
                with app_module.inbound_job_lock:
                    job_id = app_module.latest_inbound_job_by_slot.get(self.slot_id)
                    job = app_module.inbound_jobs.get(job_id)
                    self.reservation_seen = bool(job and job.get("running"))
                self.started.set()
                self.release.wait(2)
                return super().extract_packing_slip(packing_slip_no, log, progress)

        client = self._login("admin", "88293529")
        worker = BlockingWorker()
        try:
            with mock.patch.object(
                app_module,
                "_select_idle_query_worker_desc",
                return_value=(worker, worker.slot_id, "查询2", ""),
            ):
                first = client.post(
                    "/api/inbound/start",
                    json={"packing_slip_no": PACKING_SLIP_NO},
                )
                self.assertEqual(first.status_code, 200)
                self.assertTrue(worker.started.wait(1))
                self.assertTrue(worker.reservation_seen)

                second = client.post(
                    "/api/inbound/start",
                    json={"packing_slip_no": PACKING_SLIP_NO},
                )
                self.assertEqual(second.status_code, 409)
        finally:
            worker.release.set()

        self._wait_for_job(client, first.get_json()["job_id"])

    def test_failed_job_has_error_without_download_url(self):
        client = self._login("admin", "88293529")
        error = "分页跳号：期望第 2 页，实际第 3 页"
        worker = FakeInboundWorker(success=False, result=error)
        with mock.patch.object(
            app_module,
            "_select_idle_query_worker_desc",
            return_value=(worker, "query-2", "查询2", ""),
        ):
            started = client.post(
                "/api/inbound/start",
                json={"packing_slip_no": PACKING_SLIP_NO},
            )
        job_id = started.get_json()["job_id"]
        status = self._wait_for_job(client, job_id)
        self.assertTrue(status["done"])
        self.assertFalse(status["success"])
        self.assertEqual(status["stage"], "failed")
        self.assertEqual(status["error"], error)
        self.assertNotIn("download_url", status)

    def test_reserved_inbound_slot_is_excluded_from_idle_query_workers(self):
        class IdleWorker:
            busy = False
            logged_in = True
            remembered_logged_in = True

        with app_module.inbound_job_lock:
            job = app_module._empty_inbound_job(
                "admin", PACKING_SLIP_NO, "query-2", "查询2"
            )
            job["running"] = True
            app_module.inbound_jobs[job["job_id"]] = job
            app_module.latest_inbound_job_by_slot["query-2"] = job["job_id"]

        with (
            mock.patch.object(app_module.crm_pool, "query_slots", ["query-1", "query-2"]),
            mock.patch.object(app_module.crm_pool, "get", return_value=IdleWorker()),
            mock.patch.object(app_module, "_query_slot_has_running_batch", return_value=False),
            mock.patch.object(app_module, "_query_slot_has_running_service_close", return_value=False),
        ):
            workers, error = app_module._select_idle_query_workers_desc()

        self.assertEqual(error, "")
        self.assertEqual([slot_id for _worker, slot_id, _label in workers], ["query-1"])


if __name__ == "__main__":
    unittest.main()
