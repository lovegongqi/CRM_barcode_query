import json
import os
import tempfile
import unittest
from unittest import mock

import app as app_module


class ServiceCloseHistoryTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.tempdir = tempfile.TemporaryDirectory()
        self.history_file = os.path.join(self.tempdir.name, "service_close_history.json")
        self.accounts_file = os.path.join(self.tempdir.name, "accounts.json")
        self.patches = [
            mock.patch.object(app_module, "CONFIG_DIR", self.tempdir.name),
            mock.patch.object(
                app_module,
                "SERVICE_CLOSE_HISTORY_FILE",
                self.history_file,
                create=True,
            ),
            mock.patch.object(app_module, "ACCOUNTS_FILE", self.accounts_file),
        ]
        for patcher in self.patches:
            patcher.start()
        app_module.save_accounts([
            {
                "id": "admin-id",
                "username": "admin",
                "display_name": "管理员",
                "password": "admin-pass",
                "permissions": ["results"],
                "updated_at": "",
            },
            {
                "id": "viewer-id",
                "username": "viewer",
                "display_name": "查看者",
                "password": "viewer-pass",
                "permissions": ["results"],
                "updated_at": "",
            },
        ])

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tempdir.cleanup()

    def login(self, username):
        client = app_module.app.test_client()
        response = client.post(
            "/api/app-auth/login",
            json={"username": username, "password": f"{username}-pass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        return client

    @staticmethod
    def completed_job(job_id, finished_at, service_no):
        return {
            "job_id": job_id,
            "actor": "admin",
            "running": False,
            "done": True,
            "success": True,
            "error": "",
            "total": 1,
            "current": 1,
            "closed_count": 1,
            "already_closed_count": 0,
            "failed_count": 0,
            "selected_barcodes": ["870000000001"],
            "missing": [],
            "no_service": [],
            "started_at": "2026-09-21 10:00:00",
            "finished_at": finished_at,
            "logs": [{"seq": 1, "message": "已完成", "level": "success"}],
            "service_rows": [{
                "service_no": service_no,
                "barcodes": ["870000000001"],
                "customer_names": ["张三"],
                "product_names": ["净水器"],
                "slot_label": "查询1",
                "state": "closed",
                "message": "已结单",
                "detail_url": f"/api/service-orders/{service_no}",
            }],
            "results": [{"service_no": service_no, "success": True}],
            "worker": object(),
        }

    def test_history_persists_serialized_records_newest_first(self):
        """Removing the durable history layer would lose completed close jobs."""
        older = self.completed_job("close-old", "2026-09-21 10:01:00", "FWD-OLD")
        newer = self.completed_job("close-new", "2026-09-21 10:02:00", "FWD-NEW")

        app_module._append_service_close_history(older)
        app_module._append_service_close_history(newer)

        reloaded = app_module.load_service_close_history()
        self.assertEqual([record["id"] for record in reloaded], ["close-new", "close-old"])
        self.assertEqual(reloaded[0]["actor"], "admin")
        self.assertEqual(reloaded[0]["service_rows"][0]["detail_url"], "/api/service-orders/FWD-NEW")
        self.assertNotIn("worker", reloaded[0])
        with open(self.history_file, encoding="utf-8") as history_file:
            self.assertEqual(json.load(history_file)[0]["id"], "close-new")

        admin = self.login("admin")
        response = admin.get("/api/service-close/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [record["id"] for record in response.get_json()["records"]],
            ["close-new", "close-old"],
        )

    def test_newer_result_replaces_history_for_the_same_service_order(self):
        """A service order must have one latest result instead of repeated history rows."""
        older = self.completed_job("close-first", "2026-09-21 10:01:00", "FWD-SAME")
        older["service_rows"].append({
            "service_no": "FWD-OTHER",
            "barcodes": ["870000000002"],
            "customer_names": ["李四"],
            "product_names": ["滤芯"],
            "slot_label": "查询1",
            "state": "closed",
            "message": "已结单",
            "detail_url": "/api/service-orders/FWD-OTHER",
        })
        older["results"].append({"service_no": "FWD-OTHER", "success": True})
        older.update({"total": 2, "current": 2, "closed_count": 2})
        app_module._append_service_close_history(older)
        app_module._append_service_close_history(
            self.completed_job("close-latest", "2026-09-21 10:02:00", "FWD-SAME")
        )

        records = app_module.load_service_close_history()

        self.assertEqual([record["id"] for record in records], ["close-latest", "close-first"])
        self.assertEqual(records[0]["service_rows"][0]["service_no"], "FWD-SAME")
        self.assertEqual(records[1]["service_rows"][0]["service_no"], "FWD-OTHER")

    def test_loading_existing_history_hides_older_duplicate_service_orders(self):
        """Already-saved duplicate rows must also disappear from the shared view."""
        with open(self.history_file, "w", encoding="utf-8") as history_file:
            json.dump([
                app_module._service_close_history_record(
                    self.completed_job("close-first", "2026-09-21 10:01:00", "FWD-SAME")
                ),
                app_module._service_close_history_record(
                    self.completed_job("close-latest", "2026-09-21 10:02:00", "FWD-SAME")
                ),
            ], history_file, ensure_ascii=False)

        records = app_module.load_service_close_history()

        self.assertEqual([record["id"] for record in records], ["close-latest"])

    def test_only_admin_can_delete_or_clear_shared_history(self):
        """Removing server-side admin checks would let a result viewer erase history."""
        app_module._append_service_close_history(
            self.completed_job("close-one", "2026-09-21 10:01:00", "FWD-ONE")
        )
        app_module._append_service_close_history(
            self.completed_job("close-two", "2026-09-21 10:02:00", "FWD-TWO")
        )

        viewer = self.login("viewer")
        self.assertEqual(viewer.get("/api/service-close/history").status_code, 200)
        self.assertEqual(
            viewer.delete("/api/service-close/history/close-two").status_code,
            403,
        )
        self.assertEqual(viewer.delete("/api/service-close/history").status_code, 403)
        self.assertEqual(
            [record["id"] for record in app_module.load_service_close_history()],
            ["close-two", "close-one"],
        )

        admin = self.login("admin")
        deleted = admin.delete("/api/service-close/history/close-two")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            [record["id"] for record in app_module.load_service_close_history()],
            ["close-one"],
        )
        self.assertEqual(admin.delete("/api/service-close/history").status_code, 200)
        self.assertEqual(app_module.load_service_close_history(), [])

    def test_admin_can_delete_one_service_order_without_removing_its_batch(self):
        record = self.completed_job("close-batch", "2026-09-22 10:00:00", "FWD-ONE")
        record["service_rows"].append({
            "service_no": "FWD-TWO",
            "barcodes": ["870000000002"],
            "customer_names": ["李四"],
            "product_names": ["滤芯"],
            "slot_label": "查询2",
            "state": "closed",
            "message": "已结单",
            "detail_url": "/api/service-orders/FWD-TWO",
        })
        record["results"].append({"service_no": "FWD-TWO", "success": True})
        record.update({"total": 2, "current": 2, "closed_count": 2})
        app_module._append_service_close_history(record)

        admin = self.login("admin")
        response = admin.delete("/api/service-close/history/service/FWD-ONE")

        self.assertEqual(response.status_code, 200)
        records = app_module.load_service_close_history()
        self.assertEqual(len(records), 1)
        self.assertEqual(
            [row["service_no"] for row in records[0]["service_rows"]],
            ["FWD-TWO"],
        )


if __name__ == "__main__":
    unittest.main()
