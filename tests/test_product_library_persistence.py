import json
import os
import tempfile
import unittest

import app as app_module


class ProductLibraryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_config_dir = app_module.CONFIG_DIR
        self.original_product_library_file = app_module.PRODUCT_LIBRARY_FILE
        app_module.CONFIG_DIR = self.tempdir.name
        app_module.PRODUCT_LIBRARY_FILE = os.path.join(
            self.tempdir.name, "product_library.json"
        )

    def tearDown(self):
        app_module.CONFIG_DIR = self.original_config_dir
        app_module.PRODUCT_LIBRARY_FILE = self.original_product_library_file
        self.tempdir.cleanup()

    def _write_legacy_rules(self, rules):
        with open(app_module.PRODUCT_LIBRARY_FILE, "w", encoding="utf-8") as handle:
            json.dump(rules, handle, ensure_ascii=False)

    @staticmethod
    def _legacy_rules():
        return {
            "16": {
                "prefix": "16",
                "product_code": "916000024",
                "product_name": "软水机I6系列 609ECM",
                "source_barcode": "162501010001",
                "updated_at": "2026-07-24 12:00:00",
            }
        }

    def test_legacy_rules_migrate_to_sqlite_and_survive_json_removal(self):
        self._write_legacy_rules(self._legacy_rules())

        rules = app_module.load_product_library()

        self.assertEqual(rules["16"]["product_code"], "916000024")
        self.assertTrue(
            os.path.exists(os.path.join(self.tempdir.name, "product_library.sqlite3"))
        )

        os.remove(app_module.PRODUCT_LIBRARY_FILE)
        self.assertEqual(
            app_module.load_product_library()["16"]["product_name"],
            "软水机I6系列 609ECM",
        )

    def test_deleted_rule_is_not_restored_from_legacy_backup(self):
        legacy_rules = self._legacy_rules()
        self._write_legacy_rules(legacy_rules)
        app_module.load_product_library()

        self.assertTrue(app_module.delete_product_library("16"))
        self._write_legacy_rules(legacy_rules)

        self.assertNotIn("16", app_module.load_product_library())


if __name__ == "__main__":
    unittest.main()
