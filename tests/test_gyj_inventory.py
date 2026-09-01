import unittest

from gyj_inventory import (
    GYJInventoryReadError,
    GYJInventoryReader,
    GYJ_MATERIAL_URL,
    GYJ_SERIAL_URL,
    GYJ_STOCK_URL,
    parse_material_rows,
    parse_serial_rows,
    parse_stock_rows,
)


class GYJInventoryParserTests(unittest.TestCase):
    def test_stock_parser_allowlists_fields_and_drops_prices(self):
        headers = ["条码", "名称", "规格", "型号", "类别", "单位", "成本价", "库存", "库存金额"]
        rows = [["996041496", "ERH-X10 售后机箱", "", "", "配件", "个", "100", "2", "200"]]

        result = parse_stock_rows(headers, rows)

        self.assertEqual(result, [{
            "barcode": "996041496", "name": "ERH-X10 售后机箱", "spec": "", "model": "",
            "category": "配件", "unit": "个", "stock": "2",
        }])
        self.assertNotIn("成本价", repr(result))
        self.assertNotIn("200", repr(result))

    def test_material_parser_uses_explicit_serial_badge(self):
        result = parse_material_rows([
            {"barcode": "996041496", "name": "ERH-X10 售后机箱", "serial_badge": True},
            {"barcode": "406005140", "name": "电源24VDC4A", "serial_badge": False},
        ])

        self.assertTrue(result["996041496"]["has_serial"])
        self.assertFalse(result["406005140"]["has_serial"])

    def test_serial_parser_keeps_only_unshipped_rows_without_price(self):
        headers = ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"]
        rows = [
            ["SN-1", "10000398", "雷哲V200", "沈桥仓", "3110", "否"],
            ["SN-2", "10000398", "雷哲V200", "沈桥仓", "3110", "是"],
        ]

        self.assertEqual(parse_serial_rows(headers, rows), [{
            "serial": "SN-1", "barcode": "10000398", "name": "雷哲V200",
            "warehouse": "沈桥仓", "shipped": False,
        }])

    def test_stock_parser_normalizes_whitespace_in_headers(self):
        result = parse_stock_rows(
            [" 条 \n 码 ", "名 称", "规格", "型号", "类别", "单位", "库 \t 存"],
            [["A1", "甲", "", "", "", "个", "1.2300"]],
        )

        self.assertEqual(result[0]["barcode"], "A1")
        self.assertEqual(result[0]["stock"], "1.23")

    def test_parsers_reject_missing_required_columns(self):
        with self.assertRaisesRegex(GYJInventoryReadError, "库存报表缺少"):
            parse_stock_rows(["条码", "名称"], [["A1", "甲"]])
        with self.assertRaisesRegex(GYJInventoryReadError, "序列号报表缺少"):
            parse_serial_rows(["序列号", "条码"], [["SN-1", "A1"]])


class FakeInventoryPage:
    def __init__(self, reports):
        self.reports = reports
        self.visited = []
        self.filled = []
        self.selected = []
        self.queries = 0
        self.expanded = 0
        self.current_url = None
        self.page_index = 0

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        self.current_url = url
        self.page_index = 0

    def fill_field(self, label, value):
        self.filled.append((label, value))

    def select_label(self, label, value):
        self.selected.append((label, value))

    def expand_filters(self):
        self.expanded += 1

    def click_query(self):
        self.queries += 1

    def read_table_page(self, _report):
        return self.reports[self.current_url][self.page_index]

    def next_page(self):
        self.page_index += 1


def stock_page(rows, total, has_next=False):
    return {
        "headers": ["条码", "名称", "规格", "型号", "类别", "单位", "成本价", "库存", "库存金额"],
        "rows": rows,
        "total": total,
        "has_next": has_next,
    }


def material_page(rows, total, has_next=False):
    return {"rows": rows, "total": total, "has_next": has_next}


def serial_page(rows, total, has_next=False):
    return {
        "headers": ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"],
        "rows": rows,
        "total": total,
        "has_next": has_next,
    }


