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
    return sorted(lines, key=lambda line: 0 if line["serials"] else 1)


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

    def _visible_purchase_form(self):
        form = self.page.locator(".ant-modal.j-modal-box.fullscreen:visible")
        if form.count() != 1:
            raise GYJInboundError("未找到 GYJ 采购入库主表单")
        return form

    def _dismiss_intro_tour(self):
        try:
            skip = self.page.locator(".introjs-skipbutton:visible")
            if skip.count():
                skip.last.click()
        except Exception:
            pass

    @staticmethod
    def _supplier_control_state(trigger):
        try:
            state = trigger.evaluate(
                "element => { const box = element.getBoundingClientRect(); "
                "const point = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2); "
                "const owner = element.closest('.ant-select'); return {"
                "role: element.getAttribute('role') || '', "
                "visible: !!(element.offsetWidth || element.offsetHeight), "
                "tabindex: element.getAttribute('tabindex') || '', "
                "owner_class: owner ? owner.className || '' : '', "
                "point_class: point ? point.className || point.tagName || '' : ''} }"
            ) or {}
            return {
                "role": state.get("role") or "无",
                "visible": state.get("visible"),
                "tabindex": state.get("tabindex") or "无",
                "owner_class": state.get("owner_class") or "无",
                "point_class": state.get("point_class") or "无",
            }
        except Exception:
            return {
                "role": "未知", "visible": "未知", "tabindex": "未知",
                "owner_class": "未知", "point_class": "未知",
            }

    @staticmethod
    def _supplier_control_state_text(state):
        return (
            f"控件角色={state['role']}，控件可见={state['visible']}，"
            f"控件tabindex={state['tabindex']}，外层类={state['owner_class']}，"
            f"点击点类={state['point_class']}"
        )

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
        self.form = self._visible_purchase_form()

    def select_header(self, label, value):
        if not self.form:
            raise GYJInboundError("GYJ 入库表单尚未打开")
        self._dismiss_intro_tour()
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
        trigger.click(force=True)
        search_input = trigger.locator("input").first
        if search_input.count() != 1:
            search_input = field.locator("input").first
        if label == "供应商" and search_input.count() == 1:
            search_input.focus()
            try:
                search_input.fill(value, timeout=10000)
            except Exception as error:
                raise GYJInboundError(
                    "GYJ 供应商输入框未打开（"
                    + self._supplier_control_state_text(self._supplier_control_state(trigger))
                    + "）"
                ) from error
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
            control_state = self._supplier_control_state(trigger)
            raise GYJInboundError(
                f"GYJ 供应商候选未出现（控件展开={expanded}，输入框可见={input_visible}，"
                f"候选层ID={dropdown_id or '无'}，{self._supplier_control_state_text(control_state)}）"
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
        for _ in range(50):
            if rows.count() >= 3:
                break
            self.page.wait_for_timeout(100)
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
            raise GYJInboundError(
                f"未找到唯一的 GYJ 物料编码：{product_code}（结果行={result_rows.count()}）"
            )
        checkbox = result_rows.first.locator('input[type="checkbox"]')
        if checkbox.count() != 1:
            raise GYJInboundError(f"GYJ 物料 {product_code} 没有可选项")
        checkbox.check()
        self._click_exact(modal, "确 定")

    def _fill_serials(self, row, serials):
        serial_button = row.locator(".ant-input-search-icon")
        for _ in range(50):
            if serial_button.count() == 1:
                break
            self.page.wait_for_timeout(100)
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
        serial_rows = modal.locator(".ant-table-tbody > tr")
        for _ in range(100):
            if serial_rows.count() >= len(serials):
                break
            self.page.wait_for_timeout(100)
        if serial_rows.count() < len(serials):
            raise GYJInboundError(
                f"GYJ 序列号批量添加未完成（已录入 {serial_rows.count()} / {len(serials)}）"
            )
        self._click_exact(modal, "确 定")
        quantity_input = row.locator('input[id^="operNumber_"]')
        for _ in range(100):
            if quantity_input.count() == 1:
                try:
                    if str(quantity_input.evaluate("element => element.value") or "").strip() == str(len(serials)):
                        return
                except Exception:
                    pass
            self.page.wait_for_timeout(100)
        raise GYJInboundError(
            f"GYJ 序列号数量未回写（应为 {len(serials)}）"
        )

    def _fill_quantity(self, row, quantity):
        quantity_input = row.locator('input[id^="operNumber_"]')
        try:
            quantity_input.wait_for(state="visible", timeout=5000)
        except Exception as error:
            raise GYJInboundError("未找到 GYJ 无条码数量输入框") from error
        if quantity_input.count() != 1:
            raise GYJInboundError("未找到 GYJ 无条码数量输入框")
        quantity_input.evaluate(
            "(element, value) => {"
            "const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
            "setter.call(element, String(value));"
            "element.dispatchEvent(new Event('input', {bubbles: true}));"
            "element.dispatchEvent(new Event('change', {bubbles: true}));"
            "element.dispatchEvent(new FocusEvent('blur', {bubbles: true}));"
            "}",
            str(quantity),
        )
        quantity_input.press("Tab")
        self.page.wait_for_timeout(200)
        current_value = ""
        for _ in range(50):
            try:
                current_value = str(quantity_input.evaluate("element => element.value") or "").strip()
                if current_value == str(quantity):
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(100)
        try:
            state = quantity_input.evaluate(
                "element => ({value: element.value || '', id: element.id || '', type: element.type || '', "
                "readOnly: !!element.readOnly, disabled: !!element.disabled, className: element.className || ''})"
            ) or {}
        except Exception:
            state = {}
        details = f"当前值={state.get('value', current_value) or '空'}"
        if state.get("id"):
            details += f"，输入框={state['id']}"
        details += f"，只读={bool(state.get('readOnly'))}，禁用={bool(state.get('disabled'))}"
        if state.get("className"):
            details += f"，类={state['className']}"
        try:
            row_text = " ".join(str(row.inner_text() or "").split())[:240]
        except Exception:
            row_text = ""
        try:
            row_inputs = row.locator("input").evaluate_all(
                "elements => elements.map(element => ({id: element.id || '', value: element.value || ''}))"
            ) or []
        except Exception:
            row_inputs = []
        if row_text:
            details += f"，行内容={row_text}"
        if row_inputs:
            details += "，行输入=" + " | ".join(
                f"{item.get('id', '无ID')}={item.get('value', '')}" for item in row_inputs
            )
        raise GYJInboundError(f"GYJ 无条码配件数量未回写（应为 {quantity}，{details}）")

    def add_product_line(self, line):
        if self._entered_lines:
            self._click_exact(self.form, "插入行")
        row = self._entry_row()
        self._choose_product(row, line["product_code"])
        row = self._entry_row()
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
        save = self.form.get_by_role("button", name="保存（Ctrl+S）", exact=True)
        if save.count() != 1:
            try:
                labels = [text.strip() for text in self.form.locator("button").all_inner_texts()]
            except Exception:
                labels = []
            suffix = f"（可用按钮：{' | '.join(label for label in labels if label)}）" if labels else ""
            raise GYJInboundError(f"未找到唯一的 GYJ 普通保存按钮{suffix}")
        responses = []

        def capture_response(response):
            url = str(getattr(response, "url", "")).split("?", 1)[0]
            if "/jshERP-boot/" not in url:
                return
            try:
                message = " ".join(str(response.text() or "").split())[:240]
            except Exception:
                message = ""
            responses.append(f"{getattr(response, 'status', '未知')} {url.rsplit('/', 1)[-1]} {message}".strip())

        can_capture = callable(getattr(self.page, "on", None))
        if can_capture:
            self.page.on("response", capture_response)
        try:
            save.click()
            for _ in range(100):
                notice_text = "\n".join(self.page.locator(
                    ".ant-message-notice-content, .ant-notification-notice-message, "
                    ".ant-notification-notice-description"
                ).all_inner_texts())
                page_text = self.page.locator("body").inner_text()
                confirmation = f"{notice_text}\n{page_text}"
                if "保存成功" in confirmation or "操作成功" in confirmation:
                    return ""
                self.page.wait_for_timeout(100)
        finally:
            if can_capture and callable(getattr(self.page, "remove_listener", None)):
                self.page.remove_listener("response", capture_response)
        feedback = [text.strip() for text in self.page.locator(
            ".ant-form-explain:visible, .ant-message-error:visible, "
            ".ant-notification-notice-description:visible"
        ).all_inner_texts() if text.strip()]
        if feedback:
            raise GYJInboundError(f"GYJ 保存被拒绝：{' | '.join(feedback)}")
        if responses:
            raise GYJInboundError(f"GYJ 保存接口反馈：{' | '.join(responses[-3:])}")
        raise GYJInboundError("GYJ 未确认采购入库单保存成功")


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
