import re
import time

from inbound_extraction import normalize_packing_slip_no


class PackingSlipReadError(RuntimeError):
    pass


FIELD_ALIASES = {
    "order_number": ("订单号", "销售订单号"),
    "product_code": ("物料编码", "产品编码", "商品编码", "物料代码"),
    "description": ("物料描述", "产品描述", "商品描述", "品名", "名称"),
    "expected_quantity": ("应发数量", "数量", "出货数量", "发货数量"),
    "serial": ("条码", "序列号", "产品条码", "SN"),
}
MAPPED_FIELDS = tuple(FIELD_ALIASES)
SUMMARY_LABELS = ("合计", "汇总", "总计")


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _header_text(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def _header_indexes(headers):
    normalized = [_header_text(header) for header in headers]
    indexes = {}
    for field, aliases in FIELD_ALIASES.items():
        accepted = {_header_text(alias) for alias in aliases}
        indexes[field] = next(
            (index for index, header in enumerate(normalized) if header in accepted),
            None,
        )
    return indexes


def map_table_rows(headers, rows, page_number):
    headers = list(headers or [])
    indexes = _header_indexes(headers)
    missing = []
    if indexes["product_code"] is None:
        missing.append("物料编码")
    if indexes["serial"] is None:
        missing.append("条码")
    if missing:
        raise PackingSlipReadError(f"明细表缺少核心表头：{'\u3001'.join(missing)}")

    normalized_headers = [_header_text(header) for header in headers]
    mapped_rows = []
    for row_index, raw_row in enumerate(rows or [], start=1):
        cells = [_clean_text(value) for value in list(raw_row or [])]
        if not any(cells):
            continue
        normalized_cells = [_header_text(value) for value in cells]
        if normalized_cells[:len(normalized_headers)] == normalized_headers:
            continue
        if any(cell in SUMMARY_LABELS for cell in cells):
            continue

        mapped = {}
        for field in MAPPED_FIELDS:
            index = indexes[field]
            mapped[field] = cells[index] if index is not None and index < len(cells) else ""
        if not mapped["product_code"] or not mapped["serial"]:
            continue
        mapped_rows.append({"page": page_number, "row_index": row_index, **mapped})
    return mapped_rows


def _rows_fingerprint(rows):
    return tuple(
        tuple(_clean_text(row.get(field)) for field in MAPPED_FIELDS)
        for row in rows
    )


class PackingSlipCRMReader:
    def __init__(self, session, log=None, progress=None):
        self.session = session
        self.log = log
        self.progress = progress

    def extract(self, packing_slip_no):
        packing_slip_no = normalize_packing_slip_no(packing_slip_no)
        self._emit("正在打开 B2B 装箱单页面")
        self._navigate_to_packing_slips()
        self._emit(f"正在查询装箱单 {packing_slip_no}")
        self._search_and_open(packing_slip_no)
        return self._read_all_pages()

    def _emit(self, message):
        if self.log:
            self.log(message)

    def _read_all_pages(self):
        self._go_to_first_page()
        total_pages = self._total_pages()
        expected_page = 1
        rows = []
        page_counts = []
        fingerprints = set()

        while True:
            actual_page = self._current_page_number()
            if actual_page is None:
                raise PackingSlipReadError("无法识别当前页码")
            if actual_page != expected_page:
                raise PackingSlipReadError(
                    f"分页跳号：期望第 {expected_page} 页，实际第 {actual_page} 页"
                )

            page_rows = self._read_current_page()
            fingerprint = _rows_fingerprint(page_rows)
            if fingerprint in fingerprints:
                raise PackingSlipReadError(f"页面重复：第 {actual_page} 页内容已读取")
            fingerprints.add(fingerprint)

            entry = {"page": actual_page, "row_count": len(page_rows)}
            page_counts.append(entry)
            rows.extend(page_rows)
            if self.progress:
                self.progress(dict(entry))

            if total_pages is not None and actual_page >= total_pages:
                break
            if not self._has_next_page():
                if total_pages is not None:
                    raise PackingSlipReadError(
                        f"未读完总页数：已读第 {actual_page} 页，共 {total_pages} 页"
                    )
                break

            expected_page = actual_page + 1
            self._advance_to_page(expected_page)
            self._wait_for_page(expected_page, fingerprint)

        pages_read = [entry["page"] for entry in page_counts]
        last_page = pages_read[-1] if pages_read else 0
        if pages_read != list(range(1, last_page + 1)):
            raise PackingSlipReadError("已读页码不连续")
        if total_pages is not None and last_page != total_pages:
            raise PackingSlipReadError(
                f"未读完总页数：已读 {last_page} 页，共 {total_pages} 页"
            )
        return {"rows": rows, "page_counts": page_counts}

    def _navigate_to_packing_slips(self):
        if not self._click_exact_text("B2B订单管理"):
            raise PackingSlipReadError("未找到 B2B订单管理")
        self._pause()
        if not self._click_exact_text("装箱单"):
            raise PackingSlipReadError("未找到装箱单菜单")
        self._pause()

    def _search_and_open(self, packing_slip_no):
        field = self._find_labeled_input("装箱单号")
        if field is None:
            raise PackingSlipReadError("未找到装箱单号输入框")
        field.fill(packing_slip_no)
        if not (self._click_exact_text("查询") or self._click_exact_text("搜索")):
            raise PackingSlipReadError("未找到查询按钮")
        self._pause()
        if not self._open_matching_result(packing_slip_no):
            raise PackingSlipReadError(f"未找到完全匹配的装箱单：{packing_slip_no}")
        self._pause()

    def _scopes(self):
        if self.session is None:
            return []
        contexts = getattr(self.session, "_page_contexts", None)
        if callable(contexts):
            return list(contexts())
        page = getattr(self.session, "page", None)
        context = getattr(self.session, "context", None)
        pages = list(getattr(context, "pages", []) or [])
        if page is not None and page not in pages:
            pages.append(page)
        result = []
        for candidate in reversed(pages):
            result.append((candidate, candidate))
            for frame in getattr(candidate, "frames", []) or []:
                if frame is not getattr(candidate, "main_frame", None):
                    result.append((candidate, frame))
        return result

    def _pause(self, milliseconds=300):
        page = getattr(self.session, "page", None) if self.session else None
        if page is not None and hasattr(page, "wait_for_timeout"):
            page.wait_for_timeout(milliseconds)
        else:
            time.sleep(milliseconds / 1000)

    @staticmethod
    def _visible(element):
        try:
            return bool(element and element.is_visible())
        except Exception:
            return False

    def _click_exact_text(self, text):
        wanted = _header_text(text)
        for page, scope in self._scopes():
            try:
                elements = scope.query_selector_all(
                    "a, button, [role='button'], [role='menuitem'], li, span"
                )
            except Exception:
                continue
            for element in elements:
                try:
                    if self._visible(element) and _header_text(element.inner_text()) == wanted:
                        if hasattr(page, "bring_to_front"):
                            page.bring_to_front()
                        if self.session is not None:
                            self.session.page = page
                        element.click()
                        return True
                except Exception:
                    continue
        return False

    def _find_labeled_input(self, label):
        script = """(wanted) => {
            const clean = value => (value || '').replace(/\\s+/g, '').trim();
            const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            for (const node of document.querySelectorAll('label')) {
                if (!visible(node) || !clean(node.textContent).includes(clean(wanted))) continue;
                const linked = node.htmlFor ? document.getElementById(node.htmlFor) : node.querySelector('input');
                if (visible(linked)) return linked;
                const container = node.closest('div,td,form') || node.parentElement;
                const nearby = container && container.querySelector('input');
                if (visible(nearby)) return nearby;
            }
            return Array.from(document.querySelectorAll('input')).find(input => {
                const hint = [input.placeholder, input.name, input.id, input.getAttribute('aria-label')].join('');
                return visible(input) && clean(hint).includes(clean(wanted));
            }) || null;
        }"""
        for page, scope in self._scopes():
            try:
                handle = scope.evaluate_handle(script, label)
                element = handle.as_element()
                if self._visible(element):
                    if self.session is not None:
                        self.session.page = page
                    return element
            except Exception:
                continue
        return None

    def _open_matching_result(self, packing_slip_no):
        for page, scope in self._scopes():
            try:
                tables = scope.query_selector_all("table")
            except Exception:
                continue
            for table in tables:
                if not self._visible(table):
                    continue
                for row in table.query_selector_all("tr"):
                    cells = row.query_selector_all("th,td")
                    exact_cells = [cell for cell in cells if _clean_text(cell.inner_text()) == packing_slip_no]
                    if not exact_cells:
                        continue
                    for action in row.query_selector_all("a,button,[role='button']"):
                        if self._visible(action) and _header_text(action.inner_text()) in {"明细", "查看"}:
                            action.click()
                            if self.session is not None:
                                self.session.page = page
                            return True
                    exact_cells[0].click()
                    if self.session is not None:
                        self.session.page = page
                    return True
        return False

    @staticmethod
    def _table_values(table):
        headers = [
            _clean_text(cell.inner_text())
            for cell in table.query_selector_all("thead th")
        ]
        rows = []
        body_rows = table.query_selector_all("tbody tr")
        if not headers:
            all_rows = table.query_selector_all("tr")
            for row in all_rows:
                header_cells = row.query_selector_all("th")
                if header_cells:
                    headers = [_clean_text(cell.inner_text()) for cell in header_cells]
                    break
        for row in body_rows or table.query_selector_all("tr"):
            cells = row.query_selector_all("td")
            if cells:
                rows.append([_clean_text(cell.inner_text()) for cell in cells])
        return headers, rows

    def _read_current_page(self):
        page_number = self._current_page_number()
        if page_number is None:
            raise PackingSlipReadError("无法识别当前页码")
        candidates = []
        for _page, scope in self._scopes():
            try:
                tables = scope.query_selector_all("table")
            except Exception:
                continue
            for table in tables:
                if not self._visible(table):
                    continue
                headers, raw_rows = self._table_values(table)
                try:
                    mapped = map_table_rows(headers, raw_rows, page_number)
                except PackingSlipReadError:
                    continue
                candidates.append(mapped)
        if not candidates:
            raise PackingSlipReadError("未找到同时包含物料编码和条码的明细表")
        return max(candidates, key=len)

    @staticmethod
    def _element_page_number(element):
        for value in (
            element.get_attribute("value"),
            element.get_attribute("aria-label"),
            element.get_attribute("title"),
            element.inner_text(),
        ):
            match = re.fullmatch(r"\s*(\d+)\s*", str(value or ""))
            if match:
                return int(match.group(1))
        return None

    def _page_number_result(self, selector):
        values = set()
        for _page, scope in self._scopes():
            try:
                elements = scope.query_selector_all(selector)
            except Exception:
                continue
            for element in elements:
                if not self._visible(element):
                    continue
                value = self._element_page_number(element)
                if value is not None:
                    values.add(value)
        return bool(values), next(iter(values)) if len(values) == 1 else None

    def _current_page_number(self):
        found, current = self._page_number_result("[aria-current='page']")
        if found:
            return current
        found, current = self._page_number_result(
            ".pagination .active, .pagination .current, .pagination .selected, "
            ".pager .active, .pager .current, .pager .selected, "
            "[class*='pagination'] .active, [class*='pagination'] .current, "
            "[class*='pagination'] .selected"
        )
        if found:
            return current
        _found, current = self._page_number_result(
            ".pagination input, .pager input, [class*='pagination'] input, "
            "input[aria-label*='页码'], input[placeholder*='页码'], input[title*='页码']"
        )
        return current

    def _total_pages(self):
        values = set()
        pattern = re.compile(r"共\s*(\d+)\s*页")
        for _page, scope in self._scopes():
            try:
                text = scope.inner_text("body")
            except Exception:
                continue
            values.update(int(match) for match in pattern.findall(text or ""))
        if len(values) > 1:
            raise PackingSlipReadError("无法唯一识别总页数")
        return next(iter(values)) if values else None

    @staticmethod
    def _disabled(element):
        try:
            attributes = " ".join(
                [
                    element.get_attribute("disabled") or "",
                    element.get_attribute("aria-disabled") or "",
                    element.get_attribute("class") or "",
                ]
            ).lower()
            if "true" in attributes or "disabled" in attributes:
                return True
            return hasattr(element, "is_enabled") and not element.is_enabled()
        except Exception:
            return None

    def _next_controls(self):
        controls = []
        for _page, scope in self._scopes():
            try:
                elements = scope.query_selector_all("a,button,[role='button']")
            except Exception:
                continue
            for element in elements:
                if not self._visible(element):
                    continue
                text = _header_text(element.inner_text())
                hint = _header_text(
                    " ".join(
                        [
                            element.get_attribute("aria-label") or "",
                            element.get_attribute("title") or "",
                        ]
                    )
                )
                if text == "下一页" or "下一页" in hint:
                    controls.append(element)
        return controls

    def _page_input(self):
        selectors = (
            ".pagination input, .pager input, [class*='pagination'] input, "
            "input[aria-label*='页码'], input[placeholder*='页码'], input[title*='页码']"
        )
        for _page, scope in self._scopes():
            try:
                for element in scope.query_selector_all(selectors):
                    if self._visible(element):
                        return element
            except Exception:
                continue
        return None

    def _go_to_first_page(self):
        if self._current_page_number() == 1:
            return
        field = self._page_input()
        if field is not None:
            field.fill("1")
            field.press("Enter")
            self._wait_for_page(1, None)
            return
        if self._click_exact_text("首页"):
            self._wait_for_page(1, None)
            return
        raise PackingSlipReadError("无法主动进入第 1 页")

    def _has_next_page(self):
        controls = self._next_controls()
        if controls:
            states = [self._disabled(control) for control in controls]
            if any(state is False for state in states):
                return True
            if any(state is None for state in states):
                raise PackingSlipReadError("无法识别下一页状态")
            return False
        total = self._total_pages()
        current = self._current_page_number()
        if total is not None and current is not None and current < total and self._page_input() is not None:
            return True
        raise PackingSlipReadError("无法识别下一页状态")

    def _advance_to_page(self, expected_page):
        for control in self._next_controls():
            if self._disabled(control) is False:
                control.click()
                return
        field = self._page_input()
        if field is not None:
            field.fill(str(expected_page))
            field.press("Enter")
            return
        raise PackingSlipReadError(f"无法进入第 {expected_page} 页")

    def _wait_for_page(self, expected_page, previous_fingerprint):
        deadline = time.monotonic() + 10
        last_actual = None
        repeated = False
        while time.monotonic() < deadline:
            actual = self._current_page_number()
            last_actual = actual
            if actual is not None and actual not in (expected_page - 1, expected_page):
                raise PackingSlipReadError(
                    f"分页跳号：期望第 {expected_page} 页，实际第 {actual} 页"
                )
            if actual == expected_page:
                if previous_fingerprint is None:
                    return
                current_fingerprint = _rows_fingerprint(self._read_current_page())
                if current_fingerprint != previous_fingerprint:
                    return
                repeated = True
            self._pause(100)
        if repeated:
            raise PackingSlipReadError(f"页面重复：第 {expected_page} 页内容未变化")
        if last_actual is None:
            raise PackingSlipReadError("无法识别当前页码")
        raise PackingSlipReadError(
            f"等待分页超时：期望第 {expected_page} 页，实际第 {last_actual} 页"
        )
