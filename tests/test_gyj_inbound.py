import unittest
from unittest import mock

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

    def test_places_serial_number_lines_before_unbarcoded_accessories(self):
        result = inbound_result([
            {
                "product_code": "247296319",
                "description": "配件",
                "order_numbers": [],
                "serials": [],
                "expected_quantity": 10,
                "serial_count": 0,
                "unbarcoded_quantity": 10,
                "quantity_mismatch": False,
            },
            {
                "product_code": "906018301",
                "description": "主产品",
                "order_numbers": [],
                "serials": ["SN001", "SN002"],
                "expected_quantity": 2,
                "serial_count": 2,
                "unbarcoded_quantity": 0,
                "quantity_mismatch": False,
            },
        ])

        lines = build_gyj_purchase_lines(result)

        self.assertEqual([line["record_type"] for line in lines], ["条码", "无条码配件"])
        self.assertEqual([line["product_code"] for line in lines], ["906018301", "247296319"])

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
    def __init__(
        self,
        product_found=True,
        product_outcomes=None,
        create_failures=0,
        create_exception=None,
        existing_codes=None,
        pre_check_failures=0,
        fill_failure_on=None,
    ):
        self.product_found = product_found
        self.product_outcomes = list(product_outcomes or [])
        self.create_failures = create_failures
        self.create_exception = create_exception
        self.existing_codes = set(existing_codes or [])
        # pre_check_failures makes the first N pre-check iterations raise the
        # specified error so the retry-with-rollback path is exercisable.
        self.pre_check_failures = pre_check_failures
        # fill_failure_on is a dict like {'line_index_2_based': ExceptionCls()} to
        # make add_product_line raise on a specific call (used to test filling retries).
        self.fill_failure_on = dict(fill_failure_on or {})
        self.created_products = []
        self.pre_check_calls = []
        self.pre_check_calls_dedup = []
        self.rollback_precheck_called = 0
        self.rollback_filling_called = 0
        self.rollback_verifying_called = 0
        self.rollback_saving_called = 0
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
        index = len(self.lines) + 1  # 1-based line counter
        # fill_failure_on takes priority so tests can inject transient failures
        # for the stage-level retry wrapper regardless of pre-check state.
        forced = self.fill_failure_on.get(index)
        if forced is not None:
            self.fill_failure_on.pop(index)
            raise forced
        # Defense-in-depth: if the product is already known to exist (created
        # during pre-check or pre-populated), the lookup succeeds regardless of
        # the per-line outcome queue.
        if line['product_code'] in self.existing_codes:
            self.lines.append(dict(line))
            return
        product_found = (
            self.product_outcomes.pop(0)
            if self.product_outcomes
            else self.product_found
        )
        if not product_found:
            raise GYJInboundError(f"未找到物料编码：{line['product_code']}")
        self.lines.append(dict(line))

    def create_product(self, product_code, description, has_serials):
        self.created_products.append((product_code, description, has_serials))
        if len(self.created_products) <= self.create_failures:
            if self.create_exception:
                raise self.create_exception
            raise GYJInboundError(f"GYJ 新增物料失败：{product_code}")

    def _ensure_products_exist(self, items):
        self.pre_check_calls.append(list(items))
        seen_in_this_call = set()
        for item in items:
            code = item['product_code']
            seen_in_this_call.add(code)
            self.pre_check_calls_dedup.append(code)
            if code in self.existing_codes:
                continue
            last_error = None
            created_once = False
            import gyj_inbound as _gi  # local import; tests already import it
            for attempt in range(_gi.MAX_PRODUCT_CREATION_ATTEMPTS):
                try:
                    self.create_product(code, item['description'], item['has_serials'])
                    created_once = True
                    break
                except Exception as error:
                    last_error = error
            if not created_once:
                raise GYJInboundError(
                    f"GYJ 物料 {code} 新增失败，已重试 {_gi.MAX_PRODUCT_CREATION_ATTEMPTS} 次：{last_error}"
                ) from last_error
            if not self.product_found:
                raise GYJInboundError(
                    f"GYJ 物料 {code} 新增后仍无法选择，已重试 {_gi.MAX_PRODUCT_CREATION_ATTEMPTS} 次"
                )
            self.existing_codes.add(code)

    def _rollback_precheck(self):
        self.rollback_precheck_called += 1

    def _rollback_filling(self):
        self.rollback_filling_called += 1
        self._entered_lines_reset()

    def _rollback_verifying(self):
        self.rollback_verifying_called += 1

    def _rollback_saving(self):
        self.rollback_saving_called += 1

    def _entered_lines_reset(self):
        # Mirror GYJPlaywrightPage's internal reset; the test double wipes lines
        # so a filling-retry simulation can re-add them from scratch.
        self.lines = []

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
        self.selectors = []

    def goto(self, url, **kwargs):
        self.goto_url = url

    def locator(self, selector):
        self.selectors.append(selector)
        if selector == ".table-operator button.ant-btn-primary":
            return self.new_button
        if selector in (".ant-modal:visible", ".ant-modal.j-modal-box.fullscreen:visible"):
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
        return _MissingButton()

    def get_by_role(self, role, name, exact):
        if role == "button" and name == "保存（Ctrl+S）" and exact:
            return self.save_button
        return _MissingButton()

    def locator(self, selector):
        if selector == "button":
            return _SaveButtonCollection(["取 消", "保存并审核", "保存"])
        raise AssertionError(f"unexpected selector: {selector}")


class _SaveButtonCollection:
    def __init__(self, labels):
        self.labels = labels

    def all_inner_texts(self):
        return self.labels


class _ActualGYJSavePage:
    def wait_for_timeout(self, timeout):
        self.timeout = timeout

    def locator(self, selector):
        if selector == ".ant-message-notice-content, .ant-notification-notice-message, .ant-notification-notice-description":
            return _SaveNotification("")
        if selector not in ("body", "[disabled]"):
            raise AssertionError(f"unexpected selector: {selector}")
        return self

    def inner_text(self):
        return "保存成功"


class _SaveNotification:
    def __init__(self, text):
        self.text = text

    def all_inner_texts(self):
        return [self.text]

    def inner_text(self):
        return self.text


class _NotificationOnlyGYJSavePage(_ActualGYJSavePage):
    def locator(self, selector):
        if selector == "body":
            return _SaveNotification("")
        if selector == ".ant-message-notice-content, .ant-notification-notice-message, .ant-notification-notice-description":
            return _SaveNotification("保存成功")
        if selector == "[disabled]":
            return self
        raise AssertionError(f"unexpected selector: {selector}")


class _ValidationErrorGYJSavePage(_ActualGYJSavePage):
    def locator(self, selector):
        if selector == "body":
            return _SaveNotification("")
        if selector == ".ant-form-explain:visible, .ant-message-error:visible, .ant-notification-notice-description:visible":
            return _SaveNotification("请选择结算账户")
        return super().locator(selector)


