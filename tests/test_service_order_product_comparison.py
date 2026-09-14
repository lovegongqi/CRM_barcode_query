import unittest

import app as app_module


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
