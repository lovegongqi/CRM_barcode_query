import unittest

from gyj_inbound import (
    GYJInboundError,
    GYJPlaywrightPage,
    GYJPurchaseInboundWriter,
    GYJ_SUPPLIER,
    build_gyj_purchase_lines,
)


def inbound_result(items, duplicate_serials=None):
    return {
        "packing_slip_no": "SH202607210002",
        "items": items,
        "duplicate_serials": duplicate_serials or [],
    }


class GYJInboundLineTest(unittest.TestCase):
    def test_supplier_uses_the_visible_gyj_option_label(self):
        self.assertEqual(GYJ_SUPPLIER, "昆山怡口净水")

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


class _DelayedNewButton:
    def __init__(self):
        self.ready = False
        self.clicked = False
        self.last = self

    def count(self):
        return 1 if self.ready else 0

    def wait_for(self, state, timeout):
        self.ready = True

    def click(self):
        if not self.ready:
            raise AssertionError("新增按钮尚未渲染")
        self.clicked = True


class _VisibleForm:
    def __init__(self):
        self.last = self

    def count(self):
        return 1


class _PurchaseInboundListPage:
    def __init__(self):
        self.new_button = _DelayedNewButton()
        self.modal = _VisibleForm()
        self.goto_url = ""

    def goto(self, url, **kwargs):
        self.goto_url = url

    def locator(self, selector):
        if selector == ".table-operator button.ant-btn-primary":
            return self.new_button
        if selector == ".ant-modal:visible":
            return self.modal
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_text(self, text, exact):
        raise AssertionError("新增按钮必须等待采购入库工具栏渲染后按 CSS 定位")


class _SaveButton:
    def __init__(self):
        self.clicked = False

    def count(self):
        return 1

    def click(self):
        self.clicked = True


class _MissingButton:
    def count(self):
        return 0


class _ActualGYJSaveForm:
    def __init__(self):
        self.save_button = _SaveButton()

    def get_by_text(self, text, exact):
        if text == "保存（Ctrl+S）" and exact:
            return self.save_button
        return _MissingButton()


class _ActualGYJSavePage:
    def wait_for_timeout(self, timeout):
        self.timeout = timeout

    def locator(self, selector):
        if selector not in ("body", "[disabled]"):
            raise AssertionError(f"unexpected selector: {selector}")
        return self

    def inner_text(self):
        return "保存成功"


class _RowCollection:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class _ActualInboundRowsForm:
    def __init__(self):
        self.header = object()
        self.entry = object()
        self.summary = object()

    def locator(self, selector):
        if selector == ".tr":
            return _RowCollection([self.header, self.entry, self.summary])
        if selector == "tr":
            return _RowCollection([])
        raise AssertionError(f"unexpected selector: {selector}")


class _QuantityInput:
    def __init__(self):
        self.value = ""
        self.key = ""
        self.waited = None

    def count(self):
        return 1

    def fill(self, value):
        self.value = value

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)

    def press(self, key):
        self.key = key


class _ActualInboundRow:
    def __init__(self):
        self.quantity_input = _QuantityInput()

    def locator(self, selector):
        if selector == 'input[id^="operNumber_"]':
            return self.quantity_input
        raise AssertionError(f"unexpected selector: {selector}")


class _ActionButton:
    def __init__(self):
        self.clicked = False
        self.first = self

    def count(self):
        return 1

    def click(self):
        self.clicked = True


class _ProductSearchInput:
    def __init__(self):
        self.value = ""
        self.first = self

    def count(self):
        return 1

    def filter(self, **kwargs):
        return self

    def fill(self, value):
        self.value = value


class _NoProductRows:
    def __init__(self):
        self.first = self

    def count(self):
        return 0

    def filter(self, **kwargs):
        return self


class _ProductSearchModal:
    def __init__(self):
        self.search = _ProductSearchInput()
        self.query = _ActionButton()
        self.rows = _NoProductRows()

    def locator(self, selector):
        if selector == "input":
            return self.search
        if selector == "tr":
            return self.rows
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_text(self, text, exact):
        if text in ("查 询", "查询") and exact:
            return self.query
        return _MissingButton()


