import pathlib
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "aurora.js"
STYLE = ROOT / "static" / "aurora.css"
APP_LAYOUT_STYLE = ROOT / "static" / "app_layout.css"


class AuroraWarehouseNavigationTests(unittest.TestCase):
    def render_navigation(self, page, links):
        page.set_default_timeout(1000)
        anchors = "".join(
            f'<a href="{href}" class="{active}">{label}</a>'
            for href, label, active in links
        )
        page.set_content(
            f"""
            <base href="http://127.0.0.1:5002/">
            <style>{APP_LAYOUT_STYLE.read_text(encoding='utf-8')}</style>
            <style>{STYLE.read_text(encoding='utf-8')}</style>
            <main id="outside">页面内容</main>
            <nav class="page-nav">{anchors}</nav>
            """
        )
        page.evaluate("document.body.dataset.auroraPage = 'inventory'")
        page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
        page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")

    def test_mobile_warehouse_button_expands_available_links_upward(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                self.render_navigation(page, [
                    ("/crm", "查询", ""),
                    ("/results", "结果", ""),
                    ("/transfer", "移库", ""),
                    ("/inbound", "入库", ""),
                    ("/inventory", "盘点", "active"),
                    ("/product-library", "匹配", ""),
                    ("/accounts", "设置", ""),
                ])

                toggle = page.get_by_role("button", name="仓库")
                self.assertTrue(toggle.is_visible())
                self.assertIn("active", toggle.get_attribute("class"))
                self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
                menu_links = page.locator(".aurora-warehouse-menu a")
                self.assertEqual(menu_links.count(), 3)
                self.assertFalse(menu_links.first.is_visible())

                toggle.click()
                self.assertEqual(toggle.get_attribute("aria-expanded"), "true")
                self.assertEqual(
                    menu_links.evaluate_all(
                        "nodes => nodes.map(node => new URL(node.href).pathname)"
                    ),
                    ["/transfer", "/inbound", "/inventory"],
                )
                self.assertTrue(menu_links.first.is_visible())
                menu_box = page.locator(".aurora-warehouse-menu").bounding_box()
                nav_box = page.locator(".page-nav").bounding_box()
                self.assertLess(menu_box["y"], nav_box["y"])

                page.locator("#outside").click()
                self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
                toggle.click()
                page.keyboard.press("Escape")
                self.assertEqual(toggle.get_attribute("aria-expanded"), "false")
            finally:
                browser.close()

    def test_mobile_warehouse_menu_preserves_permission_filtered_subset(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                self.render_navigation(page, [
                    ("/inbound", "入库", ""),
                    ("/inventory", "盘点", "active"),
                ])

                page.get_by_role("button", name="仓库").click()
                self.assertEqual(
                    page.locator(".aurora-warehouse-menu a").evaluate_all(
                        "nodes => nodes.map(node => new URL(node.href).pathname)"
                    ),
                    ["/inbound", "/inventory"],
                )
                self.assertEqual(page.locator('a[href="/transfer"]').count(), 0)
            finally:
                browser.close()

    def test_desktop_keeps_original_warehouse_links_visible(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 800})
                self.render_navigation(page, [
                    ("/crm", "查询", ""),
                    ("/transfer", "移库", ""),
                    ("/inbound", "入库", ""),
                    ("/inventory", "盘点", "active"),
                    ("/accounts", "设置", ""),
                ])

                self.assertFalse(page.get_by_role("button", name="仓库").is_visible())
                for path in ("/transfer", "/inbound", "/inventory"):
                    self.assertTrue(page.locator(f'a[href="{path}"]').is_visible())
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
