import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import app as app_module


class FakeQueryWorker:
    def __init__(self, slot_id, delay=0.01, logged_in=True):
        self.slot_id = slot_id
        self.logged_in = logged_in
        self.remembered_logged_in = logged_in
        self.delay = delay
        self.stop_requested = False
        self.started = threading.Event()
        self.queried_barcodes = []

    def clear_stop(self):
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def is_stop_requested(self):
        return self.stop_requested

    def check_login_status(self):
        return True, "已登录"

    def query_barcode(self, barcode, log=None, output_dir=None):
        self.queried_barcodes.append(barcode)
        self.started.set()
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


class BlockingServiceCloseWorker:
    def __init__(self, products=None, detail_url="", slot_id="query-1"):
        self.slot_id = slot_id
        self.products = list(products or [])
        self.detail_url = detail_url
        self.started = threading.Event()
        self.release = threading.Event()

    def close_service_orders(self, orders, log=None, progress=None):
        row = orders[0]
        if log:
            log("正在搜索服务单", "info")
        merged = app_module._merge_service_order_products(
            row,
            self.products,
            selected_barcodes=row.get("selected_barcodes") or row.get("barcodes"),
        )
        if self.detail_url:
            merged["detail_url"] = self.detail_url
        if progress:
            progress(row=merged)
        self.started.set()
        self.release.wait(timeout=2)
        return True, {
            "results": [{
                "service_no": row["service_no"],
                "barcodes": list(merged.get("barcodes") or []),
                "selected_barcodes": list(merged.get("selected_barcodes") or []),
                "related_barcodes": list(merged.get("related_barcodes") or []),
                "service_products": list(merged.get("service_products") or []),
                "detail_url": merged.get("detail_url") or "",
                "customer_names": list(row.get("customer_names") or []),
                "success": True,
                "status": "closed",
                "message": "已结单",
            }],
        }


