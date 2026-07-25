import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as app_module


class ResultsPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_barcode_dir = app_module.BARCODE_DIR
        self.original_archive_dir = app_module.ARCHIVE_DIR
        self.original_data_file = app_module.DATA_FILE
        app_module.BARCODE_DIR = os.path.join(self.tempdir.name, "barcode")
        app_module.ARCHIVE_DIR = os.path.join(app_module.BARCODE_DIR, "archived")
        app_module.DATA_FILE = os.path.join(self.tempdir.name, "barcode_data.json")
        os.makedirs(app_module.ARCHIVE_DIR, exist_ok=True)
        Path(app_module.BARCODE_DIR, "1001.html").write_text("one", encoding="utf-8")
        Path(app_module.ARCHIVE_DIR, "1002.html").write_text("two", encoding="utf-8")
        if hasattr(app_module, "_reset_barcode_snapshot_cache"):
            app_module._reset_barcode_snapshot_cache()

        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        response = self.client.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertTrue(response.get_json()["success"])

    def tearDown(self):
        app_module.BARCODE_DIR = self.original_barcode_dir
        app_module.ARCHIVE_DIR = self.original_archive_dir
        app_module.DATA_FILE = self.original_data_file
        if hasattr(app_module, "_reset_barcode_snapshot_cache"):
            app_module._reset_barcode_snapshot_cache()
        self.tempdir.cleanup()

    @staticmethod
    def fake_fields(filepath):
        return {"sr2": {"name1": Path(filepath).stem}}

    def test_barcode_snapshot_reuses_unchanged_html_parsing(self):
        with mock.patch.object(
            app_module,
            "extract_fields_from_html",
            side_effect=self.fake_fields,
        ) as parser:
            first = app_module._barcode_snapshot()
            second = app_module._barcode_snapshot()

            self.assertEqual(parser.call_count, 2)
            self.assertEqual(first["revision"], second["revision"])

            changed_path = Path(app_module.BARCODE_DIR, "1001.html")
            changed_path.write_text("one changed and larger", encoding="utf-8")
            third = app_module._barcode_snapshot()

        self.assertNotEqual(first["revision"], third["revision"])
        self.assertEqual(parser.call_count, 4)

    def test_barcodes_api_combines_filters_and_supports_unchanged_revision(self):
        with mock.patch.object(
            app_module,
            "extract_fields_from_html",
            side_effect=self.fake_fields,
        ):
            first = self.client.get("/api/barcodes")
            payload = first.get_json()
            second = self.client.get(
                "/api/barcodes",
                query_string={"revision": payload["revision"]},
            )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["barcodes"]), 2)
        self.assertIn("filters", payload)
        self.assertTrue(payload["revision"])
        self.assertEqual(
            second.get_json(),
            {
                "success": True,
                "unchanged": True,
                "revision": payload["revision"],
                "total": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
