import atexit
import os
import tempfile
import unittest

from openpyxl import load_workbook


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "1"

import app as app_module


class ExportXlsxTest(unittest.TestCase):
    def setUp(self):
        self.output_dir = tempfile.TemporaryDirectory()
        self.original_barcode_dir = app_module.BARCODE_DIR
        app_module.BARCODE_DIR = self.output_dir.name
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.BARCODE_DIR = self.original_barcode_dir
        self.output_dir.cleanup()

    def test_export_removes_characters_forbidden_in_xlsx_cells(self):
        response = self.client.post("/api/export/xlsx", json={
            "barcodes": [{
                "barcode": "TEST001",
                "time": "2026-07-20 12:00:00",
                "remark": "客户\x00备\x0b注\x1f",
                "fields": {"sr2": {"name1": "客\x0c户"}},
            }]
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        workbook = load_workbook(os.path.join(self.output_dir.name, "export_result.xlsx"))
        self.assertEqual(workbook.active.cell(row=3, column=2).value, "客户备注")
        self.assertEqual(workbook.active.cell(row=3, column=4).value, "客户")
        workbook.close()

    def test_exported_xlsx_is_served_as_a_download(self):
        export_response = self.client.post("/api/export/xlsx", json={
            "barcodes": [{"barcode": "TEST002", "fields": {}}]
        })
        self.assertTrue(export_response.get_json()["success"])

        with self.client.get("/barcode/export_result.xlsx") as download_response:
            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(
                download_response.mimetype,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.assertIn("attachment", download_response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