class _ProductSelectionRow:
    def __init__(self):
        self.button = _ActionButton()

    def locator(self, selector):
        if selector == "button.ant-btn.ant-btn-icon-only":
            return self.button
        raise AssertionError(f"unexpected selector: {selector}")


class _InsertLineButton:
    def __init__(self):
        self.clicked = False

    def count(self):
        return 1

    def click(self):
        self.clicked = True


class _InsertLineForm:
    def __init__(self):
        self.button = _InsertLineButton()

    def get_by_text(self, text, exact):
        if text == "插入行" and exact:
            return self.button
        return _MissingButton()


class _WarehouseValue:
    def count(self):
        return 1

    def inner_text(self):
        return "沈桥仓"


class _WarehouseForm:
    def locator(self, selector):
        if selector == '[id^="depotId_"] .ant-select-selection-selected-value':
            return _WarehouseValue()
        raise AssertionError(f"unexpected selector: {selector}")


class _LegacySelectTrigger:
    def __init__(self):
        self.clicked = False
        self.force = None
        self.mouse_down = False
        self.focused = False
        self.key = ""
        self.first = self

    def bounding_box(self):
        return {"x": 100, "y": 200, "width": 260, "height": 36}

    def count(self):
        return 1

    def click(self, force=False, timeout=None):
        self.clicked = True
        self.force = force
        self.timeout = timeout

    def dispatch_event(self, name):
        self.mouse_down = name == "mousedown"

    def focus(self):
        self.focused = True

    def press(self, key):
        self.key = key

    def get_attribute(self, name):
        return ""

    def evaluate(self, script):
        return {
            "class_name": "ant-select-selection", "role": "combobox",
            "visible": True, "tabindex": "0", "owner_class": "ant-select", "point_class": "ant-select-selection",
        }

    def locator(self, selector):
        return _MissingSelect()


class _SelectSearchInput:
    def __init__(self):
        self.value = ""
        self.first = self
        self.force = False
        self.focused = False
        self.visible = False

    def count(self):
        return 1

    def fill(self, value, force=False, timeout=None):
        self.value = value
        self.force = force
        self.timeout = timeout

    def focus(self):
        self.focused = True

    def is_visible(self):
        return self.visible

    def get_attribute(self, name):
        return "" if name == "style" else None


class _SearchableLegacySelectTrigger(_LegacySelectTrigger):
    def __init__(self):
        super().__init__()
        self.search_input = _SelectSearchInput()

    def locator(self, selector):
        if selector == "input":
            return self.search_input
        return _MissingSelect()

    def get_attribute(self, name):
        return "3fadbb75-ffb1-4b24-a919-7d1f929814d7" if name == "aria-controls" else ""


class _BlockedSearchInput(_SelectSearchInput):
    def fill(self, value, force=False, timeout=None):
        raise TimeoutError("搜索输入框一直隐藏")


class _BlockedSearchableLegacySelectTrigger(_SearchableLegacySelectTrigger):
    def __init__(self):
        super().__init__()
        self.search_input = _BlockedSearchInput()


class _SelectedText:
    def __init__(self, value):
        self.value = value
        self.first = self

    def count(self):
        return 1

    def inner_text(self):
        return self.value


class _DelayedSelectedText(_SelectedText):
    def __init__(self, value):
        super().__init__("")
        self.final_value = value

    def reveal(self):
        self.value = self.final_value


class _SelectedLegacyTrigger(_LegacySelectTrigger):
    def __init__(self, value):
        super().__init__()
        self.selected = _SelectedText(value)

    def locator(self, selector):
        if selector == '.ant-select-selection-selected-value, .ant-select-selection-item':
            return self.selected
        return _MissingSelect()


class _DelayedSelectedLegacyTrigger(_SelectedLegacyTrigger):
    def __init__(self, value):
        _LegacySelectTrigger.__init__(self)
        self.selected = _DelayedSelectedText(value)

    def reveal(self):
        self.selected.reveal()


class _MissingSelect:
    def __init__(self):
        self.first = self

    def count(self):
        return 0