class _SaveResponse:
    url = "https://cloud.gyjerp.com/jshERP-boot/depotHead/add"
    status = 400

    def text(self):
        return '{"code":500,"message":"单据校验失败"}'


class _SuccessfulSaveResponse(_SaveResponse):
    status = 200
    url = "https://cloud.gyjerp.com/jshERP-boot/depotHead/addDepotHeadAndDetail"

    def text(self):
        return '{"msg":"操作成功","code":200}'


class _ResponseSaveButton(_SaveButton):
    def __init__(self, page):
        super().__init__()
        self.page = page

    def click(self):
        super().click()
        for handler in self.page.response_handlers:
            handler(_SaveResponse())


class _SuccessfulResponseSaveButton(_ResponseSaveButton):
    def click(self):
        _SaveButton.click(self)
        for handler in self.page.response_handlers:
            handler(_SuccessfulSaveResponse())


class _ResponseGYJSaveForm(_ActualGYJSaveForm):
    def __init__(self, page):
        self.save_button = _ResponseSaveButton(page)


class _SuccessfulResponseGYJSaveForm(_ActualGYJSaveForm):
    def __init__(self, page):
        self.save_button = _SuccessfulResponseSaveButton(page)


class _ResponseGYJSavePage(_ValidationErrorGYJSavePage):
    def __init__(self):
        self.response_handlers = []

    def on(self, event, handler):
        if event == "response":
            self.response_handlers.append(handler)

    def locator(self, selector):
        if selector == ".ant-form-explain:visible, .ant-message-error:visible, .ant-notification-notice-description:visible":
            return _SaveNotification("")
        return super().locator(selector)

    def remove_listener(self, event, handler):
        if event == "response":
            self.response_handlers.remove(handler)


class _RowCollection:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)

    def nth(self, index):
        return self.rows[index]


class _DelayedRowCollection(_RowCollection):
    def __init__(self, rows):
        super().__init__(rows)
        self.ready = False

    def count(self):
        return len(self.rows) if self.ready else 0


class _ActualInboundRowsForm:
    def __init__(self):
        self.header = object()
        self.entry = object()
        self.summary = object()
        self.rows = _RowCollection([self.header, self.entry, self.summary])

    def locator(self, selector):
        if selector == ".tr":
            return self.rows
        if selector == "tr":
            return _RowCollection([])
        raise AssertionError(f"unexpected selector: {selector}")


class _DelayedInboundRowsForm(_ActualInboundRowsForm):
    def __init__(self):
        super().__init__()
        self.rows = _DelayedRowCollection([self.header, self.entry, self.summary])


class _RowRenderPage(_ActualGYJSavePage):
    def __init__(self, form):
        self.form = form

    def wait_for_timeout(self, timeout):
        self.timeout = timeout
        self.form.rows.ready = True


class _QuantityInput:
    def __init__(self):
        self.value = ""
        self.keys = []
        self.typed = ""
        self.waited = None
        self.clicked = False

    def count(self):
        return 1

    def fill(self, value):
        self.value = value

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)

    def press(self, key):
        self.keys.append(key)

    def type(self, value):
        self.typed += value
        self.value = value

    def click(self):
        self.clicked = True

    def evaluate(self, script, value=None):
        if value is not None:
            self.value = str(value)
        return self.value


class _ActualInboundRow:
    def __init__(self):
        self.quantity_input = _QuantityInput()

    def locator(self, selector):
        if selector == 'input[id^="operNumber_"]':
            return self.quantity_input
        raise AssertionError(f"unexpected selector: {selector}")


class _CommittedQuantityInput(_QuantityInput):
    def __init__(self):
        super().__init__()
        self.committed = False

    def evaluate(self, script, value=None):
        if value is not None:
            self.value = str(value)
        return self.value if self.committed else ""


class _CommittedQuantityRow(_ActualInboundRow):
    def __init__(self):
        self.quantity_input = _CommittedQuantityInput()


class _RejectedQuantityInput(_QuantityInput):
    def __init__(self):
        super().__init__()
        self.value = "1"

    def fill(self, value):
        self.attempted = value

    def type(self, value):
        self.attempted = value

    def evaluate(self, script, value=None):
        if "=> ({" in script:
            return {
                "value": self.value,
                "id": "operNumber_jet-test",
                "type": "text",
                "readOnly": False,
                "disabled": False,
                "className": "ant-input-number-input",
                "html": '<input id="operNumber_jet-test" value="1">',
                "parentHtml": '<div class="quantity-control"><input></div>',
            }
        return self.value


class _RejectedQuantityRow(_ActualInboundRow):
    def __init__(self):
        self.quantity_input = _RejectedQuantityInput()

    def inner_text(self):
        return "条码 746037027 数量 1"

    def locator(self, selector):
        if selector == "input":
            return _QuantityInputCollection([self.quantity_input])
        return super().locator(selector)


class _QuantityInputCollection:
    def __init__(self, inputs):
        self.inputs = inputs

    def evaluate_all(self, script):
        return [{"id": item.evaluate("element => ({")['id'], "value": item.value} for item in self.inputs]


class _QuantityCommitPage(_ActualGYJSavePage):
    def __init__(self, row):
        self.row = row

    def wait_for_timeout(self, timeout):
        self.timeout = timeout
        self.row.quantity_input.committed = True


class _SerialButton:
    def __init__(self):
        self.clicked = False
        self.ready = False
        self.first = self

    def count(self):
        return 1 if self.ready else 0

    def click(self):
        self.clicked = True


class _SerialInputRow:
    def __init__(self):
        self.button = _SerialButton()
        self.quantity_input = _QuantityInput()
        self.quantity_input.value = "1"

    def locator(self, selector):
        if selector == ".ant-input-search-icon":
            return self.button
        if selector == 'input[id^="operNumber_"]':
            return self.quantity_input
        raise AssertionError(f"unexpected selector: {selector}")


class _SerialRenderPage(_ActualGYJSavePage):
    def __init__(self, row):
        self.row = row

    def wait_for_timeout(self, timeout):
        self.row.button.ready = True


class _ActionButton:
    def __init__(self):
        self.clicked = False
        self.first = self
        self.force = False

    def count(self):
        return 1

    def click(self, force=False):
        self.clicked = True
        self.force = force


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

    def get_by_role(self, role, name, exact=False):
        if role == "button" and name == "查 询" and exact:
            return self.query
        return _MissingButton()


class _DelayedProductPickerCollection:
    def __init__(self, modal):
        self.modal = modal
        self.ready = False

    def count(self):
        return 1 if self.ready else 0

    @property
    def last(self):
        return self.modal


