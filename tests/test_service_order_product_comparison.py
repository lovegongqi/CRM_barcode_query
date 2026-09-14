import contextlib
import unittest
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
                        document.body.innerHTML = '<div>{order_no} 产品详情</div>';
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
