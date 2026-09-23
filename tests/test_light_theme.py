import pathlib
import re
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LightThemeTest(unittest.TestCase):
    def test_work_page_logos_scroll_with_their_titles(self):
        for name in ("accounts", "crm", "inbound", "index", "inventory", "product_library", "service_close", "transfer"):
            with self.subTest(page=name):
                html = (ROOT / "templates" / f"{name}.html").read_text(encoding="utf-8")
                self.assertRegex(html, r'<div class="container">\s*<a class="aurora-logo"')

    def test_every_page_loads_light_theme_after_other_styles(self):
        for template in (ROOT / "templates").glob("*.html"):
            with self.subTest(template=template.name):
                head = template.read_text(encoding="utf-8").split("</head>", 1)[0]
                self.assertRegex(head, r'<link rel="stylesheet" href="/static/light_theme.css(?:\?[^\"]*)?">')
                light_link = head.rfind('/static/light_theme.css')
                self.assertGreater(light_link, head.rfind('</style>'))
                self.assertGreater(light_link, head.rfind('/static/aurora.css'))
                self.assertGreater(light_link, head.rfind('/static/inventory.css'))

    def test_common_surfaces_are_light_readable_glass(self):
        styles = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("app_layout.css", "aurora.css", "light_theme.css")
            if (ROOT / "static" / name).exists()
        )
        markup = '''<html><head></head><body data-aurora-page="results">
            <a class="aurora-logo" href="/"><img src="/static/ecowater-logo.png" alt="怡口"></a>
            <nav class="page-nav"><a class="active">结果</a></nav>
            <main class="card"><h2>查询</h2><input value="ORD2609140231"></main>
            <section class="global-log-modal">日志</section>
            <table><thead><tr><th>条码</th></tr></thead><tbody><tr><td>123</td></tr></tbody></table>
        </body></html>'''
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(markup)
            page.add_style_tag(content=styles)
            values = page.evaluate('''() => Object.fromEntries(
                ['body', '.page-nav', '.card', 'input', '.global-log-modal', 'th'].map(selector => {
                    const style = getComputedStyle(document.querySelector(selector));
                    return [selector, {background: style.backgroundColor, color: style.color,
                        blur: style.backdropFilter}];
                })
            )''')
            browser.close()

        def channels(color):
            return [int(part) for part in re.findall(r"\d+", color)[:3]]

        for selector in ('body', '.page-nav', '.card', 'input', '.global-log-modal', 'th'):
            with self.subTest(selector=selector):
                self.assertGreater(min(channels(values[selector]['background'])), 205)
                self.assertLess(max(channels(values[selector]['color'])), 130)
        for selector in ('.page-nav', '.card', '.global-log-modal'):
            with self.subTest(glass=selector):
                self.assertIn('blur(', values[selector]['blur'])

    def test_logo_has_no_colored_backplate(self):
        css = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("aurora.css", "light_theme.css")
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content('<body data-aurora-page="login"><a class="aurora-logo"><img alt="怡口"></a></body>')
            page.add_style_tag(content=css)
            style = page.locator('.aurora-logo').evaluate('''element => {
                const value = getComputedStyle(element);
                return {background: value.backgroundImage, border: value.borderTopWidth, shadow: value.boxShadow};
            }''')
            browser.close()
        self.assertEqual(style, {'background': 'none', 'border': '0px', 'shadow': 'none'})

    def test_mobile_content_scrolls_only_above_fixed_navigation(self):
        styles = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("app_layout.css", "aurora.css", "light_theme.css")
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 430, "height": 932})
            page.set_content('''<body data-aurora-page="inbound"><div class="container">
                <nav class="page-nav"><a>入库</a></nav>
                <main style="height: 1400px">内容</main>
            </div></body>''')
            page.add_style_tag(content=styles)
            bounds = page.evaluate('''() => {
                const content = document.querySelector('.container');
                const nav = document.querySelector('.page-nav');
                return {contentBottom: content.getBoundingClientRect().bottom,
                    navTop: nav.getBoundingClientRect().top,
                    scrollable: content.scrollHeight > content.clientHeight};
            }''')
            browser.close()
        self.assertTrue(bounds["scrollable"])
        self.assertLessEqual(bounds["contentBottom"], bounds["navTop"])

    def test_querying_status_text_is_readable(self):
        styles = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("aurora.css", "light_theme.css")
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content('<body data-aurora-page="query"><span class="aurora-status-button">查询中</span></body>')
            page.add_style_tag(content=styles)
            color = page.locator('.aurora-status-button').evaluate('element => getComputedStyle(element).color')
            browser.close()
        channels = [int(part) for part in re.findall(r"\d+", color)[:3]]
        self.assertLess(max(channels), 170)

    def test_shared_log_button_is_removed_but_log_api_remains(self):
        script = (ROOT / "static" / "log_modal.js").read_text(encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content('<body data-aurora-page="query"></body>')
            page.add_script_tag(content=script)
            result = page.evaluate('''() => {
                window.globalLogAppend('test');
                return {buttons: document.querySelectorAll('.global-log-button').length,
                    overlays: document.querySelectorAll('.global-log-overlay').length};
            }''')
            browser.close()
        self.assertEqual(result, {"buttons": 0, "overlays": 1})

    def test_mobile_transfer_and_results_controls_are_compact(self):
        common = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("app_layout.css", "aurora.css", "light_theme.css")
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 430, "height": 932})
            page.set_content('''<body data-aurora-page="transfer"><div class="container">
                <div class="fields"><div><label>移库类型</label><select><option>移出</option></select></div>
                <div><label>目标分销商</label><input></div><div><label>备注</label><input></div></div>
            </div></body>''')
            page.add_style_tag(content='''.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
                @media(max-width:860px){.fields{grid-template-columns:1fr}}''')
            page.add_style_tag(content=common)
            transfer = page.evaluate('''() => [...document.querySelectorAll('.fields > div')]
                .map(el => ({top:el.getBoundingClientRect().top,width:el.getBoundingClientRect().width}))''')
            page.set_content('''<body data-aurora-page="results"><div class="container">
                <div class="action-groups"><button class="btn">复制选中</button><button class="btn">全选当前</button></div>
                <div class="search-row"><input><button>清空搜索</button></div>
            </div></body>''')
            page.add_style_tag(content='''.search-row{display:grid;grid-template-columns:1fr auto;gap:10px}
                @media(max-width:640px){.search-row{grid-template-columns:1fr}}''')
            page.add_style_tag(content=common)
            results = page.evaluate('''() => {const input=document.querySelector('.search-row input').getBoundingClientRect();
                const button=document.querySelector('.search-row button').getBoundingClientRect();
                return {sameRow:Math.abs(input.top-button.top)<2,buttonWidth:button.width,
                    actionHeight:document.querySelector('.action-groups .btn').getBoundingClientRect().height};}''')
            browser.close()
        self.assertAlmostEqual(transfer[0]["top"], transfer[1]["top"], delta=2)
        self.assertGreater(transfer[2]["top"], transfer[1]["top"])
        self.assertTrue(results["sameRow"])
        self.assertLess(results["buttonWidth"], 125)
        self.assertLessEqual(results["actionHeight"], 36)

    def test_transfer_form_buttons_align_with_inputs_on_phone_and_desktop(self):
        template = (ROOT / "templates" / "transfer.html").read_text(encoding="utf-8")
        for marker in ('class="distributor-input-row"', 'class="transfer-remark-input-row"',
                       'transfer-mobile-clear', 'transfer-desktop-clear'):
            self.assertIn(marker, template)
        inline = "\n".join(re.findall(r"<style>(.*?)</style>", template, re.S))
        styles = "\n".join(
            (ROOT / "static" / name).read_text(encoding="utf-8")
            for name in ("app_layout.css", "aurora.css", "light_theme.css")
        )
        markup = '''<body data-aurora-page="transfer"><div class="container"><div class="card">
            <div class="fields">
                <div><label>移库类型</label><select><option>移出</option></select></div>
                <div><label>目标分销商</label><div class="distributor-input-row">
                    <div class="distributor-picker"><input></div><button class="mini-link-btn">批量导入</button>
                </div></div>
                <div class="transfer-remark-field"><label>备注</label><div class="transfer-remark-input-row">
                    <input><button class="btn transfer-mobile-clear">清空</button></div></div>
                <div class="actions"><button class="btn transfer-desktop-clear">清空</button>
                    <button class="btn">汇总预览</button><button class="btn">提交移库</button></div>
            </div></div></div></body>'''
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            bounds = {}
            for width in (430, 1565):
                page.set_viewport_size({"width": width, "height": 932})
                page.set_content(markup)
                page.add_style_tag(content=inline + styles)
                bounds[width] = page.evaluate('''() => {
                    const box = selector => document.querySelector(selector).getBoundingClientRect();
                    const visible = selector => getComputedStyle(document.querySelector(selector)).display !== 'none';
                    return {type:box('.fields select').toJSON(), distributor:box('.distributor-picker input').toJSON(),
                        importButton:box('.distributor-input-row button').toJSON(), remark:box('.transfer-remark-input-row input').toJSON(),
                        mobileClear:box('.transfer-mobile-clear').toJSON(), desktopClear:box('.transfer-desktop-clear').toJSON(),
                        preview:box('.actions .btn:nth-child(2)').toJSON(), submit:box('.actions .btn:nth-child(3)').toJSON(),
                        mobileClearVisible:visible('.transfer-mobile-clear'), desktopClearVisible:visible('.transfer-desktop-clear')};
                }''')
            browser.close()
        for width in (430, 1565):
            row = bounds[width]
            self.assertAlmostEqual(row["type"]["top"], row["distributor"]["top"], delta=2)
            self.assertAlmostEqual(row["distributor"]["top"], row["importButton"]["top"], delta=2)
            self.assertAlmostEqual(row["distributor"]["height"], row["importButton"]["height"], delta=2)
        self.assertTrue(bounds[430]["mobileClearVisible"])
        self.assertFalse(bounds[430]["desktopClearVisible"])
        self.assertAlmostEqual(bounds[430]["remark"]["top"], bounds[430]["mobileClear"]["top"], delta=2)
        self.assertGreater(bounds[430]["preview"]["top"], bounds[430]["remark"]["top"])
        self.assertFalse(bounds[1565]["mobileClearVisible"])
        self.assertTrue(bounds[1565]["desktopClearVisible"])
        self.assertAlmostEqual(bounds[1565]["remark"]["bottom"], bounds[1565]["preview"]["bottom"], delta=2)
        self.assertAlmostEqual(bounds[1565]["remark"]["bottom"], bounds[1565]["submit"]["bottom"], delta=2)


if __name__ == "__main__":
    unittest.main()
