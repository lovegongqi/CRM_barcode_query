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


class BulkLoginCaptchaTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        login = self.client.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertEqual(login.status_code, 200)
        self.job = app_module._empty_bulk_login_job(
            "all",
            [
                {"id": "query-1", "kind": "query", "label": "查询1"},
                {"id": "query-2", "kind": "query", "label": "查询2"},
                {"id": "transfer-1", "kind": "transfer", "label": "移库1"},
            ],
        )
        self.job.update({"running": True, "captcha": "2468", "step1_done": True})
        for slot in self.job["slots"]:
            slot["status"] = "waiting_captcha"
        with app_module.bulk_login_job_lock:
            app_module.bulk_login_jobs[self.job["job_id"]] = self.job

    def tearDown(self):
        with app_module.bulk_login_job_lock:
            app_module.bulk_login_jobs.pop(self.job["job_id"], None)

    def test_one_captcha_submission_runs_all_waiting_channels_concurrently(self):
        state_lock = threading.Lock()
        all_started = threading.Event()
        release = threading.Event()
        state = {"active": 0, "max_active": 0}

        class Worker:
            def login_step2(self, captcha):
                self.assert_captcha(captcha)
                with state_lock:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                    if state["active"] == 3:
                        all_started.set()
                release.wait(0.5)
                with state_lock:
                    state["active"] -= 1
                return True, "登录成功"

            @staticmethod
            def assert_captcha(captcha):
                if captcha != "2468":
                    raise AssertionError(f"unexpected captcha: {captcha}")

        workers = {slot["id"]: Worker() for slot in self.job["slots"]}

        def run_submission():
            with mock.patch.object(
                app_module.crm_pool,
                "get",
                side_effect=lambda slot_id, _kind: workers[slot_id],
            ):
                app_module._submit_bulk_login_pending(self.job["job_id"])

        thread = threading.Thread(target=run_submission)
        thread.start()
        try:
            self.assertTrue(all_started.wait(0.2), "等待中的通道没有同时提交验证码")
        finally:
            release.set()
            thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(state["max_active"], 3)
        self.assertTrue(self.job["done"])
        self.assertTrue(self.job["success"])

    def test_repeated_click_while_dispatching_starts_only_one_batch(self):
        with mock.patch.object(app_module.threading, "Thread") as thread_class:
            first = self.client.post(
                "/api/crm/bulk-login/captcha",
                json={"scope": "all", "job_id": self.job["job_id"], "captcha": "2468"},
            )
            second = self.client.post(
                "/api/crm/bulk-login/captcha",
                json={"scope": "all", "job_id": self.job["job_id"], "captcha": "2468"},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.get_json()["captcha_submitting"])
        self.assertTrue(second.get_json()["captcha_submitting"])
        self.assertEqual(thread_class.call_count, 1)
        thread_class.return_value.start.assert_called_once_with()

    def test_channel_that_reaches_captcha_late_uses_the_same_submitted_code(self):
        first_slot, late_slot, completed_slot = self.job["slots"]
        first_slot["status"] = "waiting_captcha"
        late_slot["status"] = "opening"
        completed_slot["status"] = "logged_in"
        self.job["step1_done"] = False

        class FirstWorker:
            def login_step2(self, _captcha):
                return False, "验证码可能错误"

        class LateWorker:
            def __init__(self):
                self.captchas = []

            def login_step1(self, _username, _password):
                return True, "等待验证码"

            def login_step2(self, captcha):
                self.captchas.append(captcha)
                return True, "登录成功"

        late_worker = LateWorker()
        workers = {
            first_slot["id"]: FirstWorker(),
            late_slot["id"]: late_worker,
        }
        with mock.patch.object(
            app_module.crm_pool,
            "get",
            side_effect=lambda slot_id, _kind: workers[slot_id],
        ):
            app_module._submit_bulk_login_pending(self.job["job_id"])
            app_module._run_bulk_login_one_slot(
                self.job["job_id"], dict(late_slot), "crm-user", "crm-password"
            )

        self.assertEqual(late_worker.captchas, ["2468"])
        self.assertEqual(late_slot["status"], "logged_in")


if __name__ == "__main__":
    unittest.main()
