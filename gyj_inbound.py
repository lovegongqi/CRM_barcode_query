class GYJInboundError(RuntimeError):
    pass


MAX_SERIALS_PER_LINE = 100
MAX_SERIAL_TEXT_LENGTH = 2000
GYJ_SUPPLIER = "昆山怡口净水"
GYJ_SETTLEMENT_ACCOUNT = "江西天麓"
GYJ_WAREHOUSE = "沈桥仓"
GYJ_PURCHASE_IN_URL = "https://cloud.gyjerp.com/bill/purchase_in"


def build_gyj_purchase_lines(result):
    result = result or {}
    if result.get("duplicate_serials"):
        raise GYJInboundError("存在重复条码，不能创建 GYJ 入库单")

    lines = []
    for item in result.get("items") or []:
        product_code = str(item.get("product_code") or "").strip()
        if not product_code:
            raise GYJInboundError("存在缺少物料编码的明细")
        if item.get("quantity_mismatch"):
            raise GYJInboundError(f"物料 {product_code} 数量不一致，不能创建 GYJ 入库单")

        description = str(item.get("description") or "").strip()
        order_numbers = list(item.get("order_numbers") or [])
        serials = [str(serial).strip() for serial in item.get("serials") or [] if str(serial).strip()]
        for serial_chunk in _serial_chunks(serials):
            lines.append({
                "product_code": product_code,
                "description": description,
                "source_order_numbers": order_numbers,
                "serials": serial_chunk,
                "quantity": len(serial_chunk),
                "record_type": "条码",
            })

        unbarcoded_quantity = _positive_int(item.get("unbarcoded_quantity"))
        if unbarcoded_quantity:
            lines.append({
                "product_code": product_code,
                "description": description,
                "source_order_numbers": order_numbers,
                "serials": [],
                "quantity": unbarcoded_quantity,
                "record_type": "无条码配件",
            })

    if not lines:
        raise GYJInboundError("没有可创建 GYJ 入库单的明细")
    return lines


class GYJPurchaseInboundWriter:
    def __init__(self, page, log=None, progress=None):
        self.page = page
        self.log = log
        self.progress = progress

    def _emit(self, message):
        if self.log:
            self.log(message)

    def save_packing_slip(self, packing_slip_no, lines):
        if not lines:
            raise GYJInboundError("没有可保存的 GYJ 入库明细")
        self._emit("正在新建 GYJ 采购入库单")
        self.page.open_new_form()
        self.page.select_header("供应商", GYJ_SUPPLIER)
        self.page.fill_remark(f"装箱单号：{packing_slip_no}")
        for index, line in enumerate(lines, start=1):
            self._emit(f"正在录入 {index}/{len(lines)}：{line['product_code']}")
            self.page.add_product_line(line)
            if self.progress:
                self.progress({"current_line": index, "total_lines": len(lines)})
        self._emit("正在核对 GYJ 采购入库单")
        self.page.verify_form(packing_slip_no, lines)
        self._emit("核对通过，正在保存 GYJ 采购入库单")
        order_no = self.page.click_plain_save()
        self._emit("GYJ 采购入库单已保存")
        return {"packing_slip_no": packing_slip_no, "order_no": order_no or ""}


