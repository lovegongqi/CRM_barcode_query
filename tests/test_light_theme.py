import pathlib
import re
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]


class LightThemeTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
