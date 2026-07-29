import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import app as app_module


class FakeQueryWorker:
    def __init__(self, slot_id, delay=0.01):
        self.slot_id = slot_id
        self.logged_in = True
        self.remembered_logged_in = True
        self.delay = delay
        self.stop_requested = False

    def clear_stop(self):
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def is_stop_requested(self):
        return self.stop_requested

    def check_login_status(self):
        return True, "已登录"

    def query_barcode(self, barcode, log=None, output_dir=None):
        if log:
            log(f"正在查询：{barcode}", "info")
        deadline = time.time() + self.delay
        while time.time() < deadline:
            if self.stop_requested:
                return False, "查询已被用户停止"
            time.sleep(0.005)
        if self.stop_requested:
            return False, "查询已被用户停止"
        if log:
            log(f"查询完成：{barcode}", "success")
        return True, f"{barcode}.html"


class FakeTransferWorker:
    def create_transfer(self, summary, distributor, transfer_type, remark, log, progress=None):
        log("正在保存移库单", "info")
        if progress:
            progress(order_no="TRSF202607250001")
        log("移库单已保存：TRSF202607250001", "success")
        return True, {
            "order_no": "TRSF202607250001",
            "pending_approval": False,
        }


class BackgroundJobTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        login = self.client.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.get_json()["success"])
        if hasattr(app_module, "background_query_job_lock"):
            with app_module.background_query_job_lock:
                app_module.background_query_jobs.clear()
                app_module.latest_background_query_job_by_owner.clear()

    def wait_for_query(self, job_id, timeout=3):
        deadline = time.time() + timeout
        payload = None
        while time.time() < deadline:
            response = self.client.get(
                "/api/crm/background-batch/status",
                query_string={"job_id": job_id},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            if payload.get("done"):
                return payload
            time.sleep(0.02)
        self.fail(f"background query did not finish: {payload}")

    def test_background_query_finishes_without_frontend_polling(self):
        workers = {
            "query-1": FakeQueryWorker("query-1"),
            "query-2": FakeQueryWorker("query-2"),
        }
        with mock.patch.object(
            app_module,
            "business_config",
            return_value={"batch_retry_limit": 0, "query_slot_ids": list(workers)},
        ), mock.patch.object(
            app_module.crm_pool,
            "get",
            side_effect=lambda slot_id=None, kind="query": workers[slot_id],
        ):
            response = self.client.post(
                "/api/crm/background-batch/start",
                json={
                    "barcodes": ["7925000000001", "7925000000002", "7925000000003"],
                    "slot_ids": ["query-1", "query-2"],
                    "retry_limit": 0,
                },
            )

        self.assertEqual(response.status_code, 200)
        started = response.get_json()
        self.assertTrue(started["success"])
        time.sleep(0.12)  # No status polling while the backend owns the queue.
        finished = self.wait_for_query(started["job_id"])

        self.assertFalse(finished["running"])
        self.assertTrue(finished["done"])
        self.assertEqual(finished["completed"], 3)
        self.assertEqual(finished["success_count"], 3)
        self.assertEqual(finished["failed_count"], 0)
        self.assertEqual(finished["items"], [])

    def test_background_query_uses_runtime_retry_limit(self):
        workers = {"query-1": FakeQueryWorker("query-1")}
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module,
            "RUNTIME_CONFIG_FILE",
            os.path.join(tempdir, "runtime_config.json"),
        ), mock.patch.object(
            app_module.crm_pool,
            "get",
            side_effect=lambda slot_id=None, kind="query": workers[slot_id],
        ):
            app_module.save_runtime_config({
                "batch_retry_limit": 2,
                "query_slot_ids": ["query-1"],
            })
            response = self.client.post(
                "/api/crm/background-batch/start",
                json={
                    "barcodes": ["7925000000009"],
                    "slot_ids": ["query-1"],
                    "retry_limit": 0,
                },
            )

            self.assertEqual(response.status_code, 200)
            started = response.get_json()
            self.assertEqual(started["retry_limit"], 2)
            self.assertEqual(started["slot_ids"], ["query-1"])
            self.wait_for_query(started["job_id"])

    def test_manual_stop_counts_running_and_waiting_barcodes_as_failures(self):
        worker = FakeQueryWorker("query-1", delay=2)
        with mock.patch.object(
            app_module,
            "business_config",
            return_value={"batch_retry_limit": 0, "query_slot_ids": ["query-1"]},
        ), mock.patch.object(
            app_module.crm_pool,
            "get",
            side_effect=lambda slot_id=None, kind="query": worker,
        ):
            response = self.client.post(
                "/api/crm/background-batch/start",
                json={
                    "barcodes": ["7925000000011", "7925000000012", "7925000000013"],
                    "slot_ids": ["query-1"],
                    "retry_limit": 0,
                },
            )
            self.assertEqual(response.status_code, 200)
            job_id = response.get_json()["job_id"]
            time.sleep(0.05)
            stopped = self.client.post(
                "/api/crm/background-batch/stop",
                json={"job_id": job_id},
            )

        self.assertEqual(stopped.status_code, 200)
        finished = self.wait_for_query(job_id)
        self.assertEqual(finished["failed_count"], 3)
        self.assertEqual(set(finished["failed_barcodes"]), {"7925000000011", "7925000000012", "7925000000013"})
        self.assertEqual(
            [item["state"] for item in finished["items"]],
            ["stopped", "stopped", "stopped"],
        )

    def test_latest_query_discovery_replaces_stale_job_id(self):
        stale = app_module._empty_background_query_job(
            "admin",
            ["7925000000101"],
            ["query-1"],
            0,
        )
        stale.update({"done": True, "finished_at": "2026-07-25 10:00:00"})
        latest = app_module._empty_background_query_job(
            "admin",
            ["7925000000102"],
            ["query-1"],
            0,
        )
        latest.update({"done": True, "finished_at": "2026-07-25 10:01:00"})
        with app_module.background_query_job_lock:
            app_module.background_query_jobs[stale["job_id"]] = stale
            app_module.background_query_jobs[latest["job_id"]] = latest
            app_module.latest_background_query_job_by_owner["admin"] = latest["job_id"]

        response = self.client.get(
            "/api/crm/background-batch/status",
            query_string={"job_id": stale["job_id"], "latest": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["job_id"], latest["job_id"])

    def test_background_query_status_only_returns_actionable_rows(self):
        job = app_module._empty_background_query_job(
            "admin",
            ["WAIT", "RUN", "SUCCESS", "ERROR", "STOPPED"],
            ["query-1"],
            0,
        )
        for item, state in zip(
            job["items"],
            ["waiting", "running", "success", "error", "stopped"],
        ):
            item["state"] = state
        job.update({
            "completed": 3,
            "success_count": 1,
            "failed_count": 2,
            "failed_barcodes": ["ERROR", "STOPPED"],
        })

        payload = app_module._background_query_status_payload(job)

        self.assertEqual(
            [item["barcode"] for item in payload["items"]],
            ["RUN", "ERROR", "STOPPED"],
        )
        self.assertEqual(payload["pending_count"], 1)
        self.assertEqual(payload["total"], 5)
        self.assertEqual(payload["completed"], 3)
        self.assertEqual(payload["success_count"], 1)
        self.assertEqual(payload["failed_count"], 2)

    def test_transfer_summary_failure_details_include_reasons(self):
        summary = {
            "missing": [],
            "incomplete": ["INC-001"],
            "excluded": ["MISS-001", "DIS-001"],
            "excluded_unmatched": ["MISS-001"],
        }

        details = app_module._refresh_transfer_summary_failure_details(summary)

        self.assertEqual(
            details,
            [
                {
                    "barcode": "INC-001",
                    "category": "incomplete",
                    "reason": "条码缺少产品名称或产品编码，无法自动汇总",
                },
                {
                    "barcode": "MISS-001",
                    "category": "missing",
                    "reason": "在线查询后仍无产品信息，已临时排除",
                },
                {
                    "barcode": "DIS-001",
                    "category": "excluded",
                    "reason": "拆机条码，已排除",
                },
            ],
        )

    def test_product_library_query_status_returns_logs_since_sequence(self):
        with app_module.library_query_lock:
            app_module.library_query_job.update({
                "logs": [],
                "log_seq": 0,
                "barcode": "162501010001",
            })
        app_module._library_query_log("第一条", "info")
        app_module._library_query_log("第二条", "success")

        response = self.client.get(
            "/api/product-library/query/status",
            query_string={"since": 1},
        )

        payload = response.get_json()
        self.assertEqual([row["id"] for row in payload["logs"]], [2])
        self.assertEqual(payload["log_seq"], 2)

    def test_transfer_job_persists_final_state_without_frontend(self):
        tempdir = tempfile.TemporaryDirectory()
        original_db = app_module.TRANSFER_RECORDS_DB_FILE
        app_module.TRANSFER_RECORDS_DB_FILE = os.path.join(
            tempdir.name, "transfer_records.sqlite3"
        )
        job = app_module._empty_transfer_job(
            slot_id="transfer-1",
            summary={"groups": [], "details": []},
            distributor="测试分销商",
            transfer_type="移出",
            remark="后台移库",
        )
        job.update({
            "record_id": "transfer-background-test",
            "slot_label": "移库1",
            "running": True,
            "started_at": "2026-07-25 10:00:00",
        })
        with app_module.transfer_job_lock:
            app_module.transfer_jobs[job["job_id"]] = job

        try:
            app_module._run_transfer_job(
                job["job_id"],
                FakeTransferWorker(),
                job["summary"],
                job["distributor"],
                job["transfer_type"],
                job["remark"],
            )
            rows = app_module.load_transfer_records()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["record_id"], "transfer-background-test")
            self.assertEqual(rows[0]["state"], "success")
            self.assertEqual(rows[0]["order_no"], "TRSF202607250001")
            self.assertTrue(
                any("移库单已保存" in row["message"] for row in rows[0]["logs"])
            )
        finally:
            with app_module.transfer_job_lock:
                app_module.transfer_jobs.pop(job["job_id"], None)
            app_module.TRANSFER_RECORDS_DB_FILE = original_db
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
