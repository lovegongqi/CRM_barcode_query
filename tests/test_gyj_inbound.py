import unittest

from gyj_inbound import (
    GYJInboundError,
    GYJPurchaseInboundWriter,
    build_gyj_purchase_lines,
)


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


class FakeGYJPage:
    def __init__(self, product_found=True):
        self.product_found = product_found
        self.clicked = []
        self.headers = {}
        self.remark = ""
        self.lines = []

    def open_new_form(self):
        self.clicked.append("新增")

    def select_header(self, label, value):
        self.headers[label] = value

    def fill_remark(self, value):
        self.remark = value

    def add_product_line(self, line):
        if not self.product_found:
            raise GYJInboundError(f"未找到物料编码：{line['product_code']}")
        self.lines.append(dict(line))

    def verify_form(self, packing_slip_no, lines):
        self.checked = (packing_slip_no, list(lines))

    def click_plain_save(self):
        self.clicked.append("保存")
        return "CG202608130001"


class GYJInboundWriterTest(unittest.TestCase):
    def setUp(self):
        self.lines = [{
            "product_code": "926019528",
            "description": "滤芯",
            "source_order_numbers": ["403565"],
            "serials": ["SN001"],
            "quantity": 1,
            "record_type": "条码",
        }]

    def test_writer_uses_only_plain_save_after_verification(self):
        page = FakeGYJPage()

        result = GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertEqual(page.clicked, ["新增", "保存"])
        self.assertNotIn("保存并审核", page.clicked)
        self.assertEqual(page.headers, {
            "供应商": "昆山怡口净水系统有限公司",
            "结算账户": "江西天麓",
            "仓库": "沈桥仓",
        })
        self.assertEqual(page.remark, "装箱单号：SH202607210002")
        self.assertEqual(result["order_no"], "CG202608130001")

    def test_writer_stops_before_save_when_product_lookup_fails(self):
        page = FakeGYJPage(product_found=False)

        with self.assertRaisesRegex(GYJInboundError, "未找到物料编码"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertNotIn("保存", page.clicked)


if __name__ == "__main__":
    unittest.main()