class _LegacyHeaderField:
    def __init__(self):
        self.trigger = _LegacySelectTrigger()
        self.first = self

    def locator(self, selector):
        if selector == ".ant-select-selection":
            return self.trigger
        return _MissingSelect()


class _SelectedLegacyHeaderField(_LegacyHeaderField):
    def __init__(self, value):
        self.trigger = _SelectedLegacyTrigger(value)
        self.first = self


class _SearchableLegacyHeaderField(_LegacyHeaderField):
    def __init__(self):
        self.trigger = _SearchableLegacySelectTrigger()
        self.first = self


class _BlockedSearchableLegacyHeaderField(_SearchableLegacyHeaderField):
    def __init__(self):
        self.trigger = _BlockedSearchableLegacySelectTrigger()
        self.first = self


class _FieldTextLegacyHeaderField(_LegacyHeaderField):
    def __init__(self, value):
        super().__init__()
        self.value = value

    def inner_text(self):
        return f"供应商 {self.value}"


class _DelayedSelectedLegacyHeaderField(_LegacyHeaderField):
    def __init__(self, value):
        self.trigger = _DelayedSelectedLegacyTrigger(value)
        self.first = self


class _HeaderFieldCollection:
    def __init__(self, field):
        self.field = field
        self.first = field

    def count(self):
        return 1

    def filter(self, **kwargs):
        return self


class _HeaderChoice:
    def __init__(self):
        self.clicked = False

    def count(self):
        return 1

    def click(self, force=False):
        self.clicked = True
        self.force = force


class _LegacyHeaderDropdown:
    def __init__(self, choice_text="昆山怡口净水系统有限公司"):
        self.choice = _HeaderChoice()
        self.choice_text = choice_text
        self.last = self
        self.waited = None

    def count(self):
        return 1

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)

    def get_by_text(self, text, exact):
        if text == self.choice_text and exact:
            return self.choice
        return _MissingButton()


class _FailingHeaderDropdown(_LegacyHeaderDropdown):
    def wait_for(self, state, timeout):
        raise TimeoutError("候选菜单未挂载")


class _LegacyHeaderForm:
    def __init__(self):
        self.field = _LegacyHeaderField()
        self.fields = _HeaderFieldCollection(self.field)

    def locator(self, selector):
        if selector == ".ant-form-item":
            return self.fields
        raise AssertionError(f"unexpected selector: {selector}")


class _SelectedLegacyHeaderForm(_LegacyHeaderForm):
    def __init__(self, value):
        self.field = _SelectedLegacyHeaderField(value)
        self.fields = _HeaderFieldCollection(self.field)


class _SearchableLegacyHeaderForm(_LegacyHeaderForm):
    def __init__(self):
        self.field = _SearchableLegacyHeaderField()
        self.fields = _HeaderFieldCollection(self.field)


class _BlockedSearchableLegacyHeaderForm(_LegacyHeaderForm):
    def __init__(self):
        self.field = _BlockedSearchableLegacyHeaderField()
        self.fields = _HeaderFieldCollection(self.field)


class _FieldTextLegacyHeaderForm(_LegacyHeaderForm):
    def __init__(self, value):
        self.field = _FieldTextLegacyHeaderField(value)
        self.fields = _HeaderFieldCollection(self.field)


class _DelayedSelectedLegacyHeaderForm(_LegacyHeaderForm):
    def __init__(self, value):
        self.field = _DelayedSelectedLegacyHeaderField(value)
        self.fields = _HeaderFieldCollection(self.field)


class _LegacyHeaderPage:
    def __init__(self, choice_text="昆山怡口净水系统有限公司"):
        self.dropdown = _LegacyHeaderDropdown(choice_text)
        self.selectors = []
        self.mouse = _PageMouse()

    def locator(self, selector):
        self.selectors.append(selector)
        if selector in ('[id="3fadbb75-ffb1-4b24-a919-7d1f929814d7"]', ".ant-select-dropdown"):
            return self.dropdown
        if selector == ".introjs-skipbutton:visible":
            return _MissingButton()
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_timeout(self, timeout):
        self.timeout = timeout


