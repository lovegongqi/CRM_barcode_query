import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as app_module
from playwright.sync_api import sync_playwright


class ServiceOrderProductComparisonTests(unittest.TestCase):
    def test_comparison_aggregates_codes_and_reports_all_four_business_states(self):
        service_products = [
            {"product_code": " ab-01 ", "product_name": "A", "barcode": "SN-A1"},
            {"product_code": "AB-01", "product_name": "A", "barcode": "SN-A2"},
            {"product_code": "B-02", "product_name": "B", "barcode": "SN-B1"},
            {"product_code": "D-04", "product_name": "D", "barcode": "SN-D1"},
        ]
        order_products = [
            {"product_code": "AB-01", "product_name": "A", "quantity": "1"},
            {"product_code": "ab-01", "product_name": "A", "quantity": "1.00"},
            {"product_code": "B-02", "product_name": "B", "quantity": 3},
            {"product_code": "C-03", "product_name": "C", "quantity": 1},
        ]

        rows = app_module._build_service_order_product_comparison(
            service_products, order_products
        )

        self.assertEqual(rows, [
            {"product_code": "AB-01", "product_name": "A", "order_quantity": 2,
             "service_quantity": 2, "status": "matched", "status_label": "一致"},
            {"product_code": "B-02", "product_name": "B", "order_quantity": 3,
             "service_quantity": 1, "status": "quantity_mismatch", "status_label": "数量不一致"},
            {"product_code": "D-04", "product_name": "D", "order_quantity": 0,
             "service_quantity": 1, "status": "order_missing", "status_label": "订单缺少"},
            {"product_code": "C-03", "product_name": "C", "order_quantity": 1,
             "service_quantity": 0, "status": "service_missing", "status_label": "服务单缺少"},
        ])

    def test_quantity_parser_rejects_missing_negative_and_fractional_values(self):
        for value in (None, "", "-1", "1.5", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    app_module._parse_order_product_quantity(value)

    def test_product_code_normalization_preserves_leading_zeroes(self):
        self.assertEqual(
            app_module._normalize_comparison_product_code(" 00ab-01 "),
            "00AB-01",
        )

    def test_service_detail_normalizes_cached_order_lists_without_losing_legacy_fields(self):
        detail = app_module._normalize_service_order_detail({
            "service_no": "FWD20260914001",
            "legacy_note": "keep me",
            "order_lookup": {
                "order_no": "SO20260914001",
                "products": None,
                "comparison": "invalid",
                "legacy_lookup_field": "keep this too",
            },
        })

        self.assertEqual(detail["legacy_note"], "keep me")
        self.assertEqual(detail["order_lookup"], {
            "order_no": "SO20260914001",
            "products": [],
            "comparison": [],
            "legacy_lookup_field": "keep this too",
        })

    def test_legacy_service_detail_does_not_gain_a_fake_successful_lookup(self):
        detail = app_module._normalize_service_order_detail({
            "service_no": "FWD20260914001",
            "products": [],
        })

        self.assertEqual(detail["order_lookup"], {})


def make_crm_session():
    session = object.__new__(app_module.CRMSession)
    session.lock = __import__("threading").RLock()
    session.logged_in = True
    session.page = mock.Mock()
    return session


class CRMRelatedOrderTests(unittest.TestCase):
    def _success_patches(self, session, fields=None, products=None):
        patches = contextlib.ExitStack()
        patches.enter_context(mock.patch.object(session, "is_alive", return_value=True))
        patches.enter_context(mock.patch.object(session, "_is_current_page_logged_in", return_value=True))
        patches.enter_context(mock.patch.object(session, "_open_service_order_list", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_search_service_order", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_open_service_order_detail", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_service_detail_fields", return_value=fields or []))
        patches.enter_context(mock.patch.object(session, "_open_store_order_list", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_search_store_order", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_open_store_order_detail", return_value=(True, "")))
        patches.enter_context(mock.patch.object(session, "_store_order_detail_products", return_value=products or []))
        return patches

    def test_related_order_number_uses_only_explicit_supported_labels(self):
        fields = [
            {"label": "备注", "value": "订单 SO-WRONG"},
            {"label": "关联订单号", "value": "SO20260914001"},
        ]
        self.assertEqual(
            app_module._related_order_no_from_service_fields(fields),
            "SO20260914001",
        )

    def test_related_order_number_accepts_exact_live_crm_label_and_ignores_remarks(self):
        fields = [
            {"label": "备注", "value": "关联订单 ORD-WRONG"},
            {"label": "关联订单", "value": "ORD2511110743"},
        ]
        self.assertEqual(
            app_module._related_order_no_from_service_fields(fields),
            "ORD2511110743",
        )

    def test_store_order_list_uses_live_crm_route_and_accepts_live_or_legacy_heading(self):
        session = make_crm_session()
        session.page.url = "http://crmportal.ecowaterchina.net.cn/#/workOrder/list"
        session.page.inner_text.return_value = "订单查询 订单号"
        with mock.patch.object(app_module, "load_crm_config", return_value={
            "website": {"url": "http://crmportal.ecowaterchina.net.cn/"},
        }), mock.patch.object(session, "is_alive", return_value=True), \
             mock.patch.object(session, "_goto", return_value=(True, "")) as goto, \
             mock.patch.object(app_module.time, "sleep"):
            self.assertEqual(
                session._store_order_list_url(),
                "http://crmportal.ecowaterchina.net.cn/#/ordertwoc/list",
            )
            self.assertTrue(session._store_order_list_ready())
            ok, message = session._open_store_order_list(lambda *_: None)
        self.assertTrue(ok, message)
        goto.assert_called_once_with(
            "http://crmportal.ecowaterchina.net.cn/#/ordertwoc/list", timeout=60000
        )

    def test_store_order_list_ready_accepts_legacy_heading(self):
        session = make_crm_session()
        session.page.inner_text.return_value = "订单列表 订单号"
        self.assertTrue(session._store_order_list_ready())

    def test_query_related_order_products_returns_service_fields_and_order_rows(self):
        session = make_crm_session()
        fields = [{"label": "关联订单号", "value": "SO20260914001"}]
        products = [{"product_name": "前置过滤器", "product_code": "916046216", "quantity": 2}]
        with self._success_patches(session, fields, products):
            ok, result = session.query_related_order_products("FWD20260914001")
        self.assertTrue(ok)
        self.assertEqual(result, {
            "service_no": "FWD20260914001",
            "order_no": "SO20260914001",
            "service_fields": fields,
            "order_products": products,
        })

    def test_query_related_order_products_names_missing_related_order_stage(self):
        session = make_crm_session()
        with self._success_patches(session, fields=[{"label": "备注", "value": "无关联"}]):
            ok, result = session.query_related_order_products("FWD20260914001")
        self.assertFalse(ok)
        self.assertIn("服务单没有关联订单号", result["error"])

    def test_query_related_order_products_names_exact_order_search_failure_stage(self):
        session = make_crm_session()
        fields = [{"label": "关联订单号", "value": "SO20260914001"}]
        with self._success_patches(session, fields=fields), mock.patch.object(
            session,
            "_search_store_order",
            return_value=(False, "订单搜索后未找到精确订单号：SO20260914001"),
        ):
            ok, result = session.query_related_order_products("FWD20260914001")
        self.assertFalse(ok)
        self.assertIn("订单搜索后未找到精确订单号", result["error"])

    def test_query_related_order_products_names_empty_product_table_stage(self):
        session = make_crm_session()
        fields = [{"label": "关联订单号", "value": "SO20260914001"}]
        with self._success_patches(session, fields=fields, products=[]):
            ok, result = session.query_related_order_products("FWD20260914001")
        self.assertFalse(ok)
        self.assertIn("订单详情未读取到产品明细", result["error"])

    def test_store_order_detail_clicks_exact_order_link_before_matching_cell(self):
        order_no = "SO20260914001"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(f"""
                    <table><tbody><tr><td><a id="order-link">{order_no}</a></td></tr></tbody></table>
                    <script>
                    document.querySelector('#order-link').addEventListener('click', event => {{
                        event.preventDefault();
                        document.body.innerHTML = '<div class="el-form-item"><label>订单号</label><div class="el-form-item__content">{order_no}</div></div><table><thead><tr><th>产品编码</th><th>数量</th></tr></thead><tbody><tr><td>A</td><td>1</td></tr></tbody></table>';
                    }});
                    </script>
                """)
                session = make_crm_session()
                session.page = page
                with mock.patch.object(app_module.time, "sleep"):
                    ok, message = session._open_store_order_detail(order_no)
                self.assertTrue(ok, message)
            finally:
                browser.close()


class CRMOrderProductDOMTests(unittest.TestCase):
    def setUp(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()
        self.addCleanup(self.playwright.stop)
        self.addCleanup(self.browser.close)
        self.page = self.browser.new_page()
        self.session = make_crm_session()
        self.session.page = self.page

    def detail_html(self, headers, rows, order_no="SO-100", extra=""):
        return f'''<div class="el-form-item"><label>订单号</label><div class="el-form-item__content">{order_no}</div></div>
            <table><thead><tr>{''.join(f'<th>{cell}</th>' for cell in headers)}</tr></thead>
            <tbody>{''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)}</tbody></table>{extra}'''

    def test_detail_readiness_waits_for_exact_context_and_loaded_recognized_table(self):
        list_html = '<h1>订单列表 产品筛选</h1><table><tbody><tr><td><a>SO-100</a></td></tr></tbody></table>'
        self.page.set_content(list_html)
        snapshots = [
            list_html,
            self.detail_html(["产品编码", "数量"], [["A", "1"]], order_no="SO-1000"),
            self.detail_html(["产品编码", "数量"], [], extra='<div class="el-loading-mask">加载中</div>'),
            self.detail_html(["产品编码", "数量"], [["A", "1"]]),
        ]
        steps = []

        def advance(_delay):
            steps.append(len(steps))
            self.page.set_content(snapshots[min(len(steps) - 1, 3)])

        with mock.patch.object(app_module.time, "sleep", side_effect=advance):
            ok, message = self.session._open_store_order_detail("SO-100")
        self.assertTrue(ok, message)
        self.assertEqual(len(steps), 4, "a list, different order, or loading table cannot be ready")
        self.assertEqual(self.session._store_order_detail_products(), [
            {"product_name": "", "product_code": "A", "quantity": 1},
        ])

    def test_store_order_search_uses_unlabeled_top_input_not_pager_and_waits_for_exact_anchor(self):
        order_no = "ORD2511110743"
        self.page.set_content(f"""
            <h1>订单查询</h1>
            <input id="order-search" type="search" style="width: 520px">
            <button id="search-button">查询</button>
            <table><thead><tr><th>订单号</th></tr></thead><tbody id="orders"></tbody></table>
            <div class="el-pagination"><input id="pager" type="text" style="width: 40px"></div>
            <script>
              document.querySelector('#search-button').addEventListener('click', () => {{
                document.querySelector('#orders').innerHTML = '<tr><td><a>ORD25111107439</a></td></tr>';
                setTimeout(() => {{
                  document.querySelector('#orders').innerHTML = '<tr><td><a>{order_no}</a></td></tr>';
                }}, 20);
              }});
            </script>
        """)

        self.assertTrue(self.session._set_store_order_search_keyword(order_no))
        self.assertEqual(self.page.input_value("#order-search"), order_no)
        self.assertEqual(self.page.input_value("#pager"), "")
        self.assertFalse(self.session._store_order_search_snapshot(order_no)["found"])

        def click_search():
            self.page.click("#search-button")
            return True

        with mock.patch.object(self.session, "_click_store_order_search_button", side_effect=click_search), \
             mock.patch.object(app_module.time, "sleep", side_effect=lambda _: self.page.wait_for_timeout(30)):
            ok, message = self.session._search_store_order(order_no)
        self.assertTrue(ok, message)

    def test_detail_readiness_accepts_only_stable_explicit_empty_table(self):
        detail = self.detail_html(["产品编码", "数量"], [])
        table_empty = detail.replace('<tbody></tbody>', '<tbody><tr><td colspan="2">暂无数据</td></tr></tbody>')
        wrapper_empty = detail.replace('<table>', '<div class="el-table"><table>').replace(
            '</table>', '</table><div class="el-table__empty-block">暂无数据</div></div>',
        )
        for empty in (table_empty, wrapper_empty):
            with self.subTest(empty=empty):
                self.page.set_content('<table><tbody><tr><td><a>SO-100</a></td></tr></tbody></table>')
                steps = []

                def show_empty(_delay):
                    steps.append(1)
                    self.page.set_content(empty)

                with mock.patch.object(app_module.time, "sleep", side_effect=show_empty):
                    ok, message = self.session._open_store_order_detail("SO-100")
                self.assertTrue(ok, message)
                self.assertGreaterEqual(len(steps), 2)
                self.assertEqual(self.session._store_order_detail_products(), [])

    def test_unrelated_empty_sibling_cannot_finish_product_table_readiness(self):
        self.page.set_content('<table><tbody><tr><td><a>SO-100</a></td></tr></tbody></table>')
        sibling = '<section><h2>付款记录</h2><div>暂无数据</div></section>'
        pending = self.detail_html(["产品编码", "数量"], [], extra=sibling)
        snapshots = [
            pending + '<div class="el-loading-mask">加载中</div>',
            pending,
            pending,
            self.detail_html(["产品编码", "数量"], [["A", "2"]], extra=sibling),
        ]
        steps = []

        def advance(_delay):
            steps.append(1)
            self.page.set_content(snapshots[min(len(steps) - 1, 3)])

        with mock.patch.object(app_module.time, "sleep", side_effect=advance):
            ok, message = self.session._open_store_order_detail("SO-100")
        self.assertTrue(ok, message)
        self.assertEqual(len(steps), 4, "unrelated empty text must not finish readiness before product rows load")
        self.assertEqual(self.session._store_order_detail_products(), [
            {"product_name": "", "product_code": "A", "quantity": 2},
        ])

    def test_quantity_header_ignores_unrelated_preceding_quantity(self):
        self.page.set_content(self.detail_html(
            ["商品名称", "已发数量", "物料编码", "订 单 数 量"],
            [["净水器", "99", "00ab", "2"]],
        ))
        self.assertEqual(self.session._store_order_detail_products(), [
            {"product_name": "净水器", "product_code": "00AB", "quantity": 2},
        ])

    def test_ambiguous_supported_quantity_headers_fail(self):
        for headers in (["产品编码", "数量", "订单数量"], ["产品编码", "数量", "数量"]):
            with self.subTest(headers=headers):
                self.page.set_content(self.detail_html(headers, [["A", "1", "2"]]))
                with self.assertRaisesRegex(ValueError, "数量.*表头|表头.*数量"):
                    self.session._store_order_detail_products()

    def test_incomplete_named_product_fails_lookup_and_preserves_successful_cache(self):
        for incomplete in (["未编码净水器", "", ""], ["缺数量净水器", "B", ""], ['<input value="未编码净水器">', "", ""]):
            with self.subTest(incomplete=incomplete), tempfile.TemporaryDirectory() as tempdir:
                self.page.set_content(self.detail_html(["产品名称", "产品编码", "数量"], [["A", "A", "1"], incomplete]))
                filepath = Path(tempdir) / "FWD20260914001.json"
                original = json.dumps({"products": [], "order_lookup": {"order_no": "SO-OLD", "queried_at": "yesterday"}}).encode()
                filepath.write_bytes(original)
                job = app_module._empty_order_product_job("FWD20260914001")
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(app_module, "SERVICE_ORDER_DIR", tempdir))
                    stack.enter_context(mock.patch.dict(app_module.order_product_jobs, {job["job_id"]: job}, clear=True))
                    stack.enter_context(mock.patch.object(self.session, "is_alive", return_value=True))
                    for method in ("_open_service_order_list", "_search_service_order", "_open_service_order_detail", "_open_store_order_list", "_search_store_order", "_open_store_order_detail"):
                        stack.enter_context(mock.patch.object(self.session, method, return_value=(True, "")))
                    app_module._run_order_product_job(job["job_id"], self.session)
                self.assertFalse(job["success"], "one incomplete named row must invalidate the whole lookup")
                self.assertTrue(job["error"])
                self.assertEqual(filepath.read_bytes(), original)
