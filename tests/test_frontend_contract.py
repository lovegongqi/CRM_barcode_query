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
        "inbound": "inbound.html",
        "product-library": "product_library.html",
        "settings": "accounts.html",
        "login": "login.html",
        "no-permission": "no_permission.html",
    }

    def source(self, filename):
        return (TEMPLATES / filename).read_text(encoding="utf-8")

    def media_block(self, css, max_width):
        matches = list(re.finditer(rf"@media\s*\(max-width:\s*{max_width}px\)\s*\{{", css))
        self.assertEqual(len(matches), 1)
        match = matches[0]
        depth = 1
        for index in range(match.end(), len(css)):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    return css[:match.start()], css[match.end():index], css[index + 1:]
        self.fail(f"Unclosed max-width: {max_width}px media block")

    def test_every_page_uses_aurora_shell_and_logo(self):
        for page, filename in self.page_templates.items():
            with self.subTest(page=page):
                source = self.source(filename)
                self.assertIn('/static/aurora.css', source)
                self.assertIn('/static/aurora.js', source)
                self.assertIn('/static/ecowater-logo.png', source)
                self.assertIn(f'data-aurora-page="{page}"', source)

    def test_service_order_detail_uses_structured_fields_and_product_rows(self):
        results = self.source("index.html")
        self.assertIn('id="serviceDetailContent"', results)
        self.assertIn('function renderServiceOrderDetail(detail)', results)
        self.assertIn("detail.products || []", results)
        self.assertIn("product.barcode", results)
        self.assertIn("detail.fields || []", results)
        self.assertIn("客户姓名", results)
        self.assertIn("联系电话", results)
        self.assertIn("联系地址", results)
        self.assertIn("受理时间", results)
        self.assertIn("客户预约时间", results)
        self.assertIn("服务人员", results)
        self.assertIn("<th>产品名称</th><th>产品编码</th><th>条码</th><th>关系</th>", results)
        self.assertNotIn("<th>型号</th>", results)
        self.assertNotIn("product.product_model", results)
        self.assertNotIn("service-close-summary-log", results)

    def test_results_page_can_export_service_orders_in_install_template(self):
        results = self.source("index.html")
        self.assertIn('onclick="exportServiceOrdersXlsx()"', results)
        self.assertIn("function exportServiceOrdersXlsx()", results)
        self.assertIn("fetch('/api/service-orders/export/xlsx'", results)
        self.assertIn("getSelectedBarcodeArray()", results)

    def test_service_order_detail_matches_the_dark_workspace_theme(self):
        results = self.source("index.html")
        content_rule = re.search(r"\.service-detail-content\s*\{([^}]*)\}", results, re.S)
        section_rule = re.search(r"\.service-detail-section\s*\{([^}]*)\}", results, re.S)
        table_header_rule = re.search(r"\.service-detail-table th\s*\{([^}]*)\}", results, re.S)
        self.assertIsNotNone(content_rule)
        self.assertIsNotNone(section_rule)
        self.assertIsNotNone(table_header_rule)
        self.assertRegex(content_rule.group(1), r"background:\s*#071426")
        self.assertRegex(content_rule.group(1), r"color:\s*#e2e8f0")
        self.assertRegex(section_rule.group(1), r"background:\s*#0c1b2e")
        self.assertRegex(table_header_rule.group(1), r"background:\s*#12243a")

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

    def test_settings_query_channel_options_wrap(self):
        settings = self.source("accounts.html")
        self.assertRegex(
            settings,
            r"\.runtime-query-slots\s*\{[^}]*display:flex;[^}]*flex-wrap:wrap",
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

    def test_mobile_results_actions_use_equal_three_column_grid(self):
        results = self.source("index.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        before_mobile, mobile_css, after_mobile = self.media_block(css, 640)
        self.assertEqual(results.count('<div class="action-groups">'), 1)
        self.assertEqual(
            len(re.findall(r'<div class="action-groups">(.*?)</div>', results, re.S)[0].split('<button')) - 1,
            10,
        )
        for selector in (
            'body[data-aurora-page="results"] .action-groups {',
            'body[data-aurora-page="results"] .action-groups .btn {',
        ):
            self.assertNotIn(selector, before_mobile)
            self.assertNotIn(selector, after_mobile)
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.action-groups\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.action-groups \.btn\s*\{[^}]*width:\s*100%[^}]*font-size:\s*11px[^}]*white-space:\s*nowrap',
        )

    def test_mobile_results_stats_stay_in_one_equal_three_column_row(self):
        results = self.source("index.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        before_mobile, mobile_css, after_mobile = self.media_block(css, 640)
        stats = re.findall(r'<div class="stats-grid">(.*?)</div>\s*</div>', results, re.S)[0]
        self.assertEqual(stats.count('<div class="stat-card">'), 3)
        selector = 'body[data-aurora-page="results"] .stats-grid {'
        self.assertNotIn(selector, before_mobile)
        self.assertNotIn(selector, after_mobile)
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.stats-grid\s*\{[^}]*width:\s*100%[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)',
        )

    def test_mobile_results_dates_use_two_columns_with_full_width_clear_row(self):
        results = self.source("index.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        before_mobile, mobile_css, after_mobile = self.media_block(css, 640)
        date_row = re.findall(r'<div class="date-filter-row">(.*?)</div>', results, re.S)[0]
        self.assertEqual(date_row.count('<input type="date"'), 2)
        self.assertEqual(date_row.count('class="date-clear-btn"'), 1)
        for selector in (
            'body[data-aurora-page="results"] .date-filter-row {',
            'body[data-aurora-page="results"] .date-filter-row label,\nbody[data-aurora-page="results"] .date-filter-row > span {',
            'body[data-aurora-page="results"] .date-filter-row input {',
            'body[data-aurora-page="results"] .date-clear-btn {',
        ):
            self.assertNotIn(selector, before_mobile)
            self.assertNotIn(selector, after_mobile)
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.date-filter-row\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.date-filter-row label,\s*body\[data-aurora-page="results"\] \.date-filter-row > span\s*\{[^}]*display:\s*none',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="results"\] \.date-clear-btn\s*\{[^}]*width:\s*100%[^}]*grid-column:\s*1\s*/\s*-1',
        )

    def test_query_channels_are_managed_in_settings(self):
        query = self.source("crm.html")
        settings = self.source("accounts.html")
        for token in (
            'id="querySlotMobileTrigger"',
            'id="querySlotMobileMenu"',
            'id="querySlotMobileCount"',
            "toggleQuerySlotMenu",
            "selectAllQuerySlots",
            "selectedQuerySlotIds",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, query)
        self.assertIn('id="runtimeQuerySlots"', settings)
        self.assertIn("toggleRuntimeQuerySlot", settings)
        self.assertIn("query_slot_ids", settings)

    def test_query_batch_summary_is_persisted_and_replaces_static_badge(self):
        query = self.source("crm.html")
        self.assertIn('id="queryBatchSummary"', query)
        self.assertIn("formatBatchElapsed", query)
        self.assertIn("crm_last_query_summary", query)
        self.assertIn("captureLastQuerySummary", query)
        self.assertNotIn("AUTO SCHEDULING</span>", query)

    def test_product_library_search_links_to_knowledge_base(self):
        source = self.source("product_library.html")
        self.assertIn('class="library-search-head"', source)
        self.assertIn(
            '<a class="btn btn-secondary library-knowledge-link" href="https://yk.mlmll.cn" target="_blank" rel="noopener">前往知识库</a>',
            source,
        )

    def test_product_library_online_query_is_available_without_tool_login(self):
        source = self.source("product_library.html")
        self.assertIn('<button class="btn btn-secondary" onclick="confirmQueryBarcode()">在线查询</button>', source)
        self.assertNotIn("{% if can_online_query %}", source)
        self.assertNotIn("CAN_USE_ONLINE_QUERY", source)

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

    def test_transfer_mobile_controls_and_table_stay_within_the_page(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.slot-tabs\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.actions\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.aurora-transfer-table\s*\{[^}]*min-width:\s*100%',
        )
        self.assertIn(
            'body[data-aurora-page="transfer"] .aurora-transfer-table th:nth-child(1),\n'
            '    body[data-aurora-page="transfer"] .aurora-transfer-table td:nth-child(1),',
            mobile_css,
        )
        self.assertIn(
            'body[data-aurora-page="transfer"] .aurora-transfer-table th:nth-child(6),\n'
            '    body[data-aurora-page="transfer"] .aurora-transfer-table td:nth-child(6) { display: none; }',
            mobile_css,
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="transfer"\] \.aurora-realtime\s*\{[^}]*max-height:\s*520px',
        )

    def test_query_realtime_table_compacts_to_four_columns_on_mobile(self):
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] \.aurora-query-table\s*\{[^}]*min-width:\s*100%',
        )
        self.assertIn(
            'body[data-aurora-page="query"] .aurora-query-table th:nth-child(1),\n'
            '    body[data-aurora-page="query"] .aurora-query-table td:nth-child(1),',
            mobile_css,
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] \.aurora-query-table th:nth-child\(3\),[\s\S]*?'
            r'td:nth-child\(3\),[\s\S]*?th:nth-child\(5\),[\s\S]*?td:nth-child\(5\)\s*\{[^}]*display:\s*none',
        )

    def test_mobile_query_actions_keep_start_and_clear_on_first_row(self):
        query = self.source("crm.html")
        settings = self.source("accounts.html")
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        mobile_css = css.split("@media (max-width: 720px)", 1)[1]
        self.assertNotIn('id="retryLimitInput"', query)
        self.assertNotIn('class="aurora-channel-picker"', query)
        self.assertIn('id="batchRetryLimit"', settings)
        self.assertIn('id="runtimeQuerySlots"', settings)
        self.assertIn('batch_retry_limit', settings)
        self.assertIn('query_slot_ids', settings)
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] \.query-actions\s*\{[^}]*display:\s*grid;[^}]*'
            r'grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] #batchQueryBtn\s*\{[^}]*order:\s*1',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] \.query-actions \.btn-secondary\s*\{[^}]*order:\s*2',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] #stopBatchBtn\s*\{[^}]*order:\s*3;[^}]*'
            r'grid-column:\s*1\s*/\s*-1',
        )
        self.assertRegex(
            mobile_css,
            r'body\[data-aurora-page="query"\] \.card-head\s*\{[^}]*border-bottom:\s*0',
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
        for filename in ("crm.html", "index.html", "transfer.html", "inbound.html", "product_library.html", "accounts.html"):
            with self.subTest(filename=filename):
                self.assertIn("aurora-account-logout", self.source(filename))
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertIn(".aurora-account-logout", css)

    def test_every_work_page_places_plain_username_before_logout(self):
        filenames = ("crm.html", "index.html", "transfer.html", "inbound.html", "product_library.html", "accounts.html")
        for filename in filenames:
            with self.subTest(filename=filename):
                source = self.source(filename)
                self.assertIn('class="aurora-account-session"', source)
                self.assertIn('class="aurora-account-name"', source)
                self.assertLess(source.index('class="aurora-account-name"'), source.index('class="aurora-account-logout"'))
                self.assertNotIn("当前工具账号", source)
                self.assertNotIn("工具账号：", source)
                self.assertNotIn("（管理员）", source)
        css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.aurora-account-session\s*\{[^}]*display:\s*inline-flex")
        self.assertRegex(css, r"\.aurora-account-name\s*\{[^}]*text-overflow:\s*ellipsis")

    def test_tool_account_controls_use_compact_status_rows(self):
        filenames = ("crm.html", "index.html", "transfer.html", "inbound.html", "product_library.html", "accounts.html")
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

    def test_settings_bulk_login_buttons_show_each_slot_latest_message(self):
        template = self.source("accounts.html")
        self.assertIn("mergeBulkCrmSlotProgress(data.slots || [])", template)
        self.assertIn("slot.login_message", template)
        self.assertIn("slot-pill-current", template)

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
            source.index("'permission': 'inbound'"),
            source.index("'permission': 'product-library'"),
            source.index("'permission': 'accounts'"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_inbound_page_exposes_extraction_and_download_contract(self):
        inbound = self.source("inbound.html")
        for element_id in (
            "packingSlipInput",
            "startInboundBtn",
            "inboundStage",
            "inboundSlot",
            "inboundCurrentPage",
            "inboundLogs",
            "inboundSummary",
            "inboundPageCounts",
            "inboundWarnings",
            "inboundProducts",
            "downloadInboundBtn",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', inbound)
        for function_name in (
            "startInboundExtraction",
            "pollInboundStatus",
            "renderInboundStatus",
            "renderInboundResult",
            "downloadInboundXlsx",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(f"function {function_name}", inbound)
        self.assertIn("fetch('/api/inbound/start'", inbound)
        self.assertIn("fetch('/api/inbound/status?'", inbound)
        self.assertIn("sessionStorage.getItem('inbound_job_id')", inbound)
        self.assertIn("sessionStorage.setItem('inbound_job_id'", inbound)
        self.assertIn("sessionStorage.removeItem('inbound_job_id')", inbound)
        self.assertIn("params.set('latest', '1')", inbound)
        self.assertRegex(inbound, r"response\.status === 404[\s\S]*pollInboundStatus\(true\)")
        self.assertIn("let activeJobAccepted = false", inbound)
        self.assertIn("if (!activeJobAccepted)", inbound)
        self.assertRegex(
            inbound,
            r"visibilitychange[\s\S]*if \(!document\.hidden\)[\s\S]*restoreInboundJob\(\)",
        )
        self.assertIn("document.hidden", inbound)

    def test_inbound_page_exposes_gyj_login_and_plain_save_flow(self):
        inbound = self.source("inbound.html")
        for element_id in (
            "crmPackingTab", "gyjPurchaseTab", "crmPackingWorkspace",
            "gyjPurchaseWorkspace", "gyjLoginBtn", "gyjStartBtn",
            "gyjLogs", "gyjResult",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', inbound)
        for function_name in (
            "selectInboundWorkspace", "startGYJBackgroundLogin", "startGYJPurchaseInbound",
            "pollGYJInboundStatus",
        ):
            self.assertIn(f"function {function_name}", inbound)
        self.assertIn("fetch('/api/inbound/gyj/login'", inbound)
        self.assertIn("fetch('/api/inbound/gyj/login-status'", inbound)
        self.assertIn("fetch('/api/inbound/gyj/start'", inbound)
        self.assertIn("fetch('/api/inbound/gyj/status?'", inbound)
        self.assertIn("昆山怡口净水系统有限公司", inbound)
        self.assertIn("江西天麓", inbound)
        self.assertIn("沈桥仓", inbound)
        self.assertNotIn('id="gyjStage"', inbound)
        self.assertNotIn('id="gyjProgress"', inbound)

    def test_inbound_gyj_login_uses_backend_credentials_contract(self):
        inbound = self.source("inbound.html")
        for element_id in ("gyjUsername", "gyjPassword", "gyjRememberLogin", "gyjCaptcha"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', inbound)
        for function_name in ("loadGYJCredentials", "startGYJBackgroundLogin", "submitGYJCaptcha"):
            self.assertIn(f"function {function_name}", inbound)
        self.assertIn("fetch('/api/inbound/gyj/credentials'", inbound)
        self.assertIn("fetch('/api/inbound/gyj/login/captcha'", inbound)
        self.assertIn("if (data.logged_in || data.waiting_captcha)", inbound)

    def test_inbound_gyj_captcha_preview_can_be_refreshed_without_storage(self):
        inbound = self.source("inbound.html")
        for element_id in ("gyjCaptchaImage", "gyjCaptchaRefreshBtn"):
            self.assertIn(f'id="{element_id}"', inbound)
        self.assertIn("function refreshGYJCaptchaPreview", inbound)
        self.assertIn("fetch('/api/inbound/gyj/captcha-preview'", inbound)
        self.assertNotIn("gyj_captcha_image", inbound)

    def test_inbound_gyj_captcha_preview_hides_empty_response(self):
        inbound = self.source("inbound.html")

        self.assertIn("const captchaImage = data && data.success ? data.captcha_image : ''", inbound)
        self.assertIn("captchaImage && captchaImage.startsWith('data:image/')", inbound)
        self.assertIn("image.removeAttribute('src');", inbound)

    def test_inbound_page_escapes_crm_values_and_uses_server_side_download(self):
        inbound = self.source("inbound.html")
        self.assertIn("function escapeHtml", inbound)
        self.assertIn("result.items || []", inbound)
        self.assertIn("item.serials || []", inbound)
        self.assertIn("data.page_counts || []", inbound)
        self.assertIn("result.duplicate_serials || []", inbound)
        self.assertIn("data.done && data.success && data.download_url", inbound)
        self.assertIn("new URL(inboundDownloadUrl, window.location.origin)", inbound)
        self.assertIn("url.origin !== window.location.origin", inbound)
        self.assertNotIn("result.rows", inbound)
        self.assertNotIn("JSON.stringify(inbound", inbound)

    def test_inbound_page_clears_stale_result_surfaces_for_new_and_failed_jobs(self):
        inbound = self.source("inbound.html")
        self.assertIn("function clearInboundResultSurfaces()", inbound)
        self.assertRegex(
            inbound,
            r"function startInboundExtraction\(\)[\s\S]*?clearInboundResultSurfaces\(\)[\s\S]*?fetch\('/api/inbound/start'",
        )
        self.assertRegex(
            inbound,
            r"if \(data\.done\)[\s\S]*?data\.success && data\.result[\s\S]*?clearInboundResultSurfaces\(\)",
        )

    def test_inbound_stale_job_fallback_stops_polling_when_no_latest_job_exists(self):
        inbound = self.source("inbound.html")
        self.assertRegex(
            inbound,
            r"if \(preferLatest && !data\.job_id\)\s*\{[\s\S]*?clearInterval\(inboundPollTimer\)[\s\S]*?inboundPollTimer = null;[\s\S]*?return;",
        )

    def test_shared_navigation_has_six_columns_and_inbound_permission(self):
        settings = self.source("accounts.html")
        aurora = (STATIC / "aurora.js").read_text(encoding="utf-8")
        layout_css = (STATIC / "app_layout.css").read_text(encoding="utf-8")
        aurora_css = (STATIC / "aurora.css").read_text(encoding="utf-8")
        self.assertRegex(settings, r'<input type="checkbox" value="inbound">\s*入库')
        self.assertIn("'/inbound':", aurora)
        self.assertRegex(layout_css, r"\.page-nav\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(\d+px,\s*1fr\)\)")
        # aurora.css declares .page-nav twice (desktop + mobile media query).
        # Both must use auto-fit so limited-permission accounts don’t end up
        # with a row of empty cells on the right.
        self.assertNotIn(
            "grid-template-columns: repeat(6, minmax(0, 1fr))",
            aurora_css,
            "the legacy 6-column .page-nav rule must not be present once we switched to auto-fit",
        )
        # aurora.css declares .page-nav twice (desktop + mobile media query).
        # desktop uses grid auto-fit; mobile switched to flex because grid
        # auto-fit at min 64px overflowed a 430px phone with 6 admin tabs.
        # Either layout is acceptable; what matters is the legacy 6-column
        # grid is gone, so low-permission accounts don't show empty cells.
        desktop_auto_fit = re.search(
            r"\.page-nav\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(\d+px,\s*1fr\)\)",
            aurora_css,
        )
        mobile_flex = re.search(
            r"\.page-nav\s*\{[^}]*display:\s*flex\s*!important\s*;[^}]*flex-wrap:\s*nowrap",
            aurora_css,
        )
        self.assertTrue(
            desktop_auto_fit or mobile_flex,
            "aurora.css must declare either an auto-fit grid OR a flex .page-nav so the nav row adapts to the number of visible tabs",
        )

    def test_inbound_navigation_uses_compact_vertical_transfer_glyph(self):
        aurora = (STATIC / "aurora.js").read_text(encoding="utf-8")
        self.assertIn("'/transfer': ['⇄', '移库']", aurora)
        self.assertIn("'/inbound': ['⇅', '入库']", aurora)
        self.assertNotIn("'/inbound': ['↕', '入库']", aurora)
        self.assertNotIn("'/inbound': ['⇩', '入库']", aurora)

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

    def test_query_and_transfer_log_dialogs_refresh_while_open(self):
        query = self.source("crm.html")
        transfer = self.source("transfer.html")
        aurora = (STATIC / "aurora.js").read_text(encoding="utf-8")

        self.assertIn("window.refreshAuroraLog", aurora)
        self.assertIn("queryItemLogKey(item)", query)
        self.assertIn("refreshAuroraLog(", query)
        self.assertIn("`transfer:${record.record_id}`", transfer)
        self.assertIn("refreshAuroraLog(", transfer)

    def test_transfer_record_accepts_order_number_from_job_progress(self):
        transfer = self.source("transfer.html")

        self.assertIn(
            "jobData.order_no || (jobData.result && jobData.result.order_no)",
            transfer,
        )

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
            "有无服务单",
            "查询日期",
            "客户姓名",
        ):
            with self.subTest(label=label):
                self.assertIn(label, source)
        self.assertIn("getServicePresenceStatus", source)

    def test_results_page_uses_revision_and_chunked_rendering(self):
        source = self.source("index.html")
        self.assertIn("let resultDataRevision = ''", source)
        self.assertIn("let resultsLoadPending = false", source)
        self.assertIn("const RESULT_RENDER_CHUNK_SIZE = 100", source)
        self.assertIn("let resultRenderGeneration = 0", source)
        self.assertIn("requestAnimationFrame(renderChunk)", source)
        self.assertIn("params.set('revision', resultDataRevision)", source)
        self.assertIn("document.hidden && !clearSelection", source)
        self.assertNotIn("fetch('/api/filter-options')", source)

    def test_results_page_restores_service_close_job_from_session(self):
        source = self.source("index.html")
        for token in (
            "const SERVICE_CLOSE_SESSION_KEY = 'crm_service_close_job_v1'",
            "function saveServiceCloseJob()",
            "function clearSavedServiceCloseJob()",
            "function restoreServiceCloseJob()",
            "sessionStorage.setItem(SERVICE_CLOSE_SESSION_KEY",
            "sessionStorage.removeItem(SERVICE_CLOSE_SESSION_KEY)",
            "restoreServiceCloseJob();",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_results_page_groups_barcodes_by_latest_installation_order(self):
        source = self.source("index.html")
        for token in (
            "function isInstallationServiceOrder(row)",
            "function getServiceOrderSortKey(item)",
            "const aServiceNo = getServiceOrderSortKey(a)",
            "const bServiceNo = getServiceOrderSortKey(b)",
            "aServiceNo.localeCompare(bServiceNo, undefined, {numeric: true})",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_query_summary_includes_failure_count_and_requeue_button(self):
        source = self.source("crm.html")
        self.assertIn('id="queryBatchSummary"', source)
        self.assertIn('id="retryFailedBarcodesBtn"', source)
        self.assertIn("retryFailedBarcodes()", source)
        self.assertIn("multiBatchJobs.failedBarcodes", source)
        self.assertIn("失败条码填入并自动开始查询", source)
        self.assertIn("formatBatchElapsed", source)

    def test_query_page_discovers_latest_job_across_browsers(self):
        source = self.source("crm.html")
        self.assertIn("querySharedSyncTimer", source)
        self.assertIn("pollMultiBatchStatus({latest: true})", source)
        self.assertIn("params.set('latest', '1')", source)

    def test_query_realtime_table_only_renders_actionable_rows(self):
        source = self.source("crm.html")
        self.assertIn("function queryVisibleItems(items)", source)
        self.assertIn("['running', 'error', 'stopped'].includes", source)
        self.assertIn("当前无查询中或失败条码", source)

    def test_product_library_renders_online_query_logs_in_live_result_area(self):
        source = self.source("product_library.html")
        self.assertIn('id="lookupBox" class="note" aria-live="polite" aria-atomic="false"', source)
        self.assertIn("function renderLibraryQueryInlineStatus(data)", source)
        self.assertIn("renderLibraryQueryInlineStatus(data)", source)

    def test_product_library_query_logs_are_incremental_and_visibility_aware(self):
        source = self.source("product_library.html")
        self.assertIn("let lastLibraryQueryLogSeq = 0", source)
        self.assertIn("function mergeLibraryQueryLogs(rows)", source)
        self.assertIn("params.set('since', String(lastLibraryQueryLogSeq))", source)
        self.assertIn("if (document.hidden) return", source)

    def test_shared_logs_are_bounded_lazy_and_debounced(self):
        source = (STATIC / "log_modal.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_HISTORY = 1000", source)
        self.assertIn("const MAX_RENDERED_HISTORY = 500", source)
        self.assertIn("function schedulePersistHistory()", source)
        self.assertIn("overlay.classList.contains('show')", source)
        self.assertIn("logHistory.slice(-MAX_RENDERED_HISTORY)", source)

    def test_work_page_polling_skips_hidden_tabs(self):
        for filename in ("crm.html", "index.html", "transfer.html", "inbound.html", "product_library.html", "accounts.html"):
            with self.subTest(filename=filename):
                self.assertIn("document.hidden", self.source(filename))

    def test_transfer_summary_has_product_and_failure_detail_drilldown(self):
        source = self.source("transfer.html")
        self.assertIn('id="transferSummaryDetailModal"', source)
        self.assertIn("openTransferSummaryProduct", source)
        self.assertIn("openTransferSummaryFailures", source)
        self.assertIn("failure_details", source)

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

    def test_transfer_realtime_history_is_operation_based(self):
        source = self.source("transfer.html")

        self.assertNotIn("AUTO REFRESH", source)
        self.assertIn("clearTransferRealtimeRecords()", source)
        self.assertIn("<th>操作</th>", source)
        self.assertIn('colspan="7"', source)
        self.assertIn("crm_transfer_realtime_records_v2", source)
        self.assertIn("transferRealtimeRecords.unshift", source)
        self.assertNotIn("transferSlots.forEach(ensureTransferRecord)", source)
        self.assertIn("function deleteTransferRealtimeRecord(recordId)", source)
        self.assertIn("function clearTransferRealtimeRecords()", source)
        self.assertRegex(
            source,
            r'onclick="openTransferChannelLog\(\'\$\{escapeHtml\(record\.record_id\)\}\'\)"',
        )
        self.assertRegex(
            source,
            r'onclick="deleteTransferRealtimeRecord\(\'\$\{escapeHtml\(record\.record_id\)\}\'\)"',
        )

    def test_transfer_realtime_status_uses_latest_log_while_running(self):
        source = self.source("transfer.html")

        self.assertIn("function latestTransferLogMessage(logs)", source)
        self.assertIn(
            "const latestLogMessage = latestTransferLogMessage(record.logs);",
            source,
        )
        self.assertIn(
            "latestLogMessage || jobData.message || '正在生成 CRM 移库单'",
            source,
        )

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


    def test_transfer_records_use_durable_api_and_explicit_delete_actions(self):
        transfer = self.source("transfer.html")
        self.assertIn("/api/transfer-records", transfer)
        self.assertIn("DELETE", transfer)
        self.assertIn("loadTransferRealtimeRecords", transfer)
        self.assertIn("migrateLegacyTransferRecords", transfer)

    def test_transfer_records_refresh_independently_of_slot_switching(self):
        transfer = self.source("transfer.html")
        self.assertIn("refreshTransferRealtimeRecordsFromServer", transfer)
        self.assertIn("let transferRecordsRevision = ''", transfer)
        self.assertIn("function scheduleTransferRealtimeRefresh(delay=null)", transfer)
        self.assertIn("response.unchanged", transfer)
        self.assertIn("params.set('revision', transferRecordsRevision)", transfer)
        self.assertIn("? 1000 : 5000", transfer)
        self.assertIn("if (document.hidden)", transfer)

    def test_transfer_summary_has_a_dedicated_product_quantity_card(self):
        transfer = self.source("transfer.html")
        self.assertIn('id="transferSummaryCard"', transfer)
        self.assertIn("汇总产品数量明细", transfer)
        self.assertIn("transferSummaryCard.hidden = false", transfer)

    def test_transfer_summary_reveals_product_name_and_quantity_rows(self):
        transfer = self.source("transfer.html")
        self.assertIn('class="summary-product-list"', transfer)
        self.assertIn("row.product_name", transfer)
        self.assertIn("row.quantity", transfer)
        self.assertIn("box.style.display = 'block'", transfer)

    def test_transfer_summary_can_collapse_after_a_successful_transfer(self):
        transfer = self.source("transfer.html")
        self.assertIn('id="transferSummaryToggle"', transfer)
        self.assertIn("function setTransferSummaryCollapsed(collapsed)", transfer)
        self.assertIn("setTransferSummaryCollapsed(true)", transfer)
        self.assertIn("transferSummaryCard.classList.toggle('is-collapsed', Boolean(collapsed))", transfer)

    def test_transfer_summary_collapses_when_submission_is_accepted(self):
        transfer = self.source("transfer.html")
        submit = transfer.split("async function submitTransfer()", 1)[1].split("function appendLog", 1)[0]
        self.assertIn("setTransferSummaryCollapsed(true)", submit)
        self.assertLess(submit.index("setTransferSummaryCollapsed(true)"), submit.index("startTransferPolling"))

    def test_transfer_summary_uses_product_rows_without_duplicate_detail_table(self):
        transfer = self.source("transfer.html")
        self.assertIn(".summary-product-row {", transfer)
        self.assertIn("background:rgba(15, 31, 52, .82)", transfer)
        self.assertNotIn("const details = (summary.details || [])", transfer)
        self.assertNotIn('已选条码 ${summary.total || 0} 个，产品 ${(summary.groups || []).length} 条，数量合计 ${totalQty}。', transfer)
        self.assertNotIn("<th>条码</th><th>匹配前缀</th><th>产品型号</th><th>产品名称</th>", transfer)

    def test_transfer_summary_detail_modal_uses_aurora_colors(self):
        transfer = self.source("transfer.html")
        self.assertIn(".summary-detail-modal {", transfer)
        self.assertIn("background:rgba(8, 18, 32, .98)", transfer)
        self.assertIn("border:1px solid rgba(139,190,220,.28)", transfer)
        self.assertIn(".summary-detail-table-wrap { overflow:auto; border:1px solid rgba(139,190,220,.22);", transfer)
        self.assertNotIn(".summary-detail-modal { width:min(900px, 100%); max-height:min(82vh, 760px); background:#fff;", transfer)

    def test_shared_navigation_uses_stable_short_labels(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        for label in ("查询", "结果", "移库", "入库", "匹配", "设置"):
            self.assertIn(f"'label': '{label}'", app_source)
        aurora = (STATIC / "aurora.js").read_text(encoding="utf-8")
        for label in ("查询", "结果", "移库", "入库", "匹配", "设置"):
            self.assertIn(f"'{label}'", aurora)
        self.assertIn("aurora-nav-label", aurora)
        self.assertNotIn("anchor.textContent =", aurora)

    def test_inbound_page_has_shared_history_actions(self):
        inbound = self.source("inbound.html")
        self.assertIn('id="inboundHistory"', inbound)
        self.assertIn('.inbound-progress[hidden] { display:none !important; }', inbound)
        self.assertIn("function loadInboundHistory", inbound)
        self.assertIn("function selectInboundHistory", inbound)
        self.assertIn("function deleteInboundHistory", inbound)
        self.assertIn("/api/inbound/history", inbound)

    def test_inbound_products_fold_serials_and_copy_chunked_values(self):
        inbound = self.source("inbound.html")
        self.assertIn("function toggleInboundSerials", inbound)
        self.assertIn("serials.length ? ` onclick=\"toggleInboundSerials('${productId}')\"`", inbound)
        self.assertIn("serials.slice(index, index + 100)", inbound)
        self.assertIn('data-copy="${escapeHtml(item.product_code)}"', inbound)
        self.assertIn('data-copy="${escapeHtml(item.description || \'无物料描述\')}"', inbound)
        self.assertNotIn("· 订单 ${escapeHtml(orders", inbound)
        self.assertNotIn('class="inbound-product-total"', inbound)
        self.assertIn("条码 ${escapeHtml(serials.length)} 条", inbound)
        self.assertIn("无条码配件 × ${escapeHtml(unbarcoded)}", inbound)
        self.assertNotIn("${unbarcoded ? `<code>无条码配件", inbound)

    def test_inbound_result_surfaces_use_dark_contrast_without_gyj_state_cards(self):
        inbound = self.source("inbound.html")
        self.assertRegex(inbound, r"\.inbound-history-row\s*\{[^}]*background:rgba\(8,25,48,.88\)")
        self.assertRegex(inbound, r"\.inbound-product-head\s*\{[^}]*background:rgba\(13,44,72,.92\)[^}]*color:#e6f6ff")
        self.assertRegex(inbound, r"\.inbound-serials code\s*\{[^}]*background:rgba\(12,36,62,.95\)[^}]*color:#e6f6ff")
        self.assertNotIn('id="gyjStage"', inbound)
        self.assertNotIn('id="gyjProgress"', inbound)

    def test_inbound_workspace_tabs_use_the_dark_glass_theme(self):
        inbound = self.source("inbound.html")
        tab_rule = re.search(r"\.inbound-workspace-tab\s*\{([^}]*)\}", inbound, re.S)
        active_rule = re.search(r"\.inbound-workspace-tab\.active\s*\{([^}]*)\}", inbound, re.S)
        self.assertIsNotNone(tab_rule)
        self.assertIsNotNone(active_rule)
        self.assertRegex(tab_rule.group(1), r"background:\s*rgba\(13,44,72,.88\)")
        self.assertRegex(tab_rule.group(1), r"color:\s*#d8f5ff")
        self.assertRegex(active_rule.group(1), r"border-color:\s*#48cfff")
        self.assertRegex(active_rule.group(1), r"color:\s*#f2fbff")

    def test_inbound_renders_saved_gyj_products_with_collapsed_serials(self):
        inbound = self.source("inbound.html")
        self.assertIn("function renderGYJSavedProducts", inbound)
        self.assertIn("function toggleGYJSavedSerials", inbound)
        self.assertIn("展开条码", inbound)
        self.assertIn(".inbound-serials[hidden] { display:none !important; }", inbound)
        self.assertIn('id="gyjSavedSerials-${productId}" hidden', inbound)
        self.assertNotIn('`<div class="inbound-serials"><code>无条码</code></div>`', inbound)
        self.assertIn('class="gyj-saved-serial-toggle"', inbound)
        self.assertNotIn('`${serials.length ? `<button class="inbound-product-toggle"', inbound)

    def test_inbound_gyj_result_updates_while_lines_are_being_saved(self):
        inbound = self.source("inbound.html")
        self.assertIn("if (data.result && Array.isArray(data.result.products))", inbound)
        self.assertIn("'completed_products': []", (ROOT / "app.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