class _PageMouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _DelayedSelectedLegacyHeaderPage(_LegacyHeaderPage):
    def __init__(self, form, choice_text="昆山怡口净水"):
        super().__init__(choice_text)
        self.form = form

    def wait_for_timeout(self, timeout):
        self.timeout = timeout
        self.form.field.trigger.reveal()


class _SupplierDiagnosticsPage(_LegacyHeaderPage):
    def __init__(self):
        super().__init__("昆山怡口净水")
        self.dropdown = _FailingHeaderDropdown("昆山怡口净水")


class _TourSkipButton:
    def __init__(self):
        self.clicked = False
        self.last = self

    def count(self):
        return 1

    def click(self):
        self.clicked = True


class _TourHeaderPage(_LegacyHeaderPage):
    def __init__(self):
        super().__init__("昆山怡口净水")
        self.skip = _TourSkipButton()

    def locator(self, selector):
        if selector == ".introjs-skipbutton:visible":
            return self.skip
        return super().locator(selector)


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
            "供应商": "昆山怡口净水",
        })
        self.assertEqual(page.remark, "装箱单号：SH202607210002")
        self.assertEqual(result["order_no"], "CG202608130001")

    def test_writer_stops_before_save_when_product_lookup_fails(self):
        page = FakeGYJPage(product_found=False)

        with self.assertRaisesRegex(GYJInboundError, "未找到物料编码"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertNotIn("保存", page.clicked)


class GYJPurchaseInboundPageTest(unittest.TestCase):
    def test_waits_for_purchase_inbound_new_button_before_clicking(self):
        page = _PurchaseInboundListPage()

        adapter = GYJPlaywrightPage(page)
        adapter.open_new_form()

        self.assertTrue(page.new_button.clicked)
        self.assertIs(adapter.form, page.modal)

    def test_uses_actual_plain_save_button_label(self):
        form = _ActualGYJSaveForm()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = form

        adapter.click_plain_save()

        self.assertTrue(form.save_button.clicked)

    def test_finds_the_entry_row_between_header_and_summary(self):
        form = _ActualInboundRowsForm()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = form

        row = adapter._entry_row()

        self.assertIs(row, form.entry)

    def test_fills_quantity_by_its_row_specific_input_id(self):
        row = _ActualInboundRow()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())

        adapter._fill_quantity(row, 10)

        self.assertEqual(row.quantity_input.value, "10")
        self.assertEqual(row.quantity_input.key, "Tab")
        self.assertEqual(row.quantity_input.waited, ("visible", 5000))

    def test_reports_product_search_result_count_when_item_is_missing(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        modal = _ProductSearchModal()
        adapter._visible_modal = lambda: modal

        with self.assertRaisesRegex(GYJInboundError, r"406005116（结果行=0）"):
            adapter._choose_product(_ProductSelectionRow(), "406005116")

    def test_inserts_another_row_after_the_first_prepared_line(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = _InsertLineForm()
        adapter._entered_lines = [{"product_code": "926019528"}]
        adapter._entry_row = lambda: object()
        adapter._choose_product = lambda row, code: None
        adapter._fill_quantity = lambda row, quantity: None

        adapter.add_product_line({"product_code": "906018301", "serials": [], "quantity": 60})

        self.assertTrue(adapter.form.button.clicked)

    def test_accepts_the_visible_default_warehouse_for_the_first_row(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = _WarehouseForm()

        adapter.select_header("仓库", "沈桥仓")

        self.assertEqual(adapter._headers, {"仓库": "沈桥仓"})

    def test_selects_supplier_from_legacy_ant_design_select(self):
        page = _LegacyHeaderPage()
        adapter = GYJPlaywrightPage(page)
        adapter.form = _LegacyHeaderForm()

        adapter.select_header("供应商", "昆山怡口净水系统有限公司")

        self.assertTrue(adapter.form.field.trigger.clicked)
        self.assertTrue(adapter.form.field.trigger.force)
        self.assertEqual(page.dropdown.waited, ("attached", 5000))
        self.assertTrue(page.dropdown.choice.clicked)
        self.assertEqual(adapter._headers, {"供应商": "昆山怡口净水系统有限公司"})

    def test_searches_supplier_before_waiting_for_dropdown(self):
        page = _LegacyHeaderPage("昆山怡口净水")
        adapter = GYJPlaywrightPage(page)
        adapter.form = _SearchableLegacyHeaderForm()

        adapter.select_header("供应商", "昆山怡口净水")

        self.assertTrue(adapter.form.field.trigger.clicked)
        self.assertTrue(adapter.form.field.trigger.force)
        self.assertTrue(adapter.form.field.trigger.search_input.focused)
        self.assertEqual(adapter.form.field.trigger.search_input.value, "昆山怡口净水")
        self.assertFalse(adapter.form.field.trigger.search_input.force)
        self.assertEqual(adapter.form.field.trigger.search_input.timeout, 10000)
        self.assertTrue(page.dropdown.choice.clicked)
        self.assertIn('[id="3fadbb75-ffb1-4b24-a919-7d1f929814d7"]', page.selectors)
        self.assertNotIn(".ant-select-dropdown", page.selectors)

    def test_dismisses_gyj_intro_tour_before_opening_supplier(self):
        page = _TourHeaderPage()
        adapter = GYJPlaywrightPage(page)
        adapter.form = _SearchableLegacyHeaderForm()

        adapter.select_header("供应商", "昆山怡口净水")

        self.assertTrue(page.skip.clicked)

    def test_reports_supplier_control_state_when_candidate_menu_never_attaches(self):
        page = _SupplierDiagnosticsPage()
        adapter = GYJPlaywrightPage(page)
        adapter.form = _SearchableLegacyHeaderForm()

        with self.assertRaisesRegex(
            GYJInboundError,
            r"控件展开=未知，输入框可见=False，候选层ID=3fadbb75-ffb1-4b24-a919-7d1f929814d7，控件角色=combobox，控件可见=True，控件tabindex=0，外层类=ant-select，点击点类=ant-select-selection",
        ):
            adapter.select_header("供应商", "昆山怡口净水")

    def test_reports_supplier_control_state_when_search_input_stays_hidden(self):
        page = _LegacyHeaderPage("昆山怡口净水")
        adapter = GYJPlaywrightPage(page)
        adapter.form = _BlockedSearchableLegacyHeaderForm()

        with self.assertRaisesRegex(
            GYJInboundError,
            r"GYJ 供应商输入框未打开（控件角色=combobox，控件可见=True，控件tabindex=0，外层类=ant-select，点击点类=ant-select-selection）",
        ):
            adapter.select_header("供应商", "昆山怡口净水")

    def test_keeps_supplier_when_the_form_already_shows_the_default(self):
        page = _LegacyHeaderPage("昆山怡口净水")
        adapter = GYJPlaywrightPage(page)
        adapter.form = _SelectedLegacyHeaderForm("昆山怡口净水")

        adapter.select_header("供应商", "昆山怡口净水")

        self.assertFalse(adapter.form.field.trigger.clicked)
        self.assertEqual(adapter._headers, {"供应商": "昆山怡口净水"})

    def test_keeps_supplier_when_only_the_form_field_shows_the_default(self):
        page = _LegacyHeaderPage("昆山怡口净水")
        adapter = GYJPlaywrightPage(page)
        adapter.form = _FieldTextLegacyHeaderForm("昆山怡口净水系统有限公司")

        adapter.select_header("供应商", "昆山怡口净水")

        self.assertFalse(adapter.form.field.trigger.clicked)
        self.assertEqual(adapter._headers, {"供应商": "昆山怡口净水"})

    def test_selects_supplier_immediately_when_default_has_not_rendered(self):
        form = _DelayedSelectedLegacyHeaderForm("昆山怡口净水系统有限公司")
        page = _DelayedSelectedLegacyHeaderPage(form)
        adapter = GYJPlaywrightPage(page)
        adapter.form = form

        adapter.select_header("供应商", "昆山怡口净水")

        self.assertTrue(form.field.trigger.clicked)
        self.assertTrue(form.field.trigger.force)
        self.assertTrue(page.dropdown.choice.clicked)
        self.assertEqual(adapter._headers, {"供应商": "昆山怡口净水"})


if __name__ == "__main__":
    unittest.main()
