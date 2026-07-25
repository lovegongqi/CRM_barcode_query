import os
import tempfile
import unittest

import app as app_module


class TransferPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_file = getattr(app_module, "TRANSFER_RECORDS_DB_FILE", None)
        app_module.TRANSFER_RECORDS_DB_FILE = os.path.join(
            self.tempdir.name, "transfer_records.sqlite3"
        )
        self.client = app_module.app.test_client()
        response = self.client.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def tearDown(self):
        if self.original_db_file is None:
            delattr(app_module, "TRANSFER_RECORDS_DB_FILE")
        else:
            app_module.TRANSFER_RECORDS_DB_FILE = self.original_db_file
        self.tempdir.cleanup()

    @staticmethod
    def record(record_id="transfer-test-1"):
        return {
            "record_id": record_id,
            "job_id": "job-test-1",
            "slot_id": "transfer-1",
            "slot_label": "移库1",
            "order_no": "TRSF202607240036",
            "state": "success",
            "status": "移库成功",
            "distributor": "测试分销商",
            "started_at": "2026-07-24 10:00:00",
            "finished_at": "2026-07-24 10:18:18",
            "elapsed": 1098,
            "transfer_type": "移出",
            "remark": "测试备注",
            "logs": [{"time": "10:18:18", "message": "移库单已保存", "level": "success"}],
        }

    def test_transfer_record_survives_reload_and_keeps_order_and_logs(self):
        response = self.client.post("/api/transfer-records", json=self.record())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertTrue(os.path.exists(app_module.TRANSFER_RECORDS_DB_FILE))

        first = self.client.get("/api/transfer-records")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["records"][0]["order_no"], "TRSF202607240036")
        self.assertEqual(first.get_json()["records"][0]["logs"][0]["message"], "移库单已保存")

        second = app_module.app.test_client()
        login = second.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertTrue(login.get_json()["success"])
        reloaded = second.get("/api/transfer-records")
        self.assertEqual(reloaded.get_json()["records"][0]["record_id"], "transfer-test-1")

    def test_update_delete_one_and_clear_all_are_explicit(self):
        self.client.post("/api/transfer-records", json=self.record("one"))
        self.client.post("/api/transfer-records", json=self.record("two"))

        updated = self.record("one")
        updated["status"] = "正在生成 CRM 移库单"
        updated["state"] = "running"
        updated["order_no"] = ""
        response = self.client.post("/api/transfer-records", json=updated)
        self.assertEqual(response.status_code, 200)
        rows = self.client.get("/api/transfer-records").get_json()["records"]
        row = next(item for item in rows if item["record_id"] == "one")
        self.assertEqual(row["status"], "正在生成 CRM 移库单")
        self.assertEqual(row["order_no"], "")

        deleted = self.client.delete("/api/transfer-records/one")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.client.get("/api/transfer-records").get_json()["records"]), 1)

        cleared = self.client.delete("/api/transfer-records")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(self.client.get("/api/transfer-records").get_json()["records"], [])

    def test_unauthenticated_transfer_record_api_is_protected(self):
        client = app_module.app.test_client()
        response = client.get("/api/transfer-records")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