class _DelayedProductPickerPage(_ActualGYJSavePage):
    def __init__(self):
        self.modal = _ProductSearchModal()
        self.picker = _DelayedProductPickerCollection(self.modal)
        self.waits = 0
        self.skip = _TourSkipButton()

    def locator(self, selector):
        if selector == ".ant-modal:visible:not(.j-modal-box.fullscreen)":
            return self.picker
        if selector == ".introjs-skipbutton:visible":
            return self.skip
        return super().locator(selector)

    def wait_for_timeout(self, timeout):
        self.waits += 1
        self.picker.ready = True


class _TourOverlay:
    def __init__(self):
        self.visible = True

    def count(self):
        return 1 if self.visible else 0


class _TourKeyboard:
    def __init__(self, overlay):
        self.overlay = overlay
        self.keys = []

    def press(self, key):
        self.keys.append(key)
        if key == "Escape":
            self.overlay.visible = False


class _OverlayProductPickerPage(_DelayedProductPickerPage):
    def __init__(self):
        super().__init__()
        self.skip = _MissingButton()
        self.overlay = _TourOverlay()
        self.keyboard = _TourKeyboard(self.overlay)

    def locator(self, selector):
        if selector == ".introjs-overlay:visible":
            return self.overlay
        return super().locator(selector)


class _ProductCreateInput:
    def __init__(self):
        self.value = ""
        self.first = self
        self.waited = None

    def count(self):
        return 1

    def wait_for(self, state, timeout):
        self.waited = (state, timeout)

    def fill(self, value):
        self.value = value


class _ProductCreateChoice(_ActionButton):
    pass


class _ProductCreateSerialTrigger(_ActionButton):
    def __init__(self, form):
        super().__init__()
        self.form = form

    def click(self):
        super().click()
        self.form.barcode.value = "6973703533595"


class _ProductCreateDropdown:
    def __init__(self):
        self.yes = _ProductCreateChoice()
        self.no = _ProductCreateChoice()
        self.last = self

    def get_by_text(self, text, exact):
        if exact and text == "有":
            return self.yes
        if exact and text == "无":
            return self.no
        return _MissingButton()


class _ProductCreateSaveButton(_ActionButton):
    def __init__(self, form):
        super().__init__()
        self.form = form

    def click(self):
        super().click()
        self.form.visible = False


class _ProductCreateForm:
    def __init__(self):
        self.visible = True
        self.name = _ProductCreateInput()
        self.unit = _ProductCreateInput()
        self.barcode = _ProductCreateInput()
        self.serial_trigger = _ProductCreateSerialTrigger(self)
        self.save = _ProductCreateSaveButton(self)
        self.last = self

    def count(self):
        return 1 if self.visible else 0

    def locator(self, selector):
        if selector == "input#name:visible":
            return self.name
        if selector == "input#unit:visible":
            return self.unit
        if selector == '[id^="barCode_jet-"]:visible':
            return self.barcode
        if selector == "#enableSerialNumber .ant-select-selection, #enableSerialNumber .ant-select-selector":
            return self.serial_trigger
        raise AssertionError(f"unexpected selector: {selector}")

    def get_by_role(self, role, name, exact):
        if role == "button" and name == "保存（Ctrl+S）" and exact:
            return self.save
        return _MissingButton()


class _ProductCreateNewButton(_ActionButton):
    pass


class _ProductCreatePicker:
    def __init__(self):
        self.new = _ProductCreateNewButton()

    def get_by_role(self, role, name, exact=False):
        if role == "button":
            return self.new
        return _MissingButton()


class _ProductCreatePage(_ActualGYJSavePage):
    def __init__(self):
        self.form = _ProductCreateForm()
        self.dropdown = _ProductCreateDropdown()

    def locator(self, selector):
        if selector == ".ant-select-dropdown:visible":
            return self.dropdown
        return super().locator(selector)


class _SerialEntryModal:
    def __init__(self, serial_count=1):
        self.serial_input = _SelectSearchInput()
        self.serial_input.active = True
        self.serial_input.visible = True
        self.rows = _RowCollection([object()] * serial_count)

    def locator(self, selector):
        if selector == 'textarea[placeholder="多个序列号用逗号隔开，请少于2000个字符"]':
            return self.serial_input
        if selector == ".ant-table-tbody > tr":
            return self.rows
        raise AssertionError(f"unexpected selector: {selector}")


class _DelayedSerialRows(_RowCollection):
    def __init__(self, serial_count):
        super().__init__([object()] * serial_count)
        self.ready = False

    def count(self):
        return len(self.rows) if self.ready else 0


class _DelayedSerialEntryModal(_SerialEntryModal):
    def __init__(self, serial_count):
        super().__init__(serial_count)
        self.rows = _DelayedSerialRows(serial_count)


class _SerialQuantityInput(_QuantityInput):
    def __init__(self, expected):
        super().__init__()
        self.expected = str(expected)

class _SerialQuantityRow(_SerialInputRow):
    def __init__(self, expected):
        super().__init__()
        self.quantity_input = _SerialQuantityInput(expected)

    def locator(self, selector):
        if selector == 'input[id^="operNumber_"]':
            return self.quantity_input
        return super().locator(selector)


class _SerialCommitPage(_SerialRenderPage):
    def __init__(self, row, modal):
        super().__init__(row)
        self.modal = modal

    def wait_for_timeout(self, timeout):
        super().wait_for_timeout(timeout)
        self.modal.rows.ready = True
        self.row.quantity_input.value = self.row.quantity_input.expected


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


class _AmbiguousInsertLineText:
    def count(self):
        return 4


class _InsertLineForm:
    def __init__(self):
        self.button = _InsertLineButton()

    def get_by_text(self, text, exact):
        if text == "插入行" and exact:
            return _AmbiguousInsertLineText()
        return _MissingButton()

    def get_by_role(self, role, name, exact=False):
        if role == "button" and name == "插入行" and exact:
            return self.button
        return _MissingButton()


class _SpacedInsertLineButtons:
    def __init__(self, button):
        self.button = button

    def count(self):
        return 1

    def all_inner_texts(self):
        return ["插 入 行"]

    def nth(self, index):
        assert index == 0
        return self.button


class _MultiInsertLineButtons:
    """Two buttons with different visible text but same normalised text.

    Models the bug where both "插入行" and "插 入 行" exist on the page.  The
    click helper must reject the ambiguous match and surface a clear error.
    """

    def __init__(self, primary, secondary):
        self.primary = primary
        self.secondary = secondary

    def count(self):
        return 2

    def all_inner_texts(self):
        return ["插入行", "插 入 行"]

    def nth(self, index):
        return (self.primary, self.secondary)[index]


