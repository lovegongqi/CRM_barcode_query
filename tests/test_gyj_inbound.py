import unittest

from gyj_inbound import GYJInboundError, build_gyj_purchase_lines


def inbound_result(items, duplicate_serials=None):
    return {
        "packing_slip_no": "SH202607210002",
        "items": items,
        "duplicate_serials": duplicate_serials or [],
    }


class GYJInboundLineTest(unittest.TestCase):
    def test_splits_serials_into_chunks_of_at_most_100(self):
        result = inbound_result([
            {
                "product_code": "926019528",
                "description": "GAC滤芯",
                "order_numbers": ["403565"],
                "serials": [f"SN{index:03d}" for index in range(200)],
                "expected_quantity": 200,
                "serial_count": 200,
                "unbarcoded_quantity": 0,
                "quantity_mismatch": False,
            }
        ])

        lines = build_gyj_purchase_lines(result)

        self.assertEqual([len(line["serials"]) for line in lines], [100, 100])
        self.assertTrue(all(line["quantity"] == len(line["serials"]) for line in lines))
        self.assertTrue(all(len(",".join(line["serials"])) <= 2000 for line in lines))

    def test_keeps_unbarcoded_accessory_as_quantity_line(self):
        result = inbound_result([
            {
                "product_code": "247296319",
                "description": "中央净水机面贴",
                "order_numbers": ["403565"],
                "serials": [],
                "expected_quantity": 10,
                "serial_count": 0,
                "unbarcoded_quantity": 10,
                "quantity_mismatch": False,
            }
        ])

        lines = build_gyj_purchase_lines(result)

        self.assertEqual(lines, [{
            "product_code": "247296319",
            "description": "中央净水机面贴",
            "source_order_numbers": ["403565"],
            "serials": [],
            "quantity": 10,
            "record_type": "无条码配件",
        }])

    def test_rejects_source_result_with_duplicate_serials(self):
        with self.assertRaisesRegex(GYJInboundError, "重复条码"):
            build_gyj_purchase_lines(inbound_result([], ["SN0001"]))

    def test_rejects_quantity_mismatch_before_any_gyj_operation(self):
        result = inbound_result([
            {
                "product_code": "926019528",
                "description": "GAC滤芯",
                "order_numbers": [],
                "serials": ["SN0001"],
                "expected_quantity": 2,
                "serial_count": 1,
                "unbarcoded_quantity": 0,
                "quantity_mismatch": True,
            }
        ])

        with self.assertRaisesRegex(GYJInboundError, "数量不一致"):
            build_gyj_purchase_lines(result)


if __name__ == "__main__":
    unittest.main()
