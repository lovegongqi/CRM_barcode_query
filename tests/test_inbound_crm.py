import unittest

from playwright.sync_api import sync_playwright

from inbound_crm import PackingSlipCRMReader, PackingSlipReadError, map_shipment_rows, map_table_rows


class ScriptedReader(PackingSlipCRMReader):
    def __init__(self, pages, total_pages=None, progress=None):
        super().__init__(session=None, progress=progress)
        self.pages = pages
        self.position = 1
        self.visited = []
        self.visible_buttons = [1, 3, 5, 7]
        self.scripted_total_pages = total_pages or len(pages)

    def _go_to_first_page(self):
        self.position = 1

    def _current_page_number(self):
        return self.position

    def _total_pages(self):
        return self.scripted_total_pages

    def _read_current_page(self):
        self.visited.append(self.position)
        return self.pages[self.position - 1]

    def _has_next_page(self):
        return self.position < len(self.pages)

    def _advance_to_page(self, expected_page):
        self.position = expected_page

    def _wait_for_page(self, expected_page, previous_fingerprint):
        return None


def detail_row(page, serial=None):
    return {
        "page": page,
        "row_index": 1,
        "order_number": "210524",
        "product_code": "916000024",
        "description": "中央净水机",
        "expected_quantity": "8",
        "serial": serial or f"SN{page:08d}",
    }


