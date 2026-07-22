import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"


class FrontendContractTest(unittest.TestCase):
    page_templates = {
        "query": "crm.html",
        "results": "index.html",
        "transfer": "transfer.html",
        "product-library": "product_library.html",
        "settings": "accounts.html",
        "login": "login.html",
        "no-permission": "no_permission.html",
    }

    def source(self, filename):
        return (TEMPLATES / filename).read_text(encoding="utf-8")

    def test_every_page_uses_aurora_shell_and_logo(self):
        for page, filename in self.page_templates.items():
            with self.subTest(page=page):
                source = self.source(filename)
                self.assertIn('/static/aurora.css', source)
                self.assertIn('/static/aurora.js', source)
                self.assertIn('/static/ecowater-logo.png', source)
                self.assertIn(f'data-aurora-page="{page}"', source)

    def test_desktop_logo_scrolls_with_the_page(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        base_rule = re.search(r"\.aurora-logo\s*\{([^}]*)\}", css, re.S)
        self.assertIsNotNone(base_rule)
        self.assertRegex(base_rule.group(1), r"position:\s*absolute")
        self.assertNotRegex(base_rule.group(1), r"position:\s*fixed")

    def test_desktop_logo_is_enlarged_and_aligned_with_header_copy(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        desktop_css, mobile_css = css.split("@media (max-width: 720px)", 1)
        logo_rule = re.search(r"\.aurora-logo\s*\{([^}]*)\}", desktop_css, re.S)
        self.assertIsNotNone(logo_rule)
        self.assertRegex(logo_rule.group(1), r"top:\s*25px")
        self.assertRegex(logo_rule.group(1), r"width:\s*68px")
        self.assertRegex(logo_rule.group(1), r"height:\s*68px")
        self.assertRegex(
            mobile_css,
            r"\.aurora-logo\s*\{[^}]*width:\s*38px;[^}]*height:\s*38px",
        )

    def test_mobile_query_select_all_stays_on_one_line(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r"\.aurora-channel-select-all\s*\{[^}]*display:\s*flex;[^}]*white-space:\s*nowrap",
        )

    def test_logged_in_navigation_is_fixed_on_mobile(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"@media\s*\(max-width:\s*720px\)")
        self.assertRegex(css, r"\.page-nav[^{]*\{[^}]*position:\s*fixed")
        self.assertIn("bottom: max(10px, env(safe-area-inset-bottom))", css)
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertNotIn("top: max(10px, env(safe-area-inset-top))", mobile_css)

    def test_mobile_shell_does_not_create_page_level_horizontal_scroll(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r"body\[data-aurora-page\]\s*\{[^}]*overflow-x:\s*hidden",
        )
        self.assertRegex(css, r"\.aurora-channel-chip\s*\{[^}]*position:\s*relative")

    def test_mobile_query_channels_use_one_shared_multiselect_state(self):
        query = self.source("crm.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        for token in (
            'id="querySlotMobileTrigger"',
            'id="querySlotMobileMenu"',
            'id="querySlotMobileCount"',
            "toggleQuerySlotMenu",
            "selectAllQuerySlots",
            "selectedQuerySlotIds",
        ):
            with self.subTest(token=token):
                self.assertIn(token, query)
        self.assertRegex(
            css,
            r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*none",
        )
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r"\.aurora-channel-options\s*\{[^}]*display:\s*none",
        )
        self.assertRegex(
            mobile_css,
            r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*inline-flex",
        )

    def test_query_batch_summary_is_persisted_and_replaces_static_badge(self):
        query = self.source("crm.html")
        self.assertIn('id="queryBatchSummary"', query)
        self.assertIn("formatBatchElapsed", query)
        self.assertIn("crm_last_query_summary", query)
        self.assertIn("captureLastQuerySummary", query)
        self.assertNotIn("AUTO SCHEDULING</span>", query)

    def test_mobile_work_page_subtitles_are_hidden_without_affecting_desktop(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        desktop_css, mobile_css = css.split("@media (max-width: 720px)", 1)
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page\]:not\(\[data-aurora-page="login"\]\):not\(\[data-aurora-page="no-permission"\]\) \.header > div:first-child > p,[^}]*\.app-subtitle\s*\{[^}]*display:\s*none\s*!important',
        )
        self.assertNotRegex(
            desktop_css,
            r"\.app-subtitle\s*\{[^}]*display:\s*none",
        )

    def test_mobile_logo_sits_left_of_every_work_page_title(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r"\.aurora-logo\s*\{[^}]*position:\s*absolute;[^}]*top:\s*18px;[^}]*left:\s*12px;[^}]*margin:\s*0",
        )
        self.assertRegex(
            mobile_css,
            r"\.header > div:first-child,[^}]*\.app-header > \.app-title\s*\{[^}]*padding-left:\s*50px",
        )

    def test_transfer_mobile_grid_constrains_wide_tables_to_their_scroller(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.grid > div\s*\{[^}]*min-width:\s*0',
        )

    def test_desktop_navigation_sits_below_the_page_title(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn("--aurora-nav-height:", css)
        self.assertRegex(
            css,
            r'body\[data-aurora-page\]:not\([^}]+\.page-nav\s*\{[^}]*position:\s*static\s*!important',
        )
        self.assertIn("body[data-aurora-page] .app-header", css)

    def test_desktop_content_extends_below_the_logo(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r"body\[data-aurora-page\] \.container\s*\{[^}]*padding:\s*30px 24px 42px",
        )
        self.assertIn(".app-header > .app-title", css)
        self.assertRegex(
            css,
            r"@media\s*\(max-width:\s*720px\)[\s\S]*\.app-header > \.app-title[^}]*padding-left:\s*50px",
        )

    def test_results_title_uses_the_shared_english_eyebrow(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn(
            'body[data-aurora-page] .app-header > .app-title::before',
            css,
        )
        self.assertIn('content: "BARCODE OPERATIONS CENTER"', css)

    def test_results_and_query_headers_share_the_same_subtitle_spacing(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r'body\[data-aurora-page="results"\] \.app-subtitle\s*\{[^}]*margin-top:\s*0\s*!important',
        )

    def test_every_work_page_uses_the_shared_tool_account_logout_button(self):
        for filename in ("crm.html", "index.html", "transfer.html", "product_library.html", "accounts.html"):
            with self.subTest(filename=filename):
                self.assertIn("aurora-account-logout", self.source(filename))
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn(".aurora-account-logout", css)

    def test_tool_account_controls_use_compact_status_rows(self):
        filenames = ("crm.html", "index.html", "transfer.html", "product_library.html", "accounts.html")
        for filename in filenames:
            with self.subTest(filename=filename):
                source = self.source(filename)
                self.assertIn("aurora-account-status", source)
                self.assertNotIn("aurora-header-logout", source)
        self.assertNotIn("<h2>当前工具账号</h2>", self.source("accounts.html"))
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.aurora-account-status\s*\{[^}]*min-height:\s*50px")

    def test_query_and_transfer_omit_redundant_tool_account_text(self):
        query = self.source("crm.html")
        transfer = self.source("transfer.html")
        self.assertNotIn('<span>工具账号：</span>', query)
        self.assertNotIn('工具账号：<span id="appAccountStatus">', transfer)
        self.assertIn('class="aurora-account-logout"', query)
        self.assertIn('class="aurora-account-logout"', transfer)

    def test_results_list_has_fixed_scrollable_viewport(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r'body\[data-aurora-page="results"\] \.aurora-results-grid\s*\{[^}]*max-height:[^;}]+;[^}]*overflow-y:\s*auto',
        )

    def test_work_pages_share_one_desktop_bottom_baseline(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn("@media (min-width: 721px)", css)
        for page in ("query", "results", "transfer", "product-library", "settings"):
            self.assertIn(f'body[data-aurora-page="{page}"] .container', css)
        for selector in (
            'body[data-aurora-page="query"] .aurora-realtime',
            'body[data-aurora-page="transfer"] .aurora-realtime',
            'body[data-aurora-page="results"] .aurora-results-grid',
            'body[data-aurora-page="product-library"] #editCard',
            'body[data-aurora-page="settings"] #accountListCard',
        ):
            self.assertIn(selector, css)
        self.assertRegex(css, r"\.aurora-query-table-wrap\s*\{[^}]*overflow-y:\s*auto")
        self.assertRegex(css, r"#libraryBox[^}]*overflow-y:\s*auto")
        self.assertRegex(css, r"#accountsBox[^}]*overflow-y:\s*auto")

    def test_transfer_workspace_preserves_full_width_grid(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r'body\[data-aurora-page="transfer"\] \.grid\s*\{[^}]*display:\s*grid',
        )
        self.assertRegex(
            css,
            r'body\[data-aurora-page="transfer"\] \.grid > div\s*\{[^}]*width:\s*100%',
        )

    def test_settings_primary_cards_share_one_responsive_row(self):
        template = self.source("accounts.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn('class="settings-primary-grid"', template)
        self.assertRegex(
            css,
            r'body\[data-aurora-page="settings"\] \.settings-primary-grid\s*\{[^}]*grid-template-columns:',
        )

    def test_frozen_warehouse_option_sits_below_its_name_field(self):
        template = self.source("accounts.html")
        field = re.search(
            r'<div class="frozen-warehouse-field">(.*?)</div>',
            template,
            re.S,
        )
        self.assertIsNotNone(field)
        self.assertIn('id="frozenWarehouseName"', field.group(1))
        self.assertIn('id="frozenWarehouseSaveOnly"', field.group(1))

    def test_navigation_order_matches_the_approved_workflow(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        positions = [
            source.index("'permission': 'crm'"),
            source.index("'permission': 'results'"),
            source.index("'permission': 'transfer'"),
            source.index("'permission': 'product-library'"),
            source.index("'permission': 'accounts'"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_login_and_access_pages_use_approved_compositions(self):
        login = self.source("login.html")
        denied = self.source("no_permission.html")
        self.assertIn('class="aurora-login-intro"', login)
        self.assertEqual(login.count('class="aurora-feature-icon '), 3)
        self.assertIn('aurora-login-form', login)
        self.assertIn('id="username"', login)
        self.assertIn('id="password"', login)
        self.assertIn('aurora-access-card', denied)
        self.assertIn("当前账号", denied)
        self.assertIn("可访问页面", denied)

    def test_realtime_status_surfaces_have_log_drill_down(self):
        query = self.source("crm.html")
        transfer = self.source("transfer.html")
        library = self.source("product_library.html")

        self.assertIn('id="queryRealtimeRows"', query)
        self.assertIn('openAuroraLog(', query)
        self.assertIn('id="transferRealtimeRows"', transfer)
        self.assertIn('openAuroraLog(', transfer)
        self.assertIn('id="libraryQueryActivity"', library)
        self.assertIn('openAuroraLog(', library)

    def test_product_library_rules_use_fixed_scrollable_viewport(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(
            css,
            r'body\[data-aurora-page="product-library"\] #editCard\s*\{[^}]*height:[^;}]+;[^}]*display:\s*flex',
        )
        self.assertRegex(
            css,
            r'body\[data-aurora-page="product-library"\] #libraryBox\s*\{[^}]*overflow-y:\s*auto',
        )
        self.assertIn(
            'body[data-aurora-page="product-library"] #libraryBox thead th',
            css,
        )

    def test_query_queue_uses_latest_log_and_four_query_states(self):
        query = self.source("crm.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")

        self.assertIn("<th>最新日志</th><th>查询状态</th><th>耗时</th>", query)
        self.assertIn("latestQueryLogMessage", query)
        for label in ("等待查询", "查询中", "查询成功", "查询失败"):
            with self.subTest(label=label):
                self.assertIn(label, query)
        self.assertIn("aurora-log-preview", query)
        self.assertRegex(
            css,
            r"\.aurora-realtime\s*\{[^}]*height:[^;}]+;[^}]*display:\s*flex",
        )
        self.assertRegex(
            css,
            r"\.aurora-query-table-wrap\s*\{[^}]*overflow-y:\s*auto",
        )
        self.assertIn(".aurora-query-table thead th", css)

    def test_results_page_contains_approved_filters_and_columns(self):
        source = self.source("index.html")
        for label in (
            "归属经销商",
            "服务经销商",
            "是否结单",
            "查询日期",
            "客户姓名",
        ):
            with self.subTest(label=label):
                self.assertIn(label, source)

    def test_results_page_omits_legacy_file_storage_footer(self):
        source = self.source("index.html")
        self.assertNotIn("查询结果文件保存在 barcode/ 目录下", source)

    def test_results_management_panel_uses_dark_glass_theme(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        for selector in (
            'body[data-aurora-page="results"] .management-head',
            'body[data-aurora-page="results"] .filter-shell',
            'body[data-aurora-page="results"] .stat-card',
            'body[data-aurora-page="results"] .dropdown-menu',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        self.assertIn("--results-panel-glass:", css)

    def test_transfer_page_keeps_complete_workflow(self):
        source = self.source("transfer.html")
        for token in (
            'id="transferType"',
            'id="transferDistributor"',
            'id="previewBtn"',
            'id="submitBtn"',
            "生成的移库单号",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_transfer_slot_tabs_use_dark_glass_theme(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn('body[data-aurora-page="transfer"] .slot-tab {', css)
        self.assertIn('body[data-aurora-page="transfer"] .slot-tab.active {', css)

    def test_legacy_light_surfaces_are_overridden_by_dark_theme(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        for selector in (
            "body[data-aurora-page] .stepper button",
            "body[data-aurora-page] .remark-popup",
            "body[data-aurora-page] .transfer-slot-option",
            "body[data-aurora-page] .global-log-modal",
            "body[data-aurora-page] .note",
            'body[data-aurora-page="product-library"] .lookup-result-table tr',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        self.assertIn("--aurora-control-bg:", css)

    def test_default_worker_counts_are_ten_query_and_five_transfer(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(
            len(re.findall(r'CRM_QUERY_WORKERS"\), 10', source)),
            2,
        )
        self.assertGreaterEqual(
            len(re.findall(r'CRM_TRANSFER_WORKERS"\), 5', source)),
            2,
        )


if __name__ == "__main__":
    unittest.main()
