import atexit
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
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

    def test_bulk_login_uses_remembered_credentials_when_form_is_blank(self):
        slots = [{"id": "query-1", "kind": "query", "label": "查询1"}]
        with (
            mock.patch.object(
                app_module,
                "get_remembered_crm_credentials",
                return_value={"remember": True, "username": "crm-user", "password": "crm-password"},
            ),
            mock.patch.object(app_module, "_bulk_login_slots_for_scope", return_value=("all", slots)),
            mock.patch.object(app_module.threading, "Thread") as thread_class,
        ):
            response = self.client.post("/api/crm/bulk-login/start", json={"scope": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["running"])
        thread_class.assert_called_once()
        self.assertEqual(thread_class.call_args.kwargs["args"][1:], ("crm-user", "crm-password"))


class BulkLoginSmsWebhookTest(unittest.TestCase):
    message = "【怡口净水】您的验证码1234，5分钟内有效。如非本人操作请忽略。"

    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.config_dir = tempfile.TemporaryDirectory()
        self.config_patch = mock.patch.object(app_module, "_runtime_config_dir", return_value=self.config_dir.name)
        self.config_patch.start()
        with open(os.path.join(self.config_dir.name, "crm_sms_webhook_token"), "w") as output:
            output.write("test-secret-token\n")
        self.job = app_module._empty_bulk_login_job(
            "all", [{"id": "query-1", "kind": "query", "label": "查询1"}],
        )
        self.job.update({"running": True, "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self.job["slots"][0]["status"] = "waiting_captcha"
        with app_module.bulk_login_job_lock:
            self.saved_jobs = dict(app_module.bulk_login_jobs)
            app_module.bulk_login_jobs.clear()
            app_module.bulk_login_jobs[self.job["job_id"]] = self.job

    def tearDown(self):
        with app_module.bulk_login_job_lock:
            app_module.bulk_login_jobs.clear()
            app_module.bulk_login_jobs.update(self.saved_jobs)
        self.config_patch.stop()
        self.config_dir.cleanup()

    def post_sms(self, message=None, token="test-secret-token"):
        return self.client.post(
            "/api/crm/sms-webhook",
            json={"message": self.message if message is None else message, "sender": "10612345"},
            headers={"X-Webhook-Token": token} if token is not None else {},
        )

    def test_valid_sms_schedules_existing_bulk_login_without_app_session(self):
        with mock.patch.object(app_module.threading, "Thread") as thread_class:
            response = self.post_sms()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"success": True, "accepted": True})
        self.assertEqual(self.job["captcha"], "1234")
        self.assertTrue(self.job["captcha_submitting"])
        thread_class.return_value.start.assert_called_once_with()

    def test_missing_or_wrong_token_cannot_change_job(self):
        self.assertEqual(self.post_sms(token=None).status_code, 403)
        self.assertEqual(self.post_sms(token="wrong").status_code, 403)
        self.assertEqual(self.job["captcha"], "")

    def test_receiver_is_disabled_without_persistent_secret(self):
        os.remove(os.path.join(self.config_dir.name, "crm_sms_webhook_token"))
        self.assertEqual(self.post_sms().status_code, 503)
        self.assertEqual(self.job["captcha"], "")

    def test_non_crm_sms_or_wrong_code_length_is_rejected(self):
        self.assertEqual(self.post_sms("您的验证码1234，5分钟内有效").get_json()["accepted"], False)
        self.assertEqual(self.post_sms("【怡口净水】您的验证码123456，5分钟内有效").get_json()["accepted"], False)
        self.assertEqual(self.job["captcha"], "")

    def test_no_active_or_old_job_is_rejected(self):
        self.job["running"] = False
        response = self.post_sms()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["accepted"])
        self.job["running"] = True
        self.job["started_at"] = (datetime.now() - timedelta(minutes=11)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertFalse(self.post_sms().get_json()["accepted"])

    def test_duplicate_sms_does_not_submit_twice(self):
        with mock.patch.object(app_module.threading, "Thread") as thread_class:
            self.assertEqual(self.post_sms().status_code, 202)
            self.job["captcha_submitting"] = False
            response = self.post_sms()
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()["accepted"])
        self.assertEqual(thread_class.call_count, 1)

    def test_multiple_active_jobs_are_rejected(self):
        other = app_module._empty_bulk_login_job(
            "query", [{"id": "query-2", "kind": "query", "label": "查询2"}],
        )
        other.update({"running": True, "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        other["slots"][0]["status"] = "opening"
        with app_module.bulk_login_job_lock:
            app_module.bulk_login_jobs[other["job_id"]] = other
        response = self.post_sms()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["accepted"])
        self.assertEqual(self.job["captcha"], "")


if __name__ == "__main__":
    unittest.main()