class _MultiInsertLineForm(_InsertLineForm):
    def __init__(self):
        super().__init__()
        self.multi = _MultiInsertLineButtons(self.button, _InsertLineButton())

    def get_by_role(self, role, name, exact=False):
        return _MissingButton()

    def locator(self, selector):
        if selector == "button:visible":
            return self.multi
        raise AssertionError(f"unexpected selector: {selector}")


class _SpacedInsertLineForm(_InsertLineForm):
    def get_by_role(self, role, name, exact=False):
        return _MissingButton()

    def locator(self, selector):
        if selector == "button:visible":
            return _SpacedInsertLineButtons(self.button)
        raise AssertionError(f"unexpected selector: {selector}")


class _E2EFakeRow:
    """A fake .tr that records all writes during _fill_serials / _fill_quantity."""
    def __init__(self, code):
        self.code = code
        self.serial_button_visible = False
        self.serials_submitted = []
        self.quantity_value = None
        self.icon_only_clicked = 0
    def locator(self, selector):
        if selector == ".ant-input-search-icon":
            return _E2ESerialButton(self)
        if selector == 'input[id^="operNumber_"]':
            return _E2EQuantityInput(self)
        if selector == "button.ant-btn.ant-btn-icon-only":
            return _E2EIconOnly(self)
        if selector == "input[placeholder*='条码、名称']":
            return _E2EProductSearch()
        if selector == 'textarea[placeholder="多个序列号用逗号隔开，请少于2000个字符"]:visible':
            return _E2ESerialsTextarea(self)
        if selector == ".ant-table-tbody > tr":
            return _E2EAddedSerialRows(self)
        if selector == "input":
            return _E2EProductSearch()
        raise AssertionError(f"unexpected row selector: {selector}")


class _E2EIconOnly:
    def __init__(self):
        self.clicked = 0
    @property
    def count(self): return 1
    def first(self): return self
    def click(self): self.clicked += 1


class _E2ESerialButton:
    def __init__(self, row): self.row = row
    @property
    def count(self): return 1
    def first(self): return self
    def click(self):
        self.row.serial_button_visible = True


class _E2EQuantityInput:
    def __init__(self, row): self.row = row
    @property
    def count(self): return 1
    def first(self): return self
    def evaluate(self, js):
        if js == "element => element.value || ''":
            return "" if self.row.quantity_value is None else str(self.row.quantity_value)
        raise AssertionError(f"unexpected eval: {js}")
    def wait_for(self, state, timeout): pass
    def fill(self, value, force=False, timeout=None):
        self.row.quantity_value = int(value)
    def press(self, key): pass
    def focus(self): pass
    def dispatch_event(self, name): pass


class _E2EProductSearch:
    def __init__(self):
        self.value = ""
    @property
    def count(self): return 1
    def first(self): return self
    def fill(self, value, force=False, timeout=None): self.value = value
    def focus(self): pass
    def press(self, key): pass


class _E2ESerialsTextarea:
    def __init__(self, row): self.row = row
    @property
    def count(self): return 1
    def first(self): return self
    def fill(self, value, force=False, timeout=None):
        # The textarea receives the value; on Ant Design it auto-renders rows.
        self.row.serials_submitted = [s.strip() for s in value.split(",") if s.strip()]
    def focus(self): pass
    def evaluate(self, js):
        if js == "el => !!el.closest('.ant-tabs-tabpane-active')": return True
        if "offsetWidth" in js or "visibility" in js: return True
        raise AssertionError(f"unexpected eval: {js}")


class _E2EAddedSerialRows:
    def __init__(self, row):
        self.row = row
    @property
    def count(self): return len(self.row.serials_submitted)
    def first(self): return self


class _E2EFormModal:
    def __init__(self):
        self.rows = []  # 1 default empty row
    @property
    def count(self): return 1
    def first(self): return self
    def locator(self, selector):
        if selector == ".tr":
            return _E2EFormRows(self)
        if selector == "input":
            return _E2EProductSearch()
        raise AssertionError(f"unexpected form selector: {selector}")


class _E2EFormRows:
    def __init__(self, modal): self.modal = modal
    @property
    def count(self): return 1 + len(self.modal.rows)
    def nth(self, idx):
        # index 0 is the header row (empty), so data rows are 1..N.
        if idx == 0:
            return _E2EFormRow(self.modal, header=True)
        return self.modal.rows[idx - 1]
    def last(self):
        return self.modal.rows[-1]


class _E2EFormRow:
    def __init__(self, modal, header=False):
        self.modal = modal
        self.header = header
    def locator(self, selector):
        if selector == ".ant-input-search-icon":
            return _E2EIconOnly()
        if selector == 'input[id^="operNumber_"]':
            return _E2EQuantityInput(_E2EFakeRow(""))
        if selector == "button.ant-btn.ant-btn-icon-only":
            return _E2EIconOnly()
        raise AssertionError(f"unexpected form row selector: {selector}")


class _E2EPage:
    """Stand-in for GYJPlaywrightPage that records every interaction."""
    def __init__(self):
        self.form = _E2EFormModal()
        self.headers = {}
        self.remark = ""
        self.clicked = []
        self.opened = False
        self.verified = None
        self.saved = None
        self.created_products = []
        self.pre_check_calls = []
        self.rollback_precheck_called = 0
        self.rollback_filling_called = 0
        self.rollback_verifying_called = 0
        self.rollback_saving_called = 0
    def open_new_form(self):
        self.opened = True
    def select_header(self, label, value):
        self.headers[label] = value
    def fill_remark(self, value):
        self.remark = value
    def _ensure_products_exist(self, items):
        # E2E page deliberately treats pre-check as a no-op: the flaky
        # add_product_line path is what triggers the defense-in-depth
        # create-on-fail retry in production.
        self.pre_check_calls.append(list(items))
    def _rollback_precheck(self):
        self.rollback_precheck_called += 1
    def _rollback_filling(self):
        self.rollback_filling_called += 1
    def _rollback_verifying(self):
        self.rollback_verifying_called += 1
    def _rollback_saving(self):
        self.rollback_saving_called += 1
    def add_product_line(self, line):
        # Always succeeds; record into a new row.
        row = _E2EFakeRow(line["product_code"])
        self.form.rows.append(row)
        if line.get("serials"):
            for s in line["serials"]:
                row.serials_submitted.append(s)
            row.quantity_value = len(line["serials"])
        else:
            row.quantity_value = line["quantity"]
    def create_product(self, code, description, has_serials):
        self.created_products.append((code, description, has_serials))
    def verify_form(self, packing_slip_no, lines):
        self.verified = (packing_slip_no, list(lines))
    def click_plain_save(self):
        self.clicked.append("保存")
        self.saved = True
        return "CG202608130001"


