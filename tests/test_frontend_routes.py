import atexit
import os
import tempfile
import unittest
from pathlib import Path


TEST_DATA_DIR = tempfile.TemporaryDirectory()
atexit.register(TEST_DATA_DIR.cleanup)
os.environ["CRM_DATA_DIR"] = TEST_DATA_DIR.name
os.environ["CRM_DESKTOP_APP"] = "0"

import app as app_module


class FrontendRouteSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True)
        os.makedirs(app_module.BARCODE_DIR, exist_ok=True)

    def setUp(self):
        self.client = app_module.app.test_client()
        response = self.client.post(
            "/api/app-auth/login",
            json={"username": "admin", "password": "88293529"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

    def test_startup_requires_tool_account_login(self):
        client = app_module.app.test_client()
        for route in ("/", "/crm", "/transfer", "/accounts"):
            with self.subTest(route=route):
                response = client.get(route, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].startswith("/login?next="))

        product_library = client.get("/product-library")
        self.assertEqual(product_library.status_code, 200)
        self.assertIn(b'id="lookupInput"', product_library.data)
        self.assertNotIn(b'id="editCard"', product_library.data)

        login = client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn(b'id="loginBtn"', login.data)

    def test_anonymous_product_lookup_is_public_but_online_query_is_protected(self):
        client = app_module.app.test_client()

        library = client.get("/api/product-library")
        self.assertEqual(library.status_code, 200)
        self.assertTrue(library.get_json()["success"])
        self.assertFalse(library.get_json()["can_edit"])

        lookup = client.get("/api/product-library/lookup?barcode=162501010001")
        self.assertEqual(lookup.status_code, 200)

        online_query = client.post(
            "/api/product-library/query/start",
            json={"barcode": "162501010001"},
        )
        self.assertEqual(online_query.status_code, 401)

        save_rule = client.post(
            "/api/product-library",
            json={"prefix": "16", "product_code": "916000024", "product_name": "测试产品"},
        )
        self.assertEqual(save_rule.status_code, 401)

    def test_product_library_rule_editor_is_rendered_only_for_admin(self):
        admin_page = self.client.get("/product-library")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b'id="editCard"', admin_page.data)

        accounts = app_module.load_accounts()
        accounts.append({
            "id": "viewer",
            "username": "viewer",
            "display_name": "访客",
            "password": "viewer-pass",
            "permissions": ["product-library"],
            "updated_at": "",
        })
        app_module.save_accounts(accounts)

        viewer = app_module.app.test_client()
        login = viewer.post(
            "/api/app-auth/login",
            json={"username": "viewer", "password": "viewer-pass"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.get_json()["success"])

        viewer_page = viewer.get("/product-library")
        self.assertEqual(viewer_page.status_code, 200)
        self.assertNotIn(b'id="editCard"', viewer_page.data)

    def test_launcher_forces_a_fresh_login_without_desktop_mode(self):
        source = (Path(app_module.__file__).parent / "app_launcher.py").read_text(encoding="utf-8")
        self.assertIn('url = f"http://127.0.0.1:{port}/api/app-auth/status"', source)
        self.assertEqual(source.count('url = f"http://127.0.0.1:{port}/logout"'), 2)
        self.assertNotIn('url = f"http://127.0.0.1:{port}/product-library"', source)
        self.assertNotIn('os.environ.setdefault("CRM_DESKTOP_APP", "1")', source)

    def test_every_work_page_shows_tool_account_logout(self):
        for route in ("/", "/crm", "/transfer", "/product-library", "/accounts"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'href="/logout"', response.data)

    def test_primary_pages_render_with_aurora_shell(self):
        expected_pages = {
            "/": "results",
            "/crm": "query",
            "/transfer": "transfer",
            "/product-library": "product-library",
            "/accounts": "settings",
        }
        for route, page in expected_pages.items():
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'data-aurora-page="{page}"'.encode(), response.data)
                self.assertIn(b'/static/aurora.css', response.data)

    def test_shared_assets_are_served(self):
        for path, mimetypes in (
            ("/static/aurora.css", ("text/css",)),
            ("/static/aurora.js", ("text/javascript", "application/javascript")),
            ("/static/ecowater-logo.png", ("image/png",)),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(response.mimetype, mimetypes)
                finally:
                    response.close()

    def test_settings_page_includes_account_management(self):
        response = self.client.get("/accounts")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="accountEditCard"', response.data)
        self.assertIn(b'id="accountListCard"', response.data)

    def test_filter_options_include_service_order_presence(self):
        filters = app_module.get_filter_options([
            {"fields": {"sr2": [{"servno1": "FW202607230001"}]}},
            {"fields": {"sr2": [{}]}},
        ])

        service_filter = filters["has_service_sr2"]
        self.assertEqual(service_filter["label"], "有无服务单")
        self.assertEqual(service_filter["options"], ["无服务单", "有服务单"])

    def test_batch_stop_endpoint_marks_stop_requested(self):
        client = app_module.app.test_client()
        client.post("/api/app-auth/login", json={"username": "admin", "password": "88293529"})

        with app_module.batch_job_lock:
            job = app_module._empty_batch_job(slot_id="query-1", barcodes=["1", "2"])
            job["running"] = True
            app_module.batch_jobs[job["job_id"]] = job
            app_module.latest_batch_job_by_slot["query-1"] = job["job_id"]
            job_id = job["job_id"]

        try:
            response = client.post(
                "/api/crm/batch/stop",
                json={"slot_id": "query-1", "job_id": job_id, "kind": "query"},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            with app_module.batch_job_lock:
                self.assertTrue(app_module.batch_jobs[job_id]["stop_requested"])
        finally:
            with app_module.batch_job_lock:
                app_module.batch_jobs.pop(job_id, None)
                app_module.latest_batch_job_by_slot.pop("query-1", None)


if __name__ == "__main__":
    unittest.main()
