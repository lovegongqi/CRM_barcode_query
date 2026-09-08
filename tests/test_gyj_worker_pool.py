import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import app as app_module


class FakeGYJWorker:
    def __init__(self, slot_id, session_dir, logged_in=True):
        self.owner = slot_id
        self.slot_id = slot_id
        self.session_dir = session_dir
        self.logged_in = logged_in
        self.waiting_captcha = False
        self.browser_running = logged_in
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


class GYJWorkerPoolTest(unittest.TestCase):
    def make_pool(self, logged_in=True):
        workers = {}

        def factory(slot_id, session_dir):
            worker = FakeGYJWorker(slot_id, session_dir, logged_in=logged_in)
            workers[slot_id] = worker
            return worker

        pool = app_module.GYJWorkerPool(worker_factory=factory)
        for slot_id in pool.slot_ids:
            pool.get(slot_id)
        return pool, workers

    def test_defines_five_stable_slots(self):
        pool, workers = self.make_pool()

        self.assertEqual(
            pool.slot_ids,
            ("gyj-1", "gyj-2", "gyj-3", "gyj-4", "gyj-5"),
        )
        self.assertEqual(set(workers), set(pool.slot_ids))

    def test_channel_one_preserves_admin_profile_and_others_are_distinct(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            with mock.patch.dict(os.environ, {"CRM_DATA_DIR": runtime_dir}):
                paths = [app_module._gyj_session_dir(slot_id) for slot_id in app_module.GYJ_SLOT_IDS]

        self.assertEqual(paths[0], os.path.join(runtime_dir, "gyj_session", "admin"))
        self.assertEqual(len(set(paths)), 5)
        self.assertTrue(paths[1].endswith(os.path.join("gyj_session", "gyj-2")))

    def test_rejects_unknown_slot_instead_of_falling_back(self):
        pool, _workers = self.make_pool()

        with self.assertRaisesRegex(ValueError, "无效的 GYJ 通道"):
            pool.get("admin")

    def test_round_robin_rotates_successful_leases(self):
        pool, _workers = self.make_pool()
        selected = []

        for _ in range(7):
            with pool.reserve(timeout=0.1) as worker:
                selected.append(worker.slot_id)

        self.assertEqual(
            selected,
            ["gyj-1", "gyj-2", "gyj-3", "gyj-4", "gyj-5", "gyj-1", "gyj-2"],
        )

    def test_sixth_caller_waits_until_one_of_five_leases_is_released(self):
        pool, _workers = self.make_pool()
        leases = [pool.reserve(timeout=0.1) for _ in range(5)]
        acquired = threading.Event()
        selected = []

        def claim_sixth():
            with pool.reserve(timeout=0.5) as worker:
                selected.append(worker.slot_id)
                acquired.set()

        thread = threading.Thread(target=claim_sixth)
        thread.start()
        time.sleep(0.03)
        self.assertFalse(acquired.is_set())

        leases[2].release()
        self.assertTrue(acquired.wait(0.3))
        thread.join(0.3)
        self.assertEqual(selected, ["gyj-3"])

        for lease in leases:
            lease.release()

    def test_no_logged_in_channel_fails_without_waiting(self):
        pool, _workers = self.make_pool(logged_in=False)
        started = time.monotonic()

        with self.assertRaisesRegex(RuntimeError, "请先登录 GYJ"):
            pool.reserve(timeout=0.5)

        self.assertLess(time.monotonic() - started, 0.1)

    def test_busy_timeout_uses_actionable_message(self):
        pool, _workers = self.make_pool()
        leases = [pool.reserve(timeout=0.1) for _ in range(5)]

        with self.assertRaisesRegex(RuntimeError, "所有 GYJ 通道正忙，请稍后重试"):
            pool.reserve(timeout=0.02)

        for lease in leases:
            lease.release()

    def test_lease_releases_after_exception_and_release_is_idempotent(self):
        pool, _workers = self.make_pool()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with pool.reserve(timeout=0.1):
                raise RuntimeError("boom")

        self.assertFalse(pool.slot_status("gyj-1")["busy"])
        lease = pool.reserve(timeout=0.1)
        lease.release()
        lease.release()
        self.assertFalse(pool.slot_status(lease.slot_id)["busy"])


if __name__ == "__main__":
    unittest.main()