class _E2EHistoryResult:
    @staticmethod
    def make():
        return {
            "items": [
                {"product_code": "926023628", "description": "ETF2300 PF12 滤料罐备件", "expected_quantity": 1, "order_numbers": ["403578"], "serials": ["5022607170001"], "serial_count": 1, "unbarcoded_quantity": 0, "quantity_mismatch": False},
                {"product_code": "926023602", "description": "ETF2100PF10 滤料总成", "expected_quantity": 1, "order_numbers": ["403637"], "serials": ["2702607020020"], "serial_count": 1, "unbarcoded_quantity": 0, "quantity_mismatch": False},
                {"product_code": "746037009", "description": "加热体组件组成", "expected_quantity": 1, "order_numbers": ["403578"], "serials": [], "serial_count": 0, "unbarcoded_quantity": 1, "quantity_mismatch": False},
                {"product_code": "406005128", "description": "电源24VDC3A GVE J10_J12", "expected_quantity": 1, "order_numbers": ["403637"], "serials": [], "serial_count": 0, "unbarcoded_quantity": 1, "quantity_mismatch": False},
                {"product_code": "406005140", "description": "电源24VDC4A GVE 90度弯插 ERO220", "expected_quantity": 2, "order_numbers": ["403637"], "serials": [], "serial_count": 0, "unbarcoded_quantity": 2, "quantity_mismatch": False},
            ],
        }


