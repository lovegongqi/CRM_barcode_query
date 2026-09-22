import pathlib
import re
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "aurora.js"
STYLE = ROOT / "static" / "aurora.css"
APP_LAYOUT_STYLE = ROOT / "static" / "app_layout.css"
RESULTS_TEMPLATE = ROOT / "templates" / "index.html"


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


class AuroraResultsFilterLayoutTests(unittest.TestCase):
    def render_results_shell(self, page, markup):
        template = RESULTS_TEMPLATE.read_text(encoding="utf-8")
        inline_style = re.search(r"<style>(.*?)</style>", template, re.S).group(1)
        page.set_content(f"""
            <style>{inline_style}</style>
            <style>{STYLE.read_text(encoding='utf-8')}</style>
            <body data-aurora-page="results">{markup}</body>
        """)

    def test_desktop_filters_and_dates_share_one_compact_row(self):
        template = RESULTS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('class="filter-controls-row"', template)
        self.assertIsNotNone(
            re.search(
                r'<div class="filter-controls-row">.*id="filterArea".*class="date-filter-item"',
                template,
                re.S,
            )
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1565, "height": 900})
                self.render_results_shell(page, f"""
                    <div class="filter-shell" style="width:1435px">
                        <div class="filter-controls-row">
                          <div class="filter-area" id="filterArea">
                            {''.join(f'<div class="filter-item"><label>条件 {index}</label><div class="multi-select-trigger">请选择</div></div>' for index in range(4))}
                          </div>
                          <div class="date-filter-item">
                            <label>查询日期</label>
                            <div class="date-filter-row">
                              <input type="date" id="dateStart">
                              <span>至</span>
                              <input type="date" id="dateEnd">
                              <button class="date-clear-btn">清空日期</button>
                            </div>
                          </div>
                        </div>
                    </div>
                """)

                trigger_boxes = [
                    page.locator(".multi-select-trigger").nth(index).bounding_box()
                    for index in range(4)
                ]
                date_boxes = [
                    page.locator("#dateStart").bounding_box(),
                    page.locator("#dateEnd").bounding_box(),
                    page.locator(".date-clear-btn").bounding_box(),
                ]
                control_y = trigger_boxes[0]["y"]
                for box in trigger_boxes + date_boxes:
                    self.assertLessEqual(
                        box["height"], 34,
                        f"triggers={trigger_boxes}, dates={date_boxes}",
                    )
                    self.assertAlmostEqual(box["y"], control_y, delta=5)
                self.assertTrue(all(box["width"] < 220 for box in trigger_boxes))
            finally:
                browser.close()

    def test_mobile_condition_filters_start_collapsed_and_expand_on_demand(self):
        template = RESULTS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="resultsFilterCollapse"', template)
        self.assertIn('class="filter-collapse"', template)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                self.render_results_shell(page, """
                    <details class="filter-collapse" id="resultsFilterCollapse" open>
                      <summary class="section-label filter-collapse-summary">
                        <span>条件筛选</span><span>支持多选联动</span>
                      </summary>
                      <div class="filter-controls-row"><div id="filterArea">筛选内容</div></div>
                    </details>
                """)
                page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
                page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")

                collapse = page.locator("#resultsFilterCollapse")
                self.assertFalse(collapse.evaluate("element => element.open"))
                self.assertFalse(page.locator("#filterArea").is_visible())
                collapse.locator("summary").click()
                self.assertTrue(collapse.evaluate("element => element.open"))
                self.assertTrue(page.locator("#filterArea").is_visible())
            finally:
                browser.close()

    def test_mobile_result_stats_place_counts_beside_labels_in_short_cards(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                self.render_results_shell(page, """
                    <div class="stats-grid">
                      <div class="stat-card"><span>全部结果</span><strong>4</strong></div>
                      <div class="stat-card"><span>当前筛选</span><strong>4</strong></div>
                      <div class="stat-card"><span>已选条码</span><strong>0</strong></div>
                    </div>
                """)

                cards = page.locator(".stat-card")
                for index in range(3):
                    card = cards.nth(index).bounding_box()
                    label = cards.nth(index).locator("span").bounding_box()
                    count = cards.nth(index).locator("strong").bounding_box()
                    self.assertLessEqual(card["height"], 50)
                    self.assertAlmostEqual(
                        label["y"] + label["height"] / 2,
                        count["y"] + count["height"] / 2,
                        delta=2,
                    )
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