class FakeRelatedOrderWorker:
    def __init__(self, result, release=None):
        self.slot_id = "query-2"
        self.result = result
        self.release = release

    def query_related_order_products(self, service_no, log=None):
        if self.release:
            self.release.wait(timeout=2)
        return self.result


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
        if hasattr(app_module, "order_product_job_lock"):
            with app_module.order_product_job_lock:
                app_module.order_product_jobs.clear()
                app_module.latest_order_product_job_by_service.clear()
        with app_module.priority_query_work_lock:
            app_module.priority_query_waiters.clear()
            app_module.priority_query_slot_reservations.clear()

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

    def wait_for_order_products(self, service_no, job_id=None, timeout=3):
        deadline = time.time() + timeout
        payload = None
        while time.time() < deadline:
            response = self.client.get(
                f"/api/service-orders/{service_no}/order-products/status",
                query_string={"job_id": job_id} if job_id else None,
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            if payload.get("done"):
                return payload
            time.sleep(0.02)
        self.fail(f"order product query did not finish: {payload}")

    @staticmethod
    def dispatch_order_product_now(worker):
        def dispatch_now(kind, job_id, launch):
            if kind != "order_products":
                raise AssertionError(f"unexpected priority work kind: {kind}")
            with app_module.priority_query_work_lock:
                app_module.priority_query_slot_reservations[worker.slot_id] = job_id
            launch(worker, worker.slot_id, "查询2")

        return dispatch_now

    def test_order_product_job_persists_complete_comparison_and_releases_channel(self):
        service_no = "FWD20260914001"
        detail = {
            "service_no": service_no,
            "legacy_note": "keep me",
            "fields": [{"label": "客户名称", "value": "旧客户"}],
            "products": [
                {"product_name": "A", "product_code": "A-01", "barcode": "SN-A1"},
                {"product_name": "B", "product_code": "B-02", "barcode": "SN-B1"},
            ],
        }
        service_fields = [
            {"label": "客户名称", "value": "新客户"},
            {"label": "关联订单号", "value": "SO20260914001"},
        ]
        order_products = [
            {"product_name": "A", "product_code": "A-01", "quantity": 1},
            {"product_name": "C", "product_code": "C-03", "quantity": 2},
        ]
        worker = FakeRelatedOrderWorker((True, {
            "service_no": service_no,
            "order_no": "SO20260914001",
            "service_fields": service_fields,
            "order_products": order_products,
        }))

        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module, "SERVICE_ORDER_DIR", tempdir
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=self.dispatch_order_product_now(worker),
        ):
            app_module._write_service_order_detail(service_no, detail)
            response = self.client.post(
                f"/api/service-orders/{service_no}/order-products/start"
            )
            self.assertEqual(response.status_code, 200)
            job_id = response.get_json()["job_id"]
            status = self.wait_for_order_products(service_no)

            self.assertEqual(status["job_id"], job_id)
            self.assertEqual(status["order_no"], "SO20260914001")
            self.assertEqual(status["slot_label"], "查询2")
            self.assertTrue(status["success"])
            self.assertEqual(status["detail_url"], f"/api/service-orders/{service_no}")
            self.assertNotIn(worker.slot_id, app_module.priority_query_slot_reservations)

            with open(os.path.join(tempdir, f"{service_no}.json"), encoding="utf-8") as file:
                saved = json.load(file)
            self.assertEqual(saved["legacy_note"], "keep me")
            self.assertEqual(saved["fields"], service_fields)
            self.assertEqual(saved["order_lookup"]["order_no"], "SO20260914001")
            self.assertEqual(saved["order_lookup"]["products"], order_products)
            self.assertEqual(saved["order_lookup"]["comparison"], [
                {"product_code": "A-01", "product_name": "A", "order_quantity": 1,
                 "service_quantity": 1, "status": "matched", "status_label": "一致"},
                {"product_code": "B-02", "product_name": "B", "order_quantity": 0,
                 "service_quantity": 1, "status": "order_missing", "status_label": "订单缺少"},
                {"product_code": "C-03", "product_name": "C", "order_quantity": 2,
                 "service_quantity": 0, "status": "service_missing", "status_label": "服务单缺少"},
            ])
            self.assertTrue(saved["order_lookup"]["queried_at"])

    def test_order_product_start_rejects_duplicate_running_service_job(self):
        service_no = "FWD20260914001"
        release = threading.Event()
        worker = FakeRelatedOrderWorker((False, {"error": "CRM 未登录"}), release=release)
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module, "SERVICE_ORDER_DIR", tempdir
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=self.dispatch_order_product_now(worker),
        ):
            app_module._write_service_order_detail(service_no, {
                "service_no": service_no,
                "products": [],
            })
            first = self.client.post(
                f"/api/service-orders/{service_no}/order-products/start"
            )
            second = self.client.post(
                f"/api/service-orders/{service_no}/order-products/start"
            )

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.get_json()["job_id"], first.get_json()["job_id"])
            self.assertTrue(second.get_json()["running"])

            release.set()
            status = self.wait_for_order_products(service_no, first.get_json()["job_id"])
            self.assertFalse(status["success"])
            self.assertEqual(status["stage"], "failed")
            self.assertNotIn(worker.slot_id, app_module.priority_query_slot_reservations)

    def test_failed_order_product_refresh_preserves_successful_cache_bytes(self):
        service_no = "FWD20260914001"
        old_detail = {
            "service_no": service_no,
            "fields": [{"label": "关联订单号", "value": "SO-OLD"}],
            "products": [{"product_name": "A", "product_code": "A-01", "barcode": "SN-A1"}],
            "order_lookup": {
                "order_no": "SO-OLD",
                "products": [{"product_name": "A", "product_code": "A-01", "quantity": 1}],
                "comparison": [{"product_code": "A-01", "status": "matched"}],
                "queried_at": "2026-09-14 12:00:00",
            },
        }
        worker = FakeRelatedOrderWorker((False, {"error": "订单详情未读取到产品明细"}))

        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module, "SERVICE_ORDER_DIR", tempdir
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=self.dispatch_order_product_now(worker),
        ):
            filepath = os.path.join(tempdir, f"{service_no}.json")
            os.makedirs(tempdir, exist_ok=True)
            original = (
                json.dumps(old_detail, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode()
            with open(filepath, "wb") as file:
                file.write(original)

            response = self.client.post(
                f"/api/service-orders/{service_no}/order-products/start"
            )
            self.assertEqual(response.status_code, 200)
            status = self.wait_for_order_products(service_no, response.get_json()["job_id"])

            self.assertFalse(status["success"])
            self.assertEqual(status["error"], "订单详情未读取到产品明细")
            with open(filepath, "rb") as file:
                self.assertEqual(file.read(), original)
            self.assertNotIn(worker.slot_id, app_module.priority_query_slot_reservations)

    def test_order_product_cache_write_failure_preserves_existing_file_bytes(self):
        service_no = "FWD20260914001"
        worker = FakeRelatedOrderWorker((True, {
            "service_no": service_no,
            "order_no": "SO20260914001",
            "service_fields": [{"label": "关联订单号", "value": "SO20260914001"}],
            "order_products": [{
                "product_name": "A",
                "product_code": "A-01",
                "quantity": 1,
                "invalid_json_value": object(),
            }],
        }))

        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module, "SERVICE_ORDER_DIR", tempdir
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=self.dispatch_order_product_now(worker),
        ):
            filepath = os.path.join(tempdir, f"{service_no}.json")
            original = b'{"service_no":"FWD20260914001","products":[]}\n'
            with open(filepath, "wb") as file:
                file.write(original)

            response = self.client.post(
                f"/api/service-orders/{service_no}/order-products/start"
            )
            status = self.wait_for_order_products(service_no, response.get_json()["job_id"])

            self.assertFalse(status["success"])
            with open(filepath, "rb") as file:
                self.assertEqual(file.read(), original)
            self.assertNotIn(worker.slot_id, app_module.priority_query_slot_reservations)

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

    def test_background_query_adds_a_slot_that_logs_in_while_job_is_running(self):
        first = FakeQueryWorker("query-1", delay=0.15)
        later = FakeQueryWorker("query-2", delay=0.01, logged_in=False)
        barcodes = [f"79250000001{index:02d}" for index in range(6)]
        job = app_module._empty_background_query_job(
            "admin",
            barcodes,
            ["query-1", "query-2"],
            retry_limit=0,
        )
        job.update({
            "running": True,
            "started_at": "2026-08-06 10:00:00",
        })
        with app_module.background_query_job_lock:
            app_module.background_query_jobs[job["job_id"]] = job

        thread = threading.Thread(
            target=app_module._run_background_query_job,
            args=(
                job["job_id"],
                [
                    (first, "query-1", "查询1"),
                    (later, "query-2", "查询2"),
                ],
            ),
        )
        thread.start()

        try:
            self.assertTrue(first.started.wait(timeout=1))
            deadline = time.time() + 1
            waiting_logged = False
            while time.time() < deadline:
                with app_module.background_query_job_lock:
                    current = app_module.background_query_jobs.get(job["job_id"]) or {}
                    waiting_logged = any(
                        "等待登录后自动加入" in row["message"]
                        for row in current.get("logs") or []
                    )
                if waiting_logged:
                    break
                time.sleep(0.01)
            self.assertTrue(waiting_logged)
            later.logged_in = True
            later.remembered_logged_in = True
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertGreater(len(later.queried_barcodes), 0)
            finished = app_module._background_query_status_payload(
                app_module.background_query_jobs[job["job_id"]]
            )
            self.assertTrue(finished["done"])
            self.assertEqual(finished["success_count"], len(barcodes))
            self.assertTrue(any(
                "自动加入当前查询队列" in row["message"]
                for row in finished["logs"]
            ))
        finally:
            first.request_stop()
            later.request_stop()
            thread.join(timeout=1)
            with app_module.background_query_job_lock:
                app_module.background_query_jobs.pop(job["job_id"], None)

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

    def test_transfer_summary_initializes_and_updates_each_representative_barcode_state(self):
        job = app_module._empty_summary_job("transfer-1")
        job.update({
            "running": True,
            "started_at": "2026-08-21 17:45:00",
        })
        with app_module.summary_job_lock:
            app_module.summary_jobs[job["job_id"]] = job

        observed = {}

        def fake_ensure(_barcodes, _log, _worker, state_tracker=None):
            with app_module.summary_job_lock:
                observed["pending"] = dict(app_module.summary_jobs[job["job_id"]].get("barcode_states") or {})
            state_tracker("REP-001", "querying", "查询1")
            state_tracker("REP-001", "ok", "查询1")
            state_tracker("REP-002", "querying", "查询2")
            state_tracker("REP-002", "failed", "查询2")
            return {"queried": [], "failed": []}

        try:
            with mock.patch.object(
                app_module,
                "filter_disassembly_barcodes",
                return_value=(["REP-001", "REP-002"], []),
            ), mock.patch.object(
                app_module,
                "_missing_product_library_representatives",
                return_value={"P1": "REP-001", "P2": "REP-002"},
            ), mock.patch.object(
                app_module,
                "_crm_ready_for_auto_query",
                return_value=(True, "已登录"),
            ), mock.patch.object(
                app_module,
                "ensure_product_library_for_barcodes",
                side_effect=fake_ensure,
            ), mock.patch.object(
                app_module,
                "build_transfer_summary",
                return_value={"groups": [], "details": [], "missing": [], "incomplete": [], "blocked": []},
            ), mock.patch.object(
                app_module,
                "_exclude_unmatched_transfer_barcodes",
                return_value=[],
            ):
                app_module._run_summary_job(
                    job["job_id"],
                    object(),
                    ["REP-001", "REP-002"],
                    "移出",
                    "测试分销商",
                )

            self.assertEqual(
                observed["pending"],
                {
                    "REP-001": {"state": "pending", "channel": "", "channel_label": "", "message": "等待查询", "level": "dim"},
                    "REP-002": {"state": "pending", "channel": "", "channel_label": "", "message": "等待查询", "level": "dim"},
                },
            )
            with app_module.summary_job_lock:
                states = dict(app_module.summary_jobs[job["job_id"]]["barcode_states"])
            self.assertEqual(states["REP-001"]["state"], "ok")
            self.assertEqual(states["REP-001"]["channel_label"], "查询1")
            self.assertEqual(states["REP-002"]["state"], "failed")
            self.assertEqual(states["REP-002"]["channel_label"], "查询2")
            with app_module.summary_job_lock:
                messages = [row["message"] for row in app_module.summary_jobs[job["job_id"]]["logs"]]
            self.assertFalse(any(message.startswith("代表条码 REP-") for message in messages))
        finally:
            with app_module.summary_job_lock:
                app_module.summary_jobs.pop(job["job_id"], None)

    def test_representative_barcode_state_receives_each_query_process_message(self):
        worker = FakeQueryWorker("query-1", delay=0)
        updates = []

        def track(barcode, state, channel, message="", level="dim"):
            updates.append((barcode, state, channel, message, level))

        with mock.patch.object(
            app_module,
            "_missing_product_library_representatives",
            return_value={"347": "3472311270178"},
        ), mock.patch.object(
            app_module.crm_pool,
            "query_slots",
            ["query-1"],
        ), mock.patch.object(
            app_module.crm_pool,
            "get",
            return_value=worker,
        ), mock.patch.object(
            app_module,
            "existing_barcode_result_paths",
            return_value=[],
        ), mock.patch.object(
            app_module,
            "barcode_metadata_exists",
            return_value=False,
        ), mock.patch.object(
            app_module,
            "delete_temporary_query_result",
        ), mock.patch.object(
            app_module,
            "match_product_library",
            return_value={"product_code": "P-347", "product_name": "测试产品"},
        ):
            app_module.ensure_product_library_for_barcodes(
                ["3472311270178"],
                worker=worker,
                state_tracker=track,
            )

        process_messages = [
            message
            for barcode, state, channel, message, _level in updates
            if barcode == "3472311270178" and state == "querying" and channel == "查询1"
        ]
        self.assertIn("正在查询：3472311270178", process_messages)
        self.assertIn("查询完成：3472311270178", process_messages)
        self.assertEqual(updates[-1], ("3472311270178", "ok", "查询1", "查询成功", "success"))

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

    def test_service_close_status_returns_one_realtime_row_per_service_order(self):
        orders = [
            {
                "service_no": "FWD202608050001",
                "barcodes": ["8722507290847", "3402512080268"],
                "customer_names": ["刘总"],
                "product_names": ["反渗透净水机"],
            },
            {
                "service_no": "FWD202608050002",
                "barcodes": ["7132408080189"],
                "customer_names": ["张晶盛"],
                "product_names": ["软水机"],
            },
        ]
        worker = BlockingServiceCloseWorker(
            products=[{
                "barcode": "435221024H397",
                "product_name": "关联设备",
                "product_code": "916200009",
            }],
            detail_url="/api/service-orders/FWD202608050001",
        )
        job = app_module._empty_service_close_job("query-1", orders)
        job.update({
            "running": True,
            "started_at": "2026-08-05 14:46:40",
            "slot_ids": ["query-1"],
        })
        with app_module.service_close_job_lock:
            app_module.service_close_jobs[job["job_id"]] = job
            app_module.latest_service_close_job_id = job["job_id"]
            app_module.latest_service_close_job_id = job["job_id"]

        thread = threading.Thread(
            target=app_module._run_service_close_job,
            args=(job["job_id"], [(worker, "query-1", "查询1")], orders),
        )
        thread.start()
        self.assertTrue(worker.started.wait(timeout=1))

        try:
            response = self.client.get(
                "/api/service-close/status",
                query_string={"job_id": job["job_id"], "slot_id": "query-1"},
            )
            self.assertEqual(response.status_code, 200)
            rows = response.get_json()["service_rows"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["service_no"], "FWD202608050001")
            self.assertEqual(rows[0]["selected_barcodes"], ["8722507290847", "3402512080268"])
            self.assertEqual(rows[0]["related_barcodes"], ["435221024H397"])
            self.assertEqual(
                rows[0]["barcodes"],
                ["8722507290847", "3402512080268", "435221024H397"],
            )
            self.assertEqual(rows[0]["slot_label"], "查询1")
            self.assertEqual(rows[0]["state"], "running")
            self.assertEqual(rows[0]["message"], "正在搜索服务单")
            self.assertEqual(
                rows[0]["detail_url"],
                "/api/service-orders/FWD202608050001",
            )
            self.assertEqual(rows[1]["state"], "waiting")

            worker.release.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

            finished = self.client.get(
                "/api/service-close/status",
                query_string={"job_id": job["job_id"], "slot_id": "query-1"},
            ).get_json()
            self.assertEqual(
                [row["state"] for row in finished["service_rows"]],
                ["closed", "closed"],
            )
            self.assertTrue(all(row["finished_at"] for row in finished["service_rows"]))
        finally:
            worker.release.set()
            thread.join(timeout=2)
            with app_module.service_close_job_lock:
                app_module.service_close_jobs.pop(job["job_id"], None)
                if getattr(app_module, "latest_service_close_job_id", "") == job["job_id"]:
                    app_module.latest_service_close_job_id = ""

    def test_service_close_latest_status_is_shared_between_clients(self):
        """Removing the shared latest job ID would make another browser see an empty job."""
        job = app_module._empty_service_close_job("query-1", [{
            "service_no": "FWD202609210001",
            "barcodes": ["870000000001"],
        }])
        job.update({
            "running": True,
            "started_at": "2026-09-21 12:00:00",
            "logs": [{"id": 1, "message": "正在处理", "level": "info", "time": "12:00:01"}],
            "log_seq": 1,
        })
        with app_module.service_close_job_lock:
            app_module.service_close_jobs[job["job_id"]] = job
            app_module.latest_service_close_job_id = job["job_id"]

        second_client = app_module.app.test_client()
        second_client.post("/api/app-auth/login", json={"username": "admin", "password": "88293529"})
        try:
            response = second_client.get("/api/service-close/status?latest=1")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["job_id"], job["job_id"])
            self.assertTrue(payload["running"])
            self.assertEqual(payload["service_rows"][0]["service_no"], "FWD202609210001")
            self.assertEqual(payload["logs"][0]["message"], "正在处理")

            with app_module.service_close_job_lock:
                job["running"] = False
                job["done"] = True
                job["finished_at"] = "2026-09-21 12:01:00"
            finished = second_client.get("/api/service-close/status?latest=1").get_json()
            self.assertTrue(finished["done"])
            self.assertFalse(finished["running"])
        finally:
            with app_module.service_close_job_lock:
                app_module.service_close_jobs.pop(job["job_id"], None)
                if app_module.latest_service_close_job_id == job["job_id"]:
                    app_module.latest_service_close_job_id = ""

    def test_service_close_start_uses_all_available_query_channels(self):
        orders = [
            {"service_no": f"FWD20260914000{index}", "barcodes": [f"792500000000{index}"]}
            for index in range(1, 4)
        ]
        workers = [
            BlockingServiceCloseWorker(slot_id=f"query-{index}")
            for index in range(1, 4)
        ]

        def dispatch_now(_kind, job_id, launch):
            with app_module.priority_query_work_lock:
                app_module.priority_query_slot_reservations["query-1"] = job_id
            launch(workers[0], "query-1", "查询1")

        with mock.patch.object(
            app_module,
            "selected_latest_service_orders",
            return_value={"orders": orders, "missing": [], "no_service": []},
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=dispatch_now,
        ), mock.patch.object(
            app_module,
            "_select_idle_query_workers_desc",
            return_value=(
                [
                    (workers[1], "query-2", "查询2"),
                    (workers[2], "query-3", "查询3"),
                ],
                "",
            ),
        ):
            response = self.client.post(
                "/api/service-close/start",
                json={"barcodes": [row["barcodes"][0] for row in orders]},
            )

        self.assertEqual(response.status_code, 200)
        job_id = response.get_json()["job_id"]
        try:
            self.assertTrue(workers[0].started.wait(timeout=1))
            self.assertTrue(workers[1].started.wait(timeout=1))
            self.assertTrue(workers[2].started.wait(timeout=1))
            status = self.client.get(
                "/api/service-close/status",
                query_string={"job_id": job_id},
            ).get_json()
            self.assertEqual(
                status["slot_ids"],
                ["query-1", "query-2", "query-3"],
            )
            self.assertEqual(
                {row["slot_label"] for row in status["service_rows"]},
                {"查询1", "查询2", "查询3"},
            )
        finally:
            for worker in workers:
                worker.release.set()
            deadline = time.time() + 2
            while time.time() < deadline:
                with app_module.service_close_job_lock:
                    current = app_module.service_close_jobs.get(job_id) or {}
                    if current.get("done"):
                        break
                time.sleep(0.01)
            with app_module.service_close_job_lock:
                app_module.service_close_jobs.pop(job_id, None)
                for slot_id in ["query-1", "query-2", "query-3"]:
                    app_module.latest_service_close_job_by_slot.pop(slot_id, None)
            with app_module.priority_query_work_lock:
                for slot_id in ["query-1", "query-2", "query-3"]:
                    app_module.priority_query_slot_reservations.pop(slot_id, None)

    def test_service_close_start_returns_before_the_scheduler_finds_a_channel(self):
        """A queued close job must let the browser enter the management page immediately."""
        scheduler_started = threading.Event()
        release_scheduler = threading.Event()

        def wait_for_scheduler(_kind, _job_id, _launch):
            scheduler_started.set()
            release_scheduler.wait(timeout=2)

        with mock.patch.object(
            app_module,
            "selected_latest_service_orders",
            return_value={
                "orders": [{"service_no": "FWD202609220001", "barcodes": ["890000000001"]}],
                "missing": [],
                "no_service": [],
            },
        ), mock.patch.object(
            app_module,
            "enqueue_priority_query_work",
            side_effect=wait_for_scheduler,
        ):
            started_at = time.monotonic()
            response = self.client.post("/api/service-close/start", json={"barcodes": ["890000000001"]})
            self.assertLess(time.monotonic() - started_at, 0.5)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["success"])
            self.assertTrue(scheduler_started.wait(timeout=1))

        release_scheduler.set()

    def test_service_close_merges_selected_and_detail_product_barcodes(self):
        row = {
            "service_no": "FWD202608050003",
            "barcodes": ["8722507290847"],
            "customer_names": ["刘总"],
            "product_names": [],
        }
        products = [
            {
                "barcode": "8722507290847",
                "product_name": "反渗透净水机 ERO162A",
                "product_model": "ERO162A",
                "product_code": "916200001",
            },
            {
                "barcode": "3402512080268",
                "product_name": "前置过滤器",
                "product_code": "916200002",
            },
            {
                "barcode": "7132408080189",
                "product_name": "软水机",
                "product_code": "916200003",
            },
        ]

        merged = app_module._merge_service_order_products(
            row,
            products,
            selected_barcodes=["8722507290847"],
        )

        self.assertEqual(merged["selected_barcodes"], ["8722507290847"])
        self.assertEqual(
            merged["barcodes"],
            ["8722507290847", "3402512080268", "7132408080189"],
        )
        self.assertEqual(
            merged["related_barcodes"],
            ["3402512080268", "7132408080189"],
        )
        self.assertEqual(
            merged["product_names"],
            ["反渗透净水机 ERO162A", "前置过滤器", "软水机"],
        )
        self.assertEqual(merged["service_products"], products)

    def test_service_order_detail_is_saved_and_served_as_json(self):
        with tempfile.TemporaryDirectory() as tempdir, mock.patch.object(
            app_module,
            "SERVICE_ORDER_DIR",
            tempdir,
            create=True,
        ):
            detail_url = app_module._write_service_order_detail(
                "FWD202608050003",
                {
                    "service_no": "FWD202608050003",
                    "fields": [
                        {"label": "客户名称", "value": "刘总"},
                        {"label": "联系电话", "value": "13800000000"},
                    ],
                    "products": [
                        {
                            "product_model": "ERO162A",
                            "product_name": "反渗透净水机",
                            "product_code": "916200001",
                            "barcode": "8722507290847",
                        },
                        {
                            "product_model": "",
                            "product_name": "反渗透净水机 三维净S系列 ERO270-3",
                            "product_code": "906042841",
                            "barcode": "8462412200275",
                        },
                    ],
                },
            )

            self.assertEqual(
                detail_url,
                "/api/service-orders/FWD202608050003",
            )
            response = self.client.get(detail_url)
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["service_no"], "FWD202608050003")
            self.assertEqual(payload["fields"][0], {"label": "客户名称", "value": "刘总"})
            self.assertEqual(payload["products"][0]["product_model"], "ERO162A")
            self.assertEqual(payload["products"][1]["product_model"], "ERO270-3")

            anonymous = app_module.app.test_client()
            unauthorized = anonymous.get(detail_url)
            self.assertEqual(unauthorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