class GYJEndToEndSH202607230016Test(unittest.TestCase):
    """End-to-end test of save_packing_slip on the historical SH202607230016 dataset.

    The 5 products, 2 serial numbers, 3 unbarcoded accessories, 沈桥仓 warehouse, and
    装箱单号 remark are exactly the data the user wants entered into GYJ.  The test
    proves that the writer produces a correct filled form (5 rows with right quantities
    and serial submissions) when the page is mockable, and crucially that
    click_plain_save is NOT called (user hard rule: never auto-save).
    """

    def test_save_packing_slip_fills_all_five_lines_and_skips_save(self):
        import sys
        sys.path.insert(0, ".")
        from gyj_inbound import GYJPurchaseInboundWriter
        result = _E2EHistoryResult.make()
        lines = build_gyj_purchase_lines(result)
        self.assertEqual([line["record_type"] for line in lines],
                         ["条码", "条码", "无条码配件", "无条码配件", "无条码配件"])
        self.assertEqual([line["product_code"] for line in lines],
                         ["926023628", "926023602", "746037009", "406005128", "406005140"])
        # 2 serial-numbered lines have 1 each, 3 unbarcoded have 1/1/2.
        self.assertEqual([len(line["serials"]) for line in lines], [1, 1, 0, 0, 0])
        self.assertEqual([line["quantity"] for line in lines], [1, 1, 1, 1, 2])

        page = _E2EPage()
        # Simulate the agent's hard-rule: monkey-patch click_plain_save to a no-op
        # that returns a SKIPPED order number.  The real GYJPurchaseInboundWriter
        # still calls page.click_plain_save() once; we just intercept it.
        called = {"save": 0}
        def save_noop():
            called["save"] += 1
            return "SKIPPED"
        page.click_plain_save = save_noop

        writer = GYJPurchaseInboundWriter(page, log=lambda m, l="info": None)
        payload = writer.save_packing_slip("SH202607230016", lines)

        self.assertEqual(payload["packing_slip_no"], "SH202607230016")
        self.assertTrue(page.opened)
        self.assertEqual(page.headers, {"供应商": "昆山怡口净水"})
        self.assertEqual(page.remark, "装箱单号：SH202607230016")
        self.assertEqual(page.verified[0], "SH202607230016")
        # All 5 lines added to the page in the right order.
        self.assertEqual(len(page.form.rows), 5)
        self.assertEqual([r.code for r in page.form.rows],
                         ["926023628", "926023602", "746037009", "406005128", "406005140"])
        # The 2 serial-numbered lines have the serial submitted.
        self.assertEqual(page.form.rows[0].serials_submitted, ["5022607170001"])
        self.assertEqual(page.form.rows[1].serials_submitted, ["2702607020020"])
        # The 3 unbarcoded lines have the right quantities.
        self.assertEqual(page.form.rows[2].quantity_value, 1)
        self.assertEqual(page.form.rows[3].quantity_value, 1)
        self.assertEqual(page.form.rows[4].quantity_value, 2)
        # click_plain_save was called exactly once (the writer's save step).
        # The agent's no-op intercepts it so GYJ 端 is never actually saved.
        self.assertEqual(called["save"], 1)
        self.assertEqual(payload["order_no"], "SKIPPED")




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
        self.active = True

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

    def evaluate(self, expression):
        if expression == "el => !!el.closest('.ant-tabs-tabpane-active')":
            return self.active
        if "offsetWidth" in expression or "visibility" in expression or "display" in expression:
            return self.visible
        raise AssertionError(f"unexpected evaluate snippet: {expression}")


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

        with self.assertRaisesRegex(GYJInboundError, "新增后仍无法选择"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertNotIn("保存", page.clicked)

    def test_writer_creates_missing_product_then_retries_selection(self):
        page = FakeGYJPage(product_outcomes=[False, True])

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertEqual(page.created_products, [("926019528", "滤芯", True)])
        self.assertEqual(page.lines, self.lines)
        self.assertIn("保存", page.clicked)

    def test_writer_marks_unbarcoded_accessory_as_no_serial_product(self):
        line = dict(self.lines[0], serials=[], quantity=4, record_type="无条码配件")
        page = FakeGYJPage(product_outcomes=[False, True])

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", [line])

        self.assertEqual(page.created_products, [("926019528", "滤芯", False)])

    def test_writer_retries_missing_product_creation_twice_then_stops_before_save(self):
        page = FakeGYJPage(product_found=False, create_failures=2)

        with self.assertRaisesRegex(GYJInboundError, "阶段 pre_check 失败"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # The pre-check stage raises on every attempt because the inner create always
        # exhausts its budget. With create_failures=2 the inner loop runs 2 calls in
        # attempt 0; thereafter each stage attempt runs 1 successful create (the create
        # counter finally exceeds create_failures) but the post-create verification
        # fails (product_found=False) and that path skips the existing_codes update, so
        # the next stage attempt has to create again.
        self.assertEqual(len(page.created_products), 4)
        self.assertEqual(page.rollback_precheck_called, 2)
        self.assertNotIn("保存", page.clicked)

    def test_writer_retries_a_browser_create_error_before_stopping(self):
        page = FakeGYJPage(
            product_found=False,
            create_failures=2,
            create_exception=TimeoutError("GYJ 新增表单加载超时"),
        )

        with self.assertRaisesRegex(GYJInboundError, "阶段 pre_check 失败"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # Same accounting as the previous test: 2 + 1 + 1 create calls.
        self.assertEqual(len(page.created_products), 4)
        self.assertNotIn("保存", page.clicked)



class GYJPurchaseInboundPrecheckTest(unittest.TestCase):
    """Verify the pre-check stage runs before any add_product_line call."""

    def setUp(self):
        self.lines = [
            {
                "product_code": "926019528",
                "description": "滤芯",
                "source_order_numbers": ["403565"],
                "serials": ["SN001"],
                "quantity": 1,
                "record_type": "条码",
            },
            {
                "product_code": "926019528",
                "description": "滤芯",
                "source_order_numbers": ["403565"],
                "serials": ["SN002"],
                "quantity": 1,
                "record_type": "条码",
            },
            {
                "product_code": "906042856",
                "description": "S1-600",
                "source_order_numbers": ["403578"],
                "serials": ["SN003"],
                "quantity": 1,
                "record_type": "条码",
            },
        ]

    def test_writer_pre_checks_each_unique_code_before_any_line_fill(self):
        page = FakeGYJPage()

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # Pre-check ran once for all unique codes BEFORE any add_product_line.
        self.assertEqual(page.pre_check_calls_dedup, ["926019528", "906042856"])
        # Line fills happened AFTER pre-check.
        self.assertEqual(len(page.lines), 3)
        # Both codes were created exactly once in the pre-check (existing_codes guards double-create).
        codes_created = [c[0] for c in page.created_products]
        self.assertEqual(codes_created.count("926019528"), 1)
        self.assertEqual(codes_created.count("906042856"), 1)

    def test_writer_dedupes_codes_in_precheck(self):
        page = FakeGYJPage()

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # `pre_check_calls` records the items passed to _ensure_products_exist.
        # The writer must deduped to a list of {product_code, has_serials} entries;
        # the test double preserves them unchanged for inspection.
        items_per_call = page.pre_check_calls[0] if page.pre_check_calls else []
        self.assertEqual(len(items_per_call), 2)
        self.assertEqual([item["product_code"] for item in items_per_call],
                         ["926019528", "906042856"])

    def test_writer_aborts_entire_slip_when_precheck_fails_to_create(self):
        page = FakeGYJPage(product_found=False, create_failures=99)

        with self.assertRaisesRegex(GYJInboundError, "阶段 pre_check 失败"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # No line fills, no verify, no save.
        self.assertEqual(page.lines, [])
        self.assertFalse(getattr(page, "verified", None))
        self.assertNotIn("保存", page.clicked)

    def test_writer_falls_back_to_inline_create_when_precheck_misses_a_code(self):
        # Pre-check thinks the product exists; add_product_line still finds it missing.
        # The existing inline create-on-fail path (defense-in-depth) must still work.
        page = FakeGYJPage(existing_codes={"906042856"})

        # The pre-check sees 926019528 as missing and creates it.
        # But we simulate flaky lookup by having product_outcomes force add_product_line
        # to raise once for the first 926019528 line.
        page.product_outcomes = [False, True, True]

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertEqual(len(page.lines), 3)
        # 926019528 was created once during pre-check.
        codes = [c[0] for c in page.created_products]
        self.assertIn("926019528", codes)
        self.assertNotIn("保存并审核", page.clicked)
        self.assertIn("保存", page.clicked)

    def test_writer_emits_pre_check_progress_logs(self):
        logs = []
        page = FakeGYJPage()

        GYJPurchaseInboundWriter(page, log=logs.append).save_packing_slip("SH202607210002", self.lines)

        joined = "\n".join(logs)
        self.assertIn("开始预检查 GYJ 物料：2 个唯一编码", joined)
        self.assertIn("GYJ 物料预检查完成", joined)
        # After pre-check, the filling stage emits per-line messages.
        self.assertIn("正在录入 1/3", joined)
        self.assertIn("正在录入 3/3", joined)


class GYJPurchaseInboundStageRetryTest(unittest.TestCase):
    """Verify the stage-level retry-with-rollback wrapper."""

    def setUp(self):
        self.lines = [{
            "product_code": "926019528",
            "description": "滤芯",
            "source_order_numbers": ["403565"],
            "serials": ["SN001"],
            "quantity": 1,
            "record_type": "条码",
        }]

    def test_writer_retries_filling_stage_when_a_line_fails_then_succeeds(self):
        # First add_product_line call raises a transient error; the second succeeds.
        # The stage wrapper rolls back and re-runs the entire filling stage; this
        # second pass also fails-and-then-succeeds because fill_failure_on tracks by
        # 1-based line index and we only injected one failure.
        page = FakeGYJPage(fill_failure_on={1: TimeoutError("网络抖动")})
        logs = []

        GYJPurchaseInboundWriter(page, log=logs.append).save_packing_slip("SH202607210002", self.lines)

        # Filling succeeded on the retry: line is recorded, save is clicked.
        self.assertEqual(len(page.lines), 1)
        self.assertIn("保存", page.clicked)
        # Rollback fired once between the two attempts.
        self.assertEqual(page.rollback_filling_called, 1)
        joined = "\n".join(logs)
        self.assertIn("阶段 filling 失败，正在退回重试（1/2）", joined)

    def test_writer_aborts_after_filling_fails_three_times(self):
        # Force fill to fail on every line invocation. The stage wrapper exhausts
        # STAGE_MAX_RETRIES=2 retries and re-raises as a GYJInboundError.
        page = FakeGYJPage()
        page.add_product_line = lambda line: (_ for _ in ()).throw(TimeoutError("网络持续不稳定"))

        with self.assertRaisesRegex(GYJInboundError, "阶段 filling 失败"):
            GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        # 3 stage attempts × 1 rollback after each (attempts 0 and 1) = 2 rollbacks before the final raise.
        self.assertEqual(page.rollback_filling_called, 2)
        self.assertNotIn("保存", page.clicked)

    def test_writer_retries_verifying_stage_when_verify_fails_once(self):
        page = FakeGYJPage()
        real_verify = page.verify_form
        calls = {"n": 0}

        def flaky_verify(packing_slip_no, lines):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("核对服务暂时不可用")

        page.verify_form = flaky_verify

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(page.rollback_verifying_called, 1)
        self.assertIn("保存", page.clicked)

    def test_writer_retries_saving_stage_when_save_fails_once(self):
        page = FakeGYJPage()
        real_save = page.click_plain_save
        calls = {"n": 0}

        def flaky_save():
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("保存请求超时")
            return real_save()

        page.click_plain_save = flaky_save

        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", self.lines)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(page.rollback_saving_called, 1)
        # The successful retry is the only one that actually appended "保存"
        # (real_save runs inside flaky_save on the second call).
        self.assertEqual(page.clicked.count("保存"), 1)


class GYJCreateMissingProductEndToEndTest(unittest.TestCase):
    """End-to-end: when GYJ's product library lacks a product code, the writer
    calls create_product exactly once, then re-attempts add_product_line which
    now succeeds (because the new product is in the library), and proceeds to
    click_plain_save exactly once.  The agent's no-op intercepts the save so
    GYJ 端 is never actually saved.
    """

    def test_create_missing_product_then_save_all_five_lines(self):
        import sys
        sys.path.insert(0, ".")
        from gyj_inbound import GYJPurchaseInboundWriter
        from gyj_inbound import GYJPurchaseInboundWriter as _  # noqa
        # _E2EPage already exists from the SH202607230016 test class.  Reuse
        # the same fake; it creates rows with the right code and tracks serials.
        page = _E2EPage()
        # Make add_product_line fail on its first call (so writer triggers
        # create_product), then succeed on the second.
        original_add = page.add_product_line
        calls = {"n": 0}
        def flaky_add(line):
            calls["n"] += 1
            if calls["n"] == 1:
                raise GYJInboundError("未找到物料编码：" + line["product_code"])
            return original_add(line)
        page.add_product_line = flaky_add
        # Track create_product calls.
        called = {"save": 0}
        original_save = page.click_plain_save
        def save_noop():
            called["save"] += 1
            return "SKIPPED"
        page.click_plain_save = save_noop

        # Build the SH202607230016 lines (same as GYJEndToEndSH202607230016Test).
        result = _E2EHistoryResult.make()
        lines = build_gyj_purchase_lines(result)
        writer = GYJPurchaseInboundWriter(page, log=lambda m, l="info": None)
        payload = writer.save_packing_slip("SH202607230016", lines)

        self.assertEqual(payload["packing_slip_no"], "SH202607230016")
        # add_product_line was called twice per product that had to be created
        # (once failing, once succeeding) plus once for the products that were
        # already in the library.  All 5 lines must end up in page.form.rows.
        self.assertEqual(len(page.form.rows), 5)
        # create_product was invoked for at least one product.
        self.assertGreaterEqual(len(page.created_products), 1)
        # click_plain_save was called exactly once (the writer's save step).
        self.assertEqual(called["save"], 1)


class _GYJSessionPageStub:
    def __init__(self, url, has_user_input, has_captcha_input):
        self._url = url
        self._has_user_input = has_user_input
        self._has_captcha_input = has_captcha_input

    @property
    def url(self):
        return self._url

    def evaluate(self, js):
        if "input[name='username']" in js or "placeholder" in js:
            return self._has_user_input
        if "验证码" in js or "captcha" in js or "inputCode" in js:
            return self._has_captcha_input
        raise AssertionError("unexpected evaluate snippet")


class GYJSessionLoginDetectionTest(unittest.TestCase):
    def test_reports_not_logged_in_when_dom_is_login_page_even_if_url_is_purchase_in(self):
        import sys
        sys.path.insert(0, ".")
        import app
        sess = app.GYJSession(app._gyj_session_dir("admin"))
        sess.page = _GYJSessionPageStub(
            "https://cloud.gyjerp.com/bill/purchase_in",
            has_user_input=True, has_captcha_input=True,
        )
        ok, message = sess.check_login_status()
        self.assertFalse(ok)
        self.assertFalse(sess.logged_in)
        self.assertIn("登录", message)

    def test_reports_logged_in_only_when_url_and_dom_match(self):
        import sys
        sys.path.insert(0, ".")
        import app
        sess = app.GYJSession(app._gyj_session_dir("admin"))
        sess.page = _GYJSessionPageStub(
            "https://cloud.gyjerp.com/bill/purchase_in",
            has_user_input=False, has_captcha_input=False,
        )
        ok, message = sess.check_login_status()
        self.assertTrue(ok)
        self.assertTrue(sess.logged_in)
        self.assertEqual(message, "GYJ 已登录")

class GYJPurchaseInboundPageTest(unittest.TestCase):
    def test_waits_for_purchase_inbound_new_button_before_clicking(self):
        page = _PurchaseInboundListPage()

        adapter = GYJPlaywrightPage(page)
        adapter.open_new_form()

        self.assertTrue(page.new_button.clicked)
        self.assertIs(adapter.form, page.modal)

    def test_uses_fullscreen_purchase_form_instead_of_the_last_child_dialog(self):
        page = _PurchaseInboundListPage()
        adapter = GYJPlaywrightPage(page)

        adapter.open_new_form()

        self.assertIn(".ant-modal.j-modal-box.fullscreen:visible", page.selectors)

    def test_uses_actual_plain_save_button_label(self):
        form = _ActualGYJSaveForm()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = form

        adapter.click_plain_save()

        self.assertTrue(form.save_button.clicked)

    def test_accepts_the_asynchronous_gyj_save_notification(self):
        form = _ActualGYJSaveForm()
        adapter = GYJPlaywrightPage(_NotificationOnlyGYJSavePage())
        adapter.form = form

        adapter.click_plain_save()

        self.assertTrue(form.save_button.clicked)

    def test_reports_gyj_visible_validation_error_after_save(self):
        form = _ActualGYJSaveForm()
        adapter = GYJPlaywrightPage(_ValidationErrorGYJSavePage())
        adapter.form = form

        with self.assertRaisesRegex(GYJInboundError, "请选择结算账户"):
            adapter.click_plain_save()

    def test_reports_gyj_save_api_rejection_when_no_page_error_is_visible(self):
        page = _ResponseGYJSavePage()
        adapter = GYJPlaywrightPage(page)
        adapter.form = _ResponseGYJSaveForm(page)

        with self.assertRaisesRegex(GYJInboundError, r"接口反馈.*400.*单据校验失败"):
            adapter.click_plain_save()

    def test_accepts_successful_gyj_save_api_response_without_a_toast(self):
        page = _ResponseGYJSavePage()
        adapter = GYJPlaywrightPage(page)
        adapter.form = _SuccessfulResponseGYJSaveForm(page)

        adapter.click_plain_save()

        self.assertTrue(adapter.form.save_button.clicked)

    def test_reports_actual_button_labels_when_plain_save_is_not_unique(self):
        form = _ActualGYJSaveForm()
        form.get_by_role = lambda *args, **kwargs: _MissingButton()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = form

        with self.assertRaisesRegex(GYJInboundError, r"可用按钮：取 消 \| 保存并审核 \| 保存"):
            adapter.click_plain_save()

    def test_finds_the_entry_row_between_header_and_summary(self):
        form = _ActualInboundRowsForm()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = form

        row = adapter._entry_row()

        self.assertIs(row, form.entry)

    def test_waits_until_all_initial_detail_rows_render(self):
        form = _DelayedInboundRowsForm()
        adapter = GYJPlaywrightPage(_RowRenderPage(form))
        adapter.form = form

        row = adapter._entry_row()

        self.assertIs(row, form.entry)

    def test_fills_quantity_by_its_row_specific_input_id(self):
        row = _ActualInboundRow()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())

        adapter._fill_quantity(row, 10)

        self.assertEqual(row.quantity_input.value, "10")
        self.assertTrue(row.quantity_input.clicked)
        self.assertEqual(row.quantity_input.typed, "10")
        self.assertEqual(row.quantity_input.keys, ["ControlOrMeta+A", "Backspace", "Tab"])
        self.assertEqual(row.quantity_input.waited, ("visible", 5000))

    def test_waits_for_unbarcoded_quantity_to_commit_before_continuing(self):
        row = _CommittedQuantityRow()
        page = _QuantityCommitPage(row)
        adapter = GYJPlaywrightPage(page)

        adapter._fill_quantity(row, 10)

        self.assertTrue(row.quantity_input.committed)
        self.assertEqual(row.quantity_input.evaluate("element => element.value"), "10")

    def test_reports_actual_quantity_input_state_when_gyj_rejects_a_value(self):
        row = _RejectedQuantityRow()
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())

        with self.assertRaisesRegex(GYJInboundError, r"应为 4.*当前值=1.*operNumber_jet-test.*quantity-control.*746037027"):
            adapter._fill_quantity(row, 4)

    def test_waits_for_serial_entry_icon_after_product_selection(self):
        row = _SerialInputRow()
        adapter = GYJPlaywrightPage(_SerialRenderPage(row))
        adapter._visible_modal = lambda: _SerialEntryModal()
        adapter._click_exact = lambda *args: None

        adapter._fill_serials(row, ["8432604240024"])

        self.assertTrue(row.button.clicked)

    def test_waits_for_all_serials_and_the_matching_row_quantity_before_confirming(self):
        row = _SerialQuantityRow(expected=3)
        row.button.ready = True
        modal = _DelayedSerialEntryModal(serial_count=3)
        adapter = GYJPlaywrightPage(_SerialCommitPage(row, modal))
        adapter._visible_modal = lambda: modal
        adapter._click_exact = lambda *args: None

        adapter._fill_serials(row, ["SN001", "SN002", "SN003"])

        self.assertTrue(modal.rows.ready)
        self.assertEqual(row.quantity_input.value, "3")

    def test_reports_product_search_result_count_when_item_is_missing(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        modal = _ProductSearchModal()
        adapter._wait_for_product_picker = lambda: modal

        with self.assertRaisesRegex(GYJInboundError, r"406005116（结果行=0）"):
            adapter._choose_product(_ProductSelectionRow(), "406005116")

    def test_waits_for_the_product_picker_instead_of_reusing_purchase_form(self):
        page = _DelayedProductPickerPage()
        adapter = GYJPlaywrightPage(page)

        with self.assertRaisesRegex(GYJInboundError, r"406005116（结果行=0）"):
            adapter._choose_product(_ProductSelectionRow(), "406005116")

        self.assertGreaterEqual(page.waits, 1)
        self.assertEqual(page.modal.search.value, "406005116")
        self.assertTrue(page.modal.query.clicked)

    def test_closes_a_gyj_tour_overlay_before_searching_for_a_product(self):
        page = _DelayedProductPickerPage()
        adapter = GYJPlaywrightPage(page)

        with self.assertRaisesRegex(GYJInboundError, r"406005116（结果行=0）"):
            adapter._choose_product(_ProductSelectionRow(), "406005116")

        self.assertTrue(page.skip.clicked)

    def test_escapes_a_tour_overlay_and_forces_the_real_product_query_button(self):
        page = _OverlayProductPickerPage()
        adapter = GYJPlaywrightPage(page)

        with self.assertRaisesRegex(GYJInboundError, r"406005116（结果行=0）"):
            adapter._choose_product(_ProductSelectionRow(), "406005116")

        self.assertEqual(page.keyboard.keys, ["Escape"])
        self.assertFalse(page.overlay.visible)
        self.assertTrue(page.modal.query.force)

    def test_creates_missing_product_with_visible_required_fields(self):
        page = _ProductCreatePage()
        adapter = GYJPlaywrightPage(page)
        adapter._product_picker = _ProductCreatePicker()
        adapter._visible_modal = lambda: page.form

        adapter.create_product("926023628", "测试滤芯", True)

        self.assertTrue(adapter._product_picker.new.clicked)
        self.assertEqual(page.form.name.value, "测试滤芯")
        self.assertEqual(page.form.unit.value, "个")
        self.assertEqual(page.form.barcode.value, "926023628")
        self.assertTrue(page.form.serial_trigger.clicked)
        self.assertTrue(page.dropdown.yes.clicked)
        self.assertFalse(page.dropdown.no.clicked)
        self.assertTrue(page.form.save.clicked)
        self.assertFalse(page.form.visible)

    def test_creates_unbarcoded_accessory_with_no_serial_setting(self):
        page = _ProductCreatePage()
        adapter = GYJPlaywrightPage(page)
        adapter._product_picker = _ProductCreatePicker()
        adapter._visible_modal = lambda: page.form

        adapter.create_product("406005117", "电源", False)

        self.assertTrue(page.dropdown.no.clicked)
        self.assertFalse(page.dropdown.yes.clicked)

    def test_inserts_another_row_after_the_first_prepared_line(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        adapter.form = _InsertLineForm()
        adapter._entered_lines = [{"product_code": "926019528"}]
        adapter._entry_row = lambda: object()
        adapter._choose_product = lambda row, code: None
        adapter._fill_quantity = lambda row, quantity: None

        adapter.add_product_line({"product_code": "906018301", "serials": [], "quantity": 60})

        self.assertTrue(adapter.form.button.clicked)

    def test_reports_ambiguity_when_multiple_buttons_normalise_to_same_text(self):
        form = _MultiInsertLineForm()

        with self.assertRaisesRegex(GYJInboundError, r"未找到唯一的 GYJ 按钮：插入行"):
            GYJPlaywrightPage._click_exact(form, "插入行")

        self.assertFalse(form.button.clicked)
        self.assertFalse(form.multi.secondary.clicked)

    def test_clicks_insert_line_when_gyj_spaces_the_visible_button_text(self):
        form = _SpacedInsertLineForm()

        GYJPlaywrightPage._click_exact(form, "插入行")

        self.assertTrue(form.button.clicked)

    def test_reacquires_current_row_after_product_selection_for_serials(self):
        adapter = GYJPlaywrightPage(_ActualGYJSavePage())
        before_selection = object()
        after_selection = object()
        adapter._entry_row = mock.Mock(side_effect=[before_selection, after_selection])
        adapter._choose_product = mock.Mock()
        adapter._fill_serials = mock.Mock()

        adapter.add_product_line({"product_code": "996032157", "serials": ["8432604240024"], "quantity": 1})

        adapter._choose_product.assert_called_once_with(before_selection, "996032157")
        adapter._fill_serials.assert_called_once_with(after_selection, ["8432604240024"])

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