class GYJPlaywrightPage:
    """GYJ 采购入库页的最小可见界面适配器。

    该类只操作当前可见的采购入库表单，不读取或持久化浏览器的登录资料。
    """
    def __init__(self, page):
        self.page = page
        self.form = None
        self._headers = {}
        self._entered_lines = []

    def _visible_modal(self):
        modal = self.page.locator(".ant-modal:visible")
        if modal.count() < 1:
            raise GYJInboundError("未找到 GYJ 入库表单")
        return modal.last

    @staticmethod
    def _click_exact(scope, text):
        button = scope.get_by_text(text, exact=True)
        if button.count() != 1:
            raise GYJInboundError(f"未找到唯一的 GYJ 按钮：{text}")
        button.click()

    def open_new_form(self):
        self.page.goto(GYJ_PURCHASE_IN_URL, wait_until="domcontentloaded", timeout=60000)
        new_button = self.page.locator(".table-operator button.ant-btn-primary")
        new_button.wait_for(state="visible", timeout=15000)
        if new_button.count() != 1:
            raise GYJInboundError("未找到 GYJ 采购入库的新增按钮")
        new_button.click()
        self.form = self._visible_modal()

    def select_header(self, label, value):
        if not self.form:
            raise GYJInboundError("GYJ 入库表单尚未打开")
        if label == "仓库":
            warehouse = self.form.locator(
                '[id^="depotId_"] .ant-select-selection-selected-value'
            )
            if warehouse.count() != 1 or warehouse.inner_text().strip() != value:
                raise GYJInboundError(f"GYJ 明细行仓库不是：{value}")
            self._headers[label] = value
            return
        field = self.form.locator(".ant-form-item").filter(has_text=label)
        if field.count() != 1:
            raise GYJInboundError(f"未找到 GYJ 表头字段：{label}")
        field = field.first
        try:
            field_value = field.inner_text().strip()
        except Exception:
            field_value = ""
        if label == "供应商" and value and value in field_value:
            self._headers[label] = value
            return
        trigger = field.locator(".ant-select-selector").first
        if trigger.count() != 1:
            trigger = field.locator(".ant-select-selection").first
        if trigger.count() != 1:
            raise GYJInboundError(f"GYJ 表头字段不是可选项：{label}")
        selected = trigger.locator(
            ".ant-select-selection-selected-value, .ant-select-selection-item"
        ).first
        selected_value = selected.inner_text().strip() if selected.count() == 1 else ""
        if selected_value == value or (
            label == "供应商" and value and value in selected_value
        ):
            self._headers[label] = value
            return
        trigger.focus()
        trigger.press("ArrowDown")
        search_input = trigger.locator("input").first
        if search_input.count() != 1:
            search_input = field.locator("input").first
        if label == "供应商" and search_input.count() == 1:
            search_input.focus()
            search_input.fill(value, force=True)
        dropdown_id = trigger.get_attribute("aria-controls") or ""
        dropdown = self.page.locator(f'[id="{dropdown_id}"]') if dropdown_id else self.page.locator(
            ".ant-select-dropdown"
        )
        try:
            dropdown.wait_for(state="attached", timeout=5000)
        except Exception as error:
            try:
                expanded = trigger.get_attribute("aria-expanded") or "未知"
            except Exception:
                expanded = "未知"
            try:
                input_visible = search_input.is_visible()
            except Exception:
                input_visible = "未知"
            try:
                control_state = trigger.evaluate(
                    "element => ({role: element.getAttribute('role') || '', "
                    "class_name: element.className || '', visible: !!(element.offsetWidth || element.offsetHeight)})"
                ) or {}
                control_role = control_state.get("role") or "无"
                control_visible = control_state.get("visible")
            except Exception:
                control_role = "未知"
                control_visible = "未知"
            raise GYJInboundError(
                f"GYJ 供应商候选未出现（控件展开={expanded}，输入框可见={input_visible}，"
                f"候选层ID={dropdown_id or '无'}，控件角色={control_role}，控件可见={control_visible}）"
            ) from error
        if dropdown.count() < 1:
            raise GYJInboundError(f"未打开 GYJ {label} 下拉列表")
        choice = dropdown.last.get_by_text(value, exact=True)
        if choice.count() != 1:
            raise GYJInboundError(f"未找到 GYJ {label}：{value}")
        choice.click(force=True)
        self._headers[label] = value

    def fill_remark(self, value):
        if not self.form:
            raise GYJInboundError("GYJ 入库表单尚未打开")
        remark = self.form.locator('textarea[placeholder="请输入备注"]:visible')
        if remark.count() != 1:
            raise GYJInboundError("未找到 GYJ 入库备注输入框")
        remark.fill(value)

    def _entry_row(self):
        if not self.form:
            raise GYJInboundError("GYJ 入库表单尚未打开")
        rows = self.form.locator(".tr")
        if rows.count() < 3:
            raise GYJInboundError("未找到 GYJ 入库明细行")
        return rows.nth(rows.count() - 2)

    def _choose_product(self, row, product_code):
        product_button = row.locator("button.ant-btn.ant-btn-icon-only")
        if product_button.count() < 1:
            raise GYJInboundError("未找到 GYJ 物料选择按钮")
        product_button.first.click()
        modal = self._visible_modal()
        search = modal.locator("input").filter(has_not=self.page.locator("[disabled]"))
        if search.count() < 1:
            raise GYJInboundError("未找到 GYJ 物料搜索框")
        search.first.fill(product_code)
        query = modal.get_by_text("查 询", exact=True)
        if query.count() != 1:
            query = modal.get_by_text("查询", exact=True)
        if query.count() != 1:
            raise GYJInboundError("未找到 GYJ 物料查询按钮")
        query.click()
        self.page.wait_for_timeout(800)
        result_rows = modal.locator("tr").filter(has_text=product_code)
        if result_rows.count() != 1:
            raise GYJInboundError(f"未找到唯一的 GYJ 物料编码：{product_code}")
        checkbox = result_rows.first.locator('input[type="checkbox"]')
        if checkbox.count() != 1:
            raise GYJInboundError(f"GYJ 物料 {product_code} 没有可选项")
        checkbox.check()
        self._click_exact(modal, "确 定")

    def _fill_serials(self, row, serials):
        serial_button = row.locator(".ant-input-search-icon")
        if serial_button.count() != 1:
            raise GYJInboundError("未找到 GYJ 序列号录入按钮")
        serial_button.click()
        modal = self._visible_modal()
        self._click_exact(modal, "多个序列号")
        serial_input = modal.locator(
            'textarea[placeholder="多个序列号用逗号隔开，请少于2000个字符"]'
        )
        if serial_input.count() != 1:
            raise GYJInboundError("未找到 GYJ 多个序列号输入框")
        serial_input.fill(",".join(serials))
        self._click_exact(modal, "批量添加")
        self._click_exact(modal, "确 定")

    def _fill_quantity(self, row, quantity):
        quantity_input = row.locator('input[id^="operNumber_"]')
        if quantity_input.count() != 1:
            raise GYJInboundError("未找到 GYJ 无条码数量输入框")
        quantity_input.fill(str(quantity))
        quantity_input.press("Tab")

    def add_product_line(self, line):
        if self._entered_lines:
            self._click_exact(self.form, "插入行")
        row = self._entry_row()
        self._choose_product(row, line["product_code"])
        if line.get("serials"):
            self._fill_serials(row, line["serials"])
        else:
            self._fill_quantity(row, line["quantity"])
        self._entered_lines.append(dict(line))

    def verify_form(self, packing_slip_no, lines):
        required = {
            "供应商": GYJ_SUPPLIER,
        }
        if self._headers != required:
            raise GYJInboundError("GYJ 表头核对失败")
        if self._entered_lines != list(lines):
            raise GYJInboundError("GYJ 明细核对失败")
        if not str(packing_slip_no or "").strip():
            raise GYJInboundError("装箱单号不能为空")

    def click_plain_save(self):
        if not self.form:
            raise GYJInboundError("GYJ 入库表单尚未打开")
        save = self.form.get_by_text("保存（Ctrl+S）", exact=True)
        if save.count() != 1:
            raise GYJInboundError("未找到唯一的 GYJ 普通保存按钮")
        save.click()
        self.page.wait_for_timeout(800)
        page_text = self.page.locator("body").inner_text()
        if "保存成功" not in page_text and "操作成功" not in page_text:
            raise GYJInboundError("GYJ 未确认采购入库单保存成功")
        return ""


def _serial_chunks(serials):
    chunks = []
    current = []
    for serial in serials:
        candidate = current + [serial]
        if current and (
            len(candidate) > MAX_SERIALS_PER_LINE
            or len(",".join(candidate)) > MAX_SERIAL_TEXT_LENGTH
        ):
            chunks.append(current)
            current = [serial]
        else:
            current = candidate
        if len(",".join(current)) > MAX_SERIAL_TEXT_LENGTH:
            raise GYJInboundError(f"条码 {serial} 超过 GYJ 单行字符限制")
    if current:
        chunks.append(current)
    return chunks


def _positive_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        raise GYJInboundError("无条码配件数量无效")
