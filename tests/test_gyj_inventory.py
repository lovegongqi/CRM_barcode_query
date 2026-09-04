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

    def test_serial_parser_can_preserve_shipped_rows_without_price(self):
        headers = ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"]
        rows = [["SN-2", "10000398", "雷哲V200", "沈桥仓", "3110", "是"]]

        result = parse_serial_rows(headers, rows, include_shipped=True)

        self.assertEqual(result, [{
            "serial": "SN-2", "barcode": "10000398", "name": "雷哲V200",
            "warehouse": "沈桥仓", "shipped": True,
        }])
        self.assertNotIn("3110", repr(result))

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
    def __init__(self, reports, delayed_pagination=False):
        self.reports = reports
        self.delayed_pagination = delayed_pagination
        self.visited = []
        self.filled = []
        self.selected = []
        self.waits = []
        self.queries = 0
        self.expanded = 0
        self.current_url = None
        self.page_index = 0
        self.pending_page = False
        self.pending_phase = 0
        self.serial_state = None

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        self.current_url = url
        self.page_index = 0

    def fill_field(self, label, value):
        self.filled.append((label, value))

    def select_label(self, label, value):
        self.selected.append((label, value))
        if label == "已出库":
            self.serial_state = value

    def expand_filters(self):
        self.expanded += 1

    def click_query(self):
        self.queries += 1

    def read_table_page(self, _report):
        pages = self.reports.get(
            (self.current_url, self.serial_state), self.reports.get(self.current_url)
        )
        snapshot = dict(pages[self.page_index])
        snapshot.setdefault("page_number", self.page_index + 1)
        snapshot["loading"] = self.pending_page and self.pending_phase == 0
        return snapshot

    def next_page(self):
        if not self.delayed_pagination:
            self.page_index += 1
            return
        self.pending_page = True
        self.pending_phase = 0

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if not self.pending_page:
            return
        self.pending_phase += 1
        if self.pending_phase >= 2:
            self.page_index += 1
            self.pending_page = False


def stock_page(rows, total, has_next=False):
    return {
        "headers": ["条码", "名称", "规格", "型号", "类别", "单位", "成本价", "库存", "库存金额"],
        "rows": rows,
        "total": total,
        "has_next": has_next,
    }


def material_page(rows, total, has_next=False):
    return {
        "headers": ["条码", "名称", "规格", "型号", "类别", "单位", "状态"],
        "rows": rows,
        "total": total,
        "has_next": has_next,
    }


def serial_page(rows, total, has_next=False):
    return {
        "headers": ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"],
        "rows": rows,
        "total": total,
        "has_next": has_next,
    }


