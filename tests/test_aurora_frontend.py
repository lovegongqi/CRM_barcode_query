import pathlib
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "aurora.js"
STYLE = ROOT / "static" / "aurora.css"
APP_LAYOUT_STYLE = ROOT / "static" / "app_layout.css"


class AuroraNavigationTests(unittest.TestCase):
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

    def test_mobile_navigation_shows_seven_evenly_distributed_links(self):
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

                links = page.locator(".page-nav > a")
                self.assertEqual(links.count(), 7)
                self.assertEqual(
                    links.evaluate_all(
                        "nodes => nodes.map(node => new URL(node.href).pathname)"
                    ),
                    [
                        "/crm", "/results", "/transfer", "/inbound",
                        "/inventory", "/product-library", "/accounts",
                    ],
                )
                self.assertEqual(page.get_by_role("button", name="仓库").count(), 0)
                self.assertTrue(all(links.nth(index).is_visible() for index in range(7)))
                widths = links.evaluate_all(
                    "nodes => nodes.map(node => node.getBoundingClientRect().width)"
                )
                self.assertLess(max(widths) - min(widths), 1)
                nav_box = page.locator(".page-nav").bounding_box()
                first_box = links.first.bounding_box()
                last_box = links.last.bounding_box()
                self.assertGreaterEqual(first_box["x"], nav_box["x"])
                self.assertLessEqual(last_box["x"] + last_box["width"], nav_box["x"] + nav_box["width"])
            finally:
                browser.close()

    def test_mobile_navigation_keeps_permission_filtered_links_direct(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                self.render_navigation(page, [
                    ("/inbound", "入库", ""),
                    ("/inventory", "盘点", "active"),
                ])

                links = page.locator(".page-nav > a")
                self.assertEqual(
                    links.evaluate_all(
                        "nodes => nodes.map(node => new URL(node.href).pathname)"
                    ),
                    ["/inbound", "/inventory"],
                )
                self.assertTrue(links.first.is_visible())
                self.assertTrue(links.last.is_visible())
                self.assertEqual(page.locator('a[href="/transfer"]').count(), 0)
                self.assertEqual(page.get_by_role("button", name="仓库").count(), 0)
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

                self.assertEqual(page.get_by_role("button", name="仓库").count(), 0)
                for path in ("/transfer", "/inbound", "/inventory"):
                    self.assertTrue(page.locator(f'a[href="{path}"]').is_visible())
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
