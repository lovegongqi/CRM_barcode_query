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
        for route in ("/", "/crm", "/transfer", "/product-library", "/accounts"):
            with self.subTest(route=route):
                response = client.get(route, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].startswith("/login?next="))

        login = client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn(b'id="loginBtn"', login.data)

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
        for path, mimetype in (
            ("/static/aurora.css", "text/css"),
            ("/static/aurora.js", "text/javascript"),
            ("/static/ecowater-logo.png", "image/png"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, mimetype)
                finally:
                    response.close()

    def test_settings_page_includes_account_management(self):
        response = self.client.get("/accounts")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="accountEditCard"', response.data)
        self.assertIn(b'id="accountListCard"', response.data)


if __name__ == "__main__":
    unittest.main()