class GYJInventoryReaderTests(unittest.TestCase):
    def test_stock_search_waits_for_delayed_gyj_field_mount(self):
        class SearchField:
            def __init__(self, page):
                self.page = page

            def count(self):
                return int(self.page.elapsed_ms >= 300)

            def fill(self, value):
                self.page.filled = value

        class DelayedSearchPage:
            def __init__(self):
                self.elapsed_ms = 0
                self.filled = None

            def locator(self, _selector):
                return SearchField(self)

            def wait_for_timeout(self, milliseconds):
                self.elapsed_ms += milliseconds

        page = DelayedSearchPage()

        GYJInventoryReader(page)._fill_field(
            "请输入条码、名称、助记码、规格、型号等信息", "926019528"
        )

        self.assertEqual(page.filled, "926019528")
        self.assertGreaterEqual(page.elapsed_ms, 300)

    def test_query_waits_for_delayed_gyj_button_mount(self):
        class QueryButton:
            def __init__(self, page, available):
                self.page = page
                self.available = available

            def count(self):
                return int(self.available and self.page.elapsed_ms >= 300)

            def click(self):
                self.page.clicks += 1

        class DelayedQueryPage:
            def __init__(self):
                self.elapsed_ms = 0
                self.clicks = 0
                self.waits = []

            def get_by_role(self, role, name, exact=False):
                return QueryButton(self, name == "查 询")

            def wait_for_timeout(self, milliseconds):
                self.waits.append(milliseconds)
                self.elapsed_ms += milliseconds

        page = DelayedQueryPage()

        GYJInventoryReader(page)._click_query()

        self.assertEqual(page.clicks, 1)
        self.assertGreaterEqual(page.elapsed_ms, 300)

    def test_total_count_uses_report_total_after_visible_row_range(self):
        self.assertEqual(GYJInventoryReader._total_count("1-10 共527条"), 527)

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
        self.assertFalse(any(label == "仓库" for label, _value in page.filled))
        self.assertFalse(any(label == "仓库" for label, _value in page.selected))

    def test_read_total_stock_rejects_any_row_for_a_different_barcode(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [stock_page([
                ["B2", "错误商品", "", "", "配件", "个", "9", "7", "63"],
            ], 1)],
        })

        with self.assertRaisesRegex(GYJInventoryReadError, "查询条件"):
            GYJInventoryReader(page).read_total_stock("A1")

    def test_read_total_stock_keeps_a_legitimate_empty_result_as_zero(self):
        page = FakeInventoryPage({GYJ_STOCK_URL: [stock_page([], 0)]})
        self.assertEqual(GYJInventoryReader(page).read_total_stock("A1"), "0")

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

    def test_rejects_reordered_later_headers_without_leaking_price_data(self):
        second = stock_page(
            [["A1", "甲", "", "", "", "个", "3", "999", "2997"]], 2
        )
        second["headers"] = [
            "条码", "名称", "规格", "型号", "类别", "单位", "库存", "成本价", "库存金额",
        ]
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [
                stock_page([["A1", "甲", "", "", "", "个", "998", "1", "998"]], 2, True),
                second,
            ]
        })

        with self.assertRaises(GYJInventoryReadError) as caught:
            GYJInventoryReader(page).read_stock_totals()

        message = repr(caught.exception)
        self.assertIn("表头", message)
        self.assertNotIn("成本价", message)
        self.assertNotIn("库存金额", message)
        self.assertNotIn("999", message)
        self.assertNotIn("2997", message)

    def test_material_rows_require_headers_name_and_boolean_badge_observation(self):
        cases = []
        missing_header = material_page(
            [["A1", "甲", "", "", "", "个", "启用"]], 1
        )
        missing_header["headers"] = ["条码", "规格", "型号", "类别", "单位", "状态"]
        missing_header["serial_badges"] = [False]
        cases.append(missing_header)
        cases.append(material_page([
            {"barcode": "A1", "name": "", "serial_badge": False, "enabled": True},
        ], 1))
        cases.append(material_page([
            {"barcode": "A1", "name": "序列商品 序", "enabled": True},
        ], 1))
        non_boolean = material_page(
            [["A1", "序列商品 序", "", "", "", "个", "启用"]], 1
        )
        non_boolean["serial_badges"] = [None]
        cases.append(non_boolean)

        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                page = FakeInventoryPage({
                    GYJ_MATERIAL_URL: [snapshot],
                    GYJ_STOCK_URL: [stock_page([], 0)],
                })
                with self.assertRaises(GYJInventoryReadError):
                    GYJInventoryReader(page).load_catalog()

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
        self.assertFalse(any(label == "仓库" for label, _value in page.filled))
        self.assertFalse(any(label == "仓库" for label, _value in page.selected))
        self.assertNotIn("10", repr(result))

    def test_waits_for_loading_and_stale_rows_before_collecting_next_page(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [
                stock_page([["A1", "甲", "", "", "", "个", "9", "1", "9"]], 2, True),
                stock_page([["B2", "乙", "", "", "", "个", "8", "2", "16"]], 2),
            ]
        }, delayed_pagination=True)

        result = GYJInventoryReader(page).read_stock_totals()

        self.assertEqual(result, {"A1": "1", "B2": "2"})
        self.assertGreaterEqual(len(page.waits), 2)

    def test_lookup_serial_filters_by_serial_and_returns_none_or_one_row(self):
        page = FakeInventoryPage({
            (GYJ_SERIAL_URL, "否"): [
                serial_page([["SN-1", "A1", "甲", "一仓", "10", "否"]], 1)
            ],
            (GYJ_SERIAL_URL, "是"): [serial_page([], 0)],
        })
        reader = GYJInventoryReader(page)

        self.assertEqual(reader.lookup_serial("SN-1")["barcode"], "A1")
        self.assertIn(("序列号", "SN-1"), page.filled)

        empty = FakeInventoryPage({
            (GYJ_SERIAL_URL, "否"): [serial_page([], 0)],
            (GYJ_SERIAL_URL, "是"): [serial_page([], 0)],
        })
        self.assertIsNone(GYJInventoryReader(empty).lookup_serial("MISSING"))

    def test_lookup_serial_queries_both_states_and_preserves_shipped_row(self):
        page = FakeInventoryPage({
            (GYJ_SERIAL_URL, "否"): [serial_page([], 0)],
            (GYJ_SERIAL_URL, "是"): [
                serial_page([], 1, True),
                serial_page([["SHIPPED-1", "B2", "序列商品", "其他仓", "999", "是"]], 1),
            ],
        })

        result = GYJInventoryReader(page).lookup_serial("SHIPPED-1")

        self.assertEqual(result, {
            "serial": "SHIPPED-1", "barcode": "B2", "name": "序列商品",
            "warehouse": "其他仓", "shipped": True,
        })
        self.assertEqual(page.selected, [("已出库", "否"), ("已出库", "是")])
        self.assertEqual(page.filled, [("序列号", "SHIPPED-1"), ("序列号", "SHIPPED-1")])
        self.assertEqual(page.queries, 2)
        self.assertFalse(any(label == "仓库" for label, _value in page.filled))
        self.assertFalse(any(label == "仓库" for label, _value in page.selected))
        self.assertNotIn("999", repr(result))

    def test_lookup_serial_rejects_non_exact_filtered_rows(self):
        page = FakeInventoryPage({
            (GYJ_SERIAL_URL, "否"): [
                serial_page([["OTHER-1", "B2", "序列商品", "沈桥仓", "10", "是"]], 1)
            ],
            (GYJ_SERIAL_URL, "是"): [serial_page([], 0)],
        })

        with self.assertRaisesRegex(GYJInventoryReadError, "查询条件"):
            GYJInventoryReader(page).lookup_serial("SHIPPED-1")

    def test_rejects_pagination_total_mismatch(self):
        page = FakeInventoryPage({
            GYJ_STOCK_URL: [stock_page([["A1", "甲", "", "", "", "个", "1", "1", "1"]], 2)]
        })

        with self.assertRaisesRegex(GYJInventoryReadError, "分页总数"):
            GYJInventoryReader(page).read_stock_totals()