class GYJInventoryReaderTests(unittest.TestCase):
    def test_read_total_stock_combines_pages_exactly_without_selecting_warehouse(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [
                stock_page([["A1", "测试商品", "", "", "配件", "个", "99", "10000000000000000000000000000.1", "999"]], 2, True),
                stock_page([["A1", "测试商品", "", "", "配件", "个", "99", "0.2", "999"]], 2),
            ]
        })

        result = GYJInventoryReader(page).read_total_stock("A1")

        self.assertEqual(result, "10000000000000000000000000000.3")
        self.assertEqual(page.visited, [GYJ_STOCK_URL])
        self.assertEqual(page.filled, [("请输入条码、名称、助记码、规格、型号等信息", "A1")])
        self.assertFalse(any(label == "仓库" for label, _value in page.selected))

    def test_read_stock_totals_aggregates_all_pages_without_focused_filter(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [
                stock_page([["A1", "甲", "", "", "", "个", "1", "1.25", "1.25"]], 2, True),
                stock_page([["B2", "乙", "", "", "", "个", "2", "2.500", "5"]], 2),
            ]
        })

        result = GYJInventoryReader(page).read_stock_totals()

        self.assertEqual(result, {"A1": "1.25", "B2": "2.5"})
        self.assertEqual(page.filled, [])
        self.assertEqual(page.selected, [])

    def test_load_catalog_uses_all_enabled_products_and_marks_orphan_stock(self):
        page = FakeInventoryPage({
            GYJ_MATERIAL_URL: [material_page([
                {"barcode": "A1", "name": "有库存", "unit": "个", "serial_badge": False, "enabled": True},
                {"barcode": "B2", "name": "零库存序列商品", "unit": "台", "serial_badge": True, "enabled": True},
                {"barcode": "OFF", "name": "停用", "serial_badge": False, "enabled": False},
            ], 3)],
            GYJ_STOCK_URL: [stock_page([
                ["A1", "有库存", "", "", "", "个", "9", "3", "27"],
                ["ORPHAN", "无商品信息", "", "", "", "个", "8", "1", "8"],
            ], 2)],
        })

        result = GYJInventoryReader(page).load_catalog()

        self.assertEqual(result[:2], [
            {"barcode": "A1", "name": "有库存", "spec": "", "model": "", "category": "", "unit": "个", "has_serial": False, "initial_stock": "3"},
            {"barcode": "B2", "name": "零库存序列商品", "spec": "", "model": "", "category": "", "unit": "台", "has_serial": True, "initial_stock": "0"},
        ])
        self.assertEqual(result[2]["barcode"], "ORPHAN")
        self.assertEqual(result[2]["data_error"], "无法确认商品序列号设置")
        self.assertNotIn("has_serial", result[2])
        self.assertNotIn("OFF", repr(result))
        self.assertNotIn("27", repr(result))

    def test_material_name_only_removes_a_confirmed_serial_badge(self):
        page = FakeInventoryPage({
            GYJ_MATERIAL_URL: [{
                "headers": ["条码", "名称", "规格", "型号", "类别", "单位", "状态"],
                "rows": [
                    ["A1", "工序", "", "", "", "个", "启用"],
                    ["B2", "序列商品 序", "", "", "", "台", "启用"],
                ],
                "serial_badges": [False, True],
                "total": 2,
                "has_next": False,
            }],
            GYJ_STOCK_URL: [stock_page([], 0)],
        })

        result = GYJInventoryReader(page).load_catalog()

        self.assertEqual([item["name"] for item in result], ["工序", "序列商品"])

    def test_read_unshipped_serials_sets_no_and_combines_pages(self):
        page = FakeInventoryPage({
            GYJ_SERIAL_URL: [
                serial_page([["SN-1", "A1", "甲", "一仓", "10", "否"]], 2, True),
                serial_page([["SN-2", "A1", "甲", "二仓", "10", "否"]], 2),
            ]
        })

        result = GYJInventoryReader(page).read_unshipped_serials("A1")

        self.assertEqual([row["serial"] for row in result], ["SN-1", "SN-2"])
        self.assertEqual(page.visited, [GYJ_SERIAL_URL])
        self.assertEqual(page.expanded, 1)
        self.assertIn(("商品", "A1"), page.filled)
        self.assertIn(("已出库", "否"), page.selected)
        self.assertFalse(any(label == "仓库" for label, _value in page.selected))
        self.assertNotIn("10", repr(result))

    def test_lookup_serial_filters_by_serial_and_returns_none_or_one_row(self):
        page = FakeInventoryPage({
            GYJ_SERIAL_URL: [serial_page([["SN-1", "A1", "甲", "一仓", "10", "否"]], 1)]
        })
        reader = GYJInventoryReader(page)

        self.assertEqual(reader.lookup_serial("SN-1")["barcode"], "A1")
        self.assertIn(("序列号", "SN-1"), page.filled)

        empty = FakeInventoryPage({GYJ_SERIAL_URL: [serial_page([], 0)]})
        self.assertIsNone(GYJInventoryReader(empty).lookup_serial("MISSING"))

    def test_rejects_pagination_total_mismatch(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [stock_page([["A1", "甲", "", "", "", "个", "1", "1", "1"]], 2)]
        })

        with self.assertRaisesRegex(GYJInventoryReadError, "分页总数"):
            GYJInventoryReader(page).read_stock_totals()