class InboundCRMTest(unittest.TestCase):
    def test_maps_shipment_details_with_unbarcoded_accessories(self):
        rows = map_shipment_rows(
            ["erp发货单号", "产品名称", "产品编码", "发货数量", "已发数量"],
            [
                ["403565", "中央净水机面贴", "247296319", "10", "10"],
                ["403565", "机头马达组件", "457384691", "1", "1"],
            ],
        )

        self.assertEqual(
            rows,
            [
                {"order_number": "403565", "description": "中央净水机面贴", "product_code": "247296319", "expected_quantity": "10"},
                {"order_number": "403565", "description": "机头马达组件", "product_code": "457384691", "expected_quantity": "1"},
            ],
        )

    def test_recognizes_element_pagination_next_and_total_pages(self):
        class Element:
            def __init__(self, text="", class_name="", value="", disabled=None):
                self.text = text
                self.class_name = class_name
                self.value = value
                self.disabled = disabled

            def is_visible(self):
                return True

            def is_enabled(self):
                return not self.disabled

            def get_attribute(self, name):
                return {"class": self.class_name, "value": self.value}.get(name)

            def inner_text(self):
                return self.text

        class Scope:
            def query_selector_all(self, selector):
                if "li.number.active" in selector:
                    return [Element(text="1", class_name="number active")]
                if "button.btn-next" in selector:
                    return [Element(class_name="btn-next")]
                if ".el-pagination input" in selector:
                    return [Element(value="20条/页")]
                return []

            def inner_text(self, selector):
                return "出库明细 共 388 条 20条/页"

        class Session:
            page = Scope()
            context = None

        reader = PackingSlipCRMReader(Session())

        self.assertEqual(reader._current_page_number(), 1)
        self.assertEqual(reader._total_pages(), 20)
        self.assertTrue(reader._has_next_page())

    def test_selects_fifty_rows_per_page_before_reading(self):
        class PageSizeInput:
            def __init__(self, scope):
                self.scope = scope
                self.value = "20条/页"

            def is_visible(self):
                return True

            def get_attribute(self, name):
                return self.value if name == "value" else ""

            def click(self):
                self.scope.menu_open = True

        class PageSizeOption:
            def __init__(self, field):
                self.field = field

            def is_visible(self):
                return True

            def inner_text(self):
                return "50条/页"

            def click(self):
                self.field.value = "50条/页"

        class Scope:
            def __init__(self):
                self.menu_open = False
                self.field = PageSizeInput(self)

            def query_selector_all(self, selector):
                if ".el-pagination .el-select input" in selector:
                    return [self.field]
                if "el-select-dropdown__item" in selector and self.menu_open:
                    return [PageSizeOption(self.field)]
                return []

        class Session:
            def __init__(self):
                self.page = Scope()
                self.context = None

        reader = PackingSlipCRMReader(Session())

        self.assertTrue(reader._set_outbound_page_size(50))
        self.assertEqual(reader.session.page.field.value, "50条/页")

    def test_selects_fifty_rows_from_element_ui_sizes_control_with_numeric_value(self):
        class PageSizeInput:
            def __init__(self, scope):
                self.scope = scope
                self.value = "20"

            def is_visible(self):
                return True

            def get_attribute(self, name):
                return ""

            def input_value(self):
                return self.value

            def click(self):
                self.scope.menu_open = True

        class PageSizeOption:
            def __init__(self, field):
                self.field = field

            def is_visible(self):
                return True

            def inner_text(self):
                return "50 条/页"

            def click(self):
                self.field.value = "50"

        class Scope:
            def __init__(self):
                self.menu_open = False
                self.field = PageSizeInput(self)

            def query_selector_all(self, selector):
                if ".el-pagination__sizes input" in selector:
                    return [self.field]
                if "el-select-dropdown__item" in selector and self.menu_open:
                    return [PageSizeOption(self.field)]
                return []

        class Session:
            def __init__(self):
                self.page = Scope()
                self.context = None

        reader = PackingSlipCRMReader(Session())

        self.assertTrue(reader._set_outbound_page_size(50))
        self.assertEqual(reader.session.page.field.value, "50")

    def test_waits_for_first_page_to_render_selected_page_size(self):
        class DelayedPageSizeReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)
                self.reads = 0
                self.pauses = 0

            def _outbound_item_count(self):
                return 388

            def _section_table_values(self, title):
                self.reads += 1
                return ["产品编码", "产品条码"], [["x", "y"]] * (20 if self.reads < 3 else 50)

            def _pause(self, milliseconds=300):
                self.pauses += 1

        reader = DelayedPageSizeReader()

        self.assertTrue(reader._wait_for_outbound_page_size_render(50))
        self.assertEqual(reader.pauses, 2)

    def test_reads_headers_and_rows_from_separate_detail_tables(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<div class='rt-section'><div class='rt-section__header'>出库明细</div>"
                    "<table><thead><tr><th>产品名称</th><th>产品编码</th><th>产品条码</th></tr></thead></table>"
                    "<table><tbody><tr><td>净水机</td><td>906042856</td><td>9462607180448</td></tr></tbody></table>"
                    "</div>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                headers, rows = PackingSlipCRMReader(Session())._section_table_values("出库明细")

                self.assertEqual(headers, ["产品名称", "产品编码", "产品条码"])
                self.assertEqual(rows, [["净水机", "906042856", "9462607180448"]])
            finally:
                browser.close()

    def test_waits_for_detail_section_to_finish_rendering(self):
        class DelayedDetailReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)
                self.reads = 0
                self.pauses = 0

            def _section_table_values(self, title):
                self.reads += 1
                if self.reads < 3:
                    return [], []
                return ["产品编码", "发货数量"], [["247296319", "10"]]

            def _pause(self, milliseconds=300):
                self.pauses += 1

        reader = DelayedDetailReader()

        self.assertTrue(reader._wait_for_detail_section("发货明细", ("产品编码", "发货数量")))
        self.assertEqual(reader.pauses, 2)

    def test_finds_element_ui_input_when_label_is_not_html_label(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<div class='el-form-item'>"
                    "<div class='el-form-item__label'>装箱单号</div>"
                    "<div class='el-form-item__content'><input class='el-input__inner'></div>"
                    "</div>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                field = PackingSlipCRMReader(Session())._find_labeled_input("装箱单号")

                self.assertIsNotNone(field)
                field.fill("SH202607210002")
                self.assertEqual(page.locator("input").input_value(), "SH202607210002")
            finally:
                browser.close()

    def test_finds_packing_slip_quick_search_input(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<div class='rt-grid'>"
                    "<div class='rt-grid__header'><div class='el-input el-input-group'>"
                    "<input class='el-input__inner' placeholder='快速搜索'>"
                    "</div></div>"
                    "<table><thead><tr><th>装箱单号</th></tr></thead></table>"
                    "</div>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                field = PackingSlipCRMReader(Session())._find_labeled_input("装箱单号")

                self.assertIsNotNone(field)
                field.fill("SH202607210002")
                self.assertEqual(page.locator("input").input_value(), "SH202607210002")
            finally:
                browser.close()

    def test_waits_for_search_input_after_menu_navigation(self):
        class Field:
            def __init__(self):
                self.value = ""

            def fill(self, value):
                self.value = value

        class DelayedReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)
                self.field = Field()
                self.lookup_count = 0

            def _find_labeled_input(self, label):
                self.lookup_count += 1
                return self.field if self.lookup_count == 3 else None

            def _click_exact_text(self, text):
                return text == "查询"

            def _open_matching_result(self, packing_slip_no):
                return self.field.value == packing_slip_no

            def _pause(self, milliseconds=300):
                return None

        reader = DelayedReader()

        try:
            reader._search_and_open("SH202607210002")
        except PackingSlipReadError:
            pass

        self.assertEqual(reader.lookup_count, 3)
        self.assertEqual(reader.field.value, "SH202607210002")

    def test_uses_enter_for_quick_search_when_the_list_has_no_text_button(self):
        class Field:
            def __init__(self):
                self.value = ""
                self.key = ""

            def fill(self, value):
                self.value = value

            def press(self, key):
                self.key = key

        class Reader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)
                self.field = Field()

            def _find_labeled_input(self, label):
                return self.field

            def _click_exact_text(self, text):
                return False

            def _open_matching_result(self, packing_slip_no):
                return self.field.value == packing_slip_no and self.field.key == "Enter"

            def _pause(self, milliseconds=300):
                return None

        reader = Reader()

        reader._search_and_open("SH202607210002")

        self.assertEqual(reader.field.key, "Enter")

    def test_reports_visible_field_metadata_without_reading_values(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<div class='el-form-item'>"
                    "<div class='el-form-item__label'>装箱单编号</div>"
                    "<input class='el-input__inner' placeholder='请输入编号' name='packingNo' value='secret'>"
                    "</div>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                diagnostics = PackingSlipCRMReader(Session())._visible_field_diagnostics()

                self.assertIn("装箱单编号", diagnostics)
                self.assertIn("请输入编号", diagnostics)
                self.assertIn("packingNo", diagnostics)
                self.assertNotIn("secret", diagnostics)
            finally:
                browser.close()

    def test_opens_matching_packing_slip_already_visible_in_list(self):
        class ListedReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)
                self.opened = []

            def _open_matching_result(self, packing_slip_no):
                self.opened.append(packing_slip_no)
                return True

            def _find_labeled_input(self, label):
                raise AssertionError("不应在已有精确结果时查找筛选输入框")

            def _pause(self, milliseconds=300):
                return None

        reader = ListedReader()

        reader._search_and_open("SH202607210002")

        self.assertEqual(reader.opened, ["SH202607210002"])

    def test_opens_detail_by_clicking_the_exact_packing_slip_link(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<table><thead><tr><th>装箱单号</th><th>经销商</th><th>装箱单类型</th></tr></thead>"
                    "<tbody><tr><td style='width: 400px' "
                    "onclick=\"document.querySelector('#opened').textContent='cell'\">"
                    "<a href='#detail' onclick=\"document.querySelector('#opened').textContent='link'; event.stopPropagation()\">"
                    "SH202607210002</a></td><td>江西省天麓工贸有限公司</td><td>销售订单</td></tr></tbody></table>"
                    "<span id='opened'></span>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                opened = PackingSlipCRMReader(Session())._open_matching_result("SH202607210002")

                self.assertTrue(opened)
                self.assertEqual(page.locator("#opened").text_content(), "link")
            finally:
                browser.close()

    def test_captures_packing_slip_type_from_the_matching_list_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(
                    "<div class='el-table__header-wrapper'><table class='el-table__header'><thead><tr>"
                    "<th>装箱单号</th><th>经销商</th><th>创建时间</th><th>单据状态</th><th>装箱单类型</th>"
                    "</tr></thead></table></div>"
                    "<div class='el-table__body-wrapper'><table class='el-table__body'><tbody><tr>"
                    "<td><a href='#detail'>SH202607210002</a></td>"
                    "<td>江西省天麓工贸有限公司</td><td>2026-07-21</td><td>已签收</td><td>销售订单</td>"
                    "</tr></tbody></table>"
                    "</div>"
                )

                class Session:
                    context = None

                    def __init__(self):
                        self.page = page

                reader = PackingSlipCRMReader(Session())

                opened = reader._open_matching_result("SH202607210002")

                self.assertTrue(opened)
                self.assertEqual(reader.packing_slip_type, "销售订单")
            finally:
                browser.close()

    def test_maps_shuffled_headers_and_filters_noise_rows(self):
        headers = ["条码", "物料描述", "订单号", "应发数量", "物料编码"]
        rows = [
            ["SN00000001", "中央净水机", "210524", "1", "916000024"],
            headers,
            ["", "合计", "", "1", ""],
            ["", "", "", "", ""],
        ]

        mapped = map_table_rows(headers, rows, page_number=3)

        self.assertEqual(
            mapped,
            [
                {
                    "page": 3,
                    "row_index": 1,
                    "order_number": "210524",
                    "product_code": "916000024",
                    "description": "中央净水机",
                    "expected_quantity": "1",
                    "serial": "SN00000001",
                }
            ],
        )

    def test_requires_product_code_and_serial_headers(self):
        with self.assertRaisesRegex(PackingSlipReadError, "物料编码.*条码"):
            map_table_rows(["订单号", "物料描述"], [["210524", "中央净水机"]], 1)

    def test_reads_all_eight_pages_in_strict_consecutive_order(self):
        updates = []
        reader = ScriptedReader(
            [[detail_row(page)] for page in range(1, 9)],
            progress=updates.append,
        )

        result = reader._read_all_pages()

        self.assertEqual(reader.visited, [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(
            [entry["page"] for entry in result["page_counts"]],
            [1, 2, 3, 4, 5, 6, 7, 8],
        )
        self.assertEqual([row["page"] for row in result["rows"]], list(range(1, 9)))
        self.assertEqual(updates[-1], {"page": 8, "row_count": 1})

    def test_rejects_page_jump(self):
        class JumpingReader(ScriptedReader):
            def _advance_to_page(self, expected_page):
                self.position = 3

        reader = JumpingReader([[detail_row(1)], [detail_row(2)], [detail_row(3)]])

        with self.assertRaisesRegex(PackingSlipReadError, "期望第 2 页，实际第 3 页"):
            reader._read_all_pages()

    def test_rejects_repeated_page_content(self):
        repeated = detail_row(1, serial="SN00000001")
        reader = ScriptedReader([[repeated], [{**repeated, "page": 2}]])

        with self.assertRaisesRegex(PackingSlipReadError, "页面重复"):
            reader._read_all_pages()

    def test_rejects_unidentifiable_current_page(self):
        class UnknownPageReader(ScriptedReader):
            def _current_page_number(self):
                return None

        reader = UnknownPageReader([[detail_row(1)]])

        with self.assertRaisesRegex(PackingSlipReadError, "无法识别当前页码"):
            reader._read_all_pages()

    def test_does_not_fall_back_when_aria_current_page_is_ambiguous(self):
        class PageElement:
            def __init__(self, number):
                self.number = str(number)

            def is_visible(self):
                return True

            def get_attribute(self, name):
                return None

            def inner_text(self):
                return self.number

        class Scope:
            def query_selector_all(self, selector):
                if selector == "[aria-current='page']":
                    return [PageElement(1), PageElement(2)]
                if ".active" in selector:
                    return [PageElement(1)]
                return []

        class Session:
            page = Scope()
            context = None

        reader = PackingSlipCRMReader(Session())

        self.assertIsNone(reader._current_page_number())

    def test_rejects_next_page_becoming_unavailable_before_total(self):
        class PrematureEndReader(ScriptedReader):
            def _has_next_page(self):
                return self.position < 7

        reader = PrematureEndReader(
            [[detail_row(page)] for page in range(1, 9)],
            total_pages=8,
        )

        with self.assertRaisesRegex(PackingSlipReadError, "未读完总页数"):
            reader._read_all_pages()

    def test_rejects_unknown_total_when_next_control_state_cannot_be_read(self):
        class DetachedControl:
            def get_attribute(self, name):
                raise RuntimeError("control was detached")

        class UnknownTotalReader(PackingSlipCRMReader):
            def __init__(self):
                super().__init__(session=None)

            def _go_to_first_page(self):
                return None

            def _current_page_number(self):
                return 1

            def _total_pages(self):
                return None

            def _read_current_page(self):
                return [detail_row(1)]

            def _next_controls(self):
                return [DetachedControl()]

        reader = UnknownTotalReader()

        with self.assertRaisesRegex(PackingSlipReadError, "无法识别下一页状态"):
            reader._read_all_pages()


if __name__ == "__main__":
    unittest.main()
