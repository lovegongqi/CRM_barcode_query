"""Read-only parsing and report access for GYJ inventory data."""

from decimal import Decimal
import re

from inventory_store import normalize_quantity


GYJ_STOCK_URL = "https://cloud.gyjerp.com/report/material_stock"
GYJ_MATERIAL_URL = "https://cloud.gyjerp.com/material/material"
GYJ_SERIAL_URL = (
    "https://cloud.gyjerp.com/system/plugins/serialNumberStatistics/"
    "serialNumberStatistics.html"
)
STOCK_SEARCH_FIELD = "请输入条码、名称、助记码、规格、型号等信息"


class GYJInventoryReadError(RuntimeError):
    pass


def _text(value):
    return " ".join(str(value or "").split())


def _header_indexes(headers):
    return {"".join(str(header or "").split()): index for index, header in enumerate(headers or [])}


def _normalized_headers(headers):
    return tuple("".join(str(header or "").split()) for header in headers or [])


def _cell(row, indexes, header):
    index = indexes.get(header)
    if index is None or index >= len(row):
        return ""
    return _text(row[index])


def parse_stock_rows(headers, rows):
    indexes = _header_indexes(headers)
    fields = {
        "barcode": "条码",
        "name": "名称",
        "spec": "规格",
        "model": "型号",
        "category": "类别",
        "unit": "单位",
        "stock": "库存",
    }
    missing = [header for header in fields.values() if header not in indexes]
    if missing:
        raise GYJInventoryReadError(f"GYJ 库存报表缺少列：{','.join(missing)}")
    parsed = []
    for row in rows or []:
        values = {key: _cell(row, indexes, header) for key, header in fields.items()}
        if not any(values.values()) or any(value == "合计" for value in values.values()):
            continue
        if not values["barcode"]:
            continue
        try:
            values["stock"] = normalize_quantity(values["stock"] or "0")
        except ValueError as error:
            raise GYJInventoryReadError(
                f"GYJ 库存数量无效：{values['barcode']}"
            ) from error
        parsed.append(values)
    return parsed


def parse_material_rows(rows):
    materials = {}
    for row in rows or []:
        if not isinstance(row, dict):
            raise GYJInventoryReadError("GYJ 商品信息行格式无效")
        if _is_total_row(row):
            continue
        barcode = _text(row.get("barcode"))
        name = _text(row.get("name"))
        if not barcode or not name:
            raise GYJInventoryReadError("GYJ 商品信息缺少条码或名称")
        if not isinstance(row.get("serial_badge"), bool):
            raise GYJInventoryReadError(f"GYJ 商品缺少序列号设置：{barcode}")
        if row.get("enabled") is False:
            continue
        materials[barcode] = {
            "barcode": barcode,
            "name": name,
            "spec": _text(row.get("spec")),
            "model": _text(row.get("model")),
            "category": _text(row.get("category")),
            "unit": _text(row.get("unit")),
            "has_serial": row["serial_badge"],
        }
    return materials


def parse_serial_rows(headers, rows, include_shipped=False):
    indexes = _header_indexes(headers)
    fields = {
        "serial": "序列号",
        "barcode": "条码",
        "name": "名称",
        "warehouse": "仓库",
    }
    required = [*fields.values(), "已出库"]
    missing = [header for header in required if header not in indexes]
    if missing:
        raise GYJInventoryReadError(f"GYJ 序列号报表缺少列：{','.join(missing)}")
    parsed = []
    for row in rows or []:
        values = {key: _cell(row, indexes, header) for key, header in fields.items()}
        shipped = _cell(row, indexes, "已出库")
        if not any(values.values()) or any(value == "合计" for value in values.values()):
            continue
        if not values["serial"]:
            continue
        if shipped not in {"否", "是"}:
            raise GYJInventoryReadError(
                f"GYJ 序列号出库状态无效：{values['serial']}"
            )
        values["shipped"] = shipped == "是"
        if values["shipped"] and not include_shipped:
            continue
        parsed.append(values)
    return parsed


def _sum_quantities(values):
    decimals = [Decimal(normalize_quantity(value)) for value in values]
    if not decimals:
        return "0"
    exponent = min(value.as_tuple().exponent for value in decimals)
    total = 0
    for value in decimals:
        parts = value.as_tuple()
        coefficient = int("".join(str(digit) for digit in parts.digits) or "0")
        if parts.sign:
            coefficient = -coefficient
        total += coefficient * (10 ** (parts.exponent - exponent))
    sign = int(total < 0)
    digits = tuple(int(digit) for digit in str(abs(total))) or (0,)
    exact = Decimal((sign, digits, exponent))
    return normalize_quantity(format(exact, "f"))


def _is_total_row(row):
    values = row.values() if isinstance(row, dict) else row
    return any(_text(value) == "合计" for value in values)


class GYJInventoryReader:
    """Read inventory reports without mutating GYJ records or warehouse filters."""

    def __init__(self, page, log=None):
        self.page = page
        self.browser_page = getattr(page, "page", page)
        self.log = log

    def _emit(self, message):
        if self.log:
            self.log(message)

    def _goto(self, url):
        target = self.page if hasattr(self.page, "goto") else self.browser_page
        target.goto(url, wait_until="domcontentloaded", timeout=60000)

    def _fill_field(self, label, value):
        if hasattr(self.page, "fill_field"):
            self.page.fill_field(label, value)
            return
        if label == STOCK_SEARCH_FIELD:
            control = self.browser_page.locator(
                f'input[placeholder="{STOCK_SEARCH_FIELD}"]:visible'
            )
        else:
            field = self.browser_page.locator(
                ".ant-form-item:visible, .search-form-item:visible"
            ).filter(has_text=label)
            control = field.locator("input:visible").first
        if control.count() != 1:
            raise GYJInventoryReadError(f"未找到 GYJ 查询字段：{label}")
        control.fill(str(value))

    def _select_label(self, label, value):
        if hasattr(self.page, "select_label"):
            self.page.select_label(label, value)
            return
        field = self.browser_page.locator(
            ".ant-form-item:visible, .search-form-item:visible"
        ).filter(has_text=label)
        trigger = field.locator(
            ".ant-select-selector:visible, .ant-select-selection:visible"
        ).first
        if trigger.count() != 1:
            raise GYJInventoryReadError(f"未找到 GYJ 筛选字段：{label}")
        trigger.click()
        dropdown = self.browser_page.locator(".ant-select-dropdown:visible").last
        choice = dropdown.get_by_text(value, exact=True)
        if choice.count() != 1:
            raise GYJInventoryReadError(f"未找到 GYJ {label} 选项：{value}")
        choice.click()

    def _expand_filters(self):
        if hasattr(self.page, "expand_filters"):
            self.page.expand_filters()
            return
        for label in ("展 开", "展开"):
            control = self.browser_page.get_by_text(label, exact=True)
            if control.count() == 1:
                control.click()
                return

    def _click_query(self):
        if hasattr(self.page, "click_query"):
            self.page.click_query()
            return
        for attempt in range(151):
            for label in ("查 询", "查询"):
                control = self.browser_page.get_by_role("button", name=label, exact=True)
                if control.count() == 1:
                    control.click()
                    self.browser_page.wait_for_timeout(300)
                    return
            if attempt < 150:
                self.browser_page.wait_for_timeout(100)
        raise GYJInventoryReadError("未找到 GYJ 查询按钮")

    def _read_table_page(self, report):
        if hasattr(self.page, "read_table_page"):
            return dict(self.page.read_table_page(report))
        snapshot = self.browser_page.evaluate(
            """report => {
                const visible = element => !!(element && (element.offsetWidth || element.offsetHeight));
                const tables = Array.from(document.querySelectorAll('.ant-table-wrapper, .ant-table'))
                    .filter(visible).filter(table => table.querySelector('tbody'));
                const table = tables[tables.length - 1];
                if (!table) return null;
                const headers = Array.from(table.querySelectorAll('thead th'))
                    .map(cell => (cell.innerText || cell.textContent || '').replace(/\\s+/g, ' ').trim());
                const normalizedHeaders = headers.map(header => header.replace(/\\s+/g, ''));
                const nameIndex = ['名称', '商品名称', '物料名称']
                    .map(name => normalizedHeaders.indexOf(name)).find(index => index >= 0);
                const serialBadges = [];
                const rows = Array.from(table.querySelectorAll('tbody tr'))
                    .filter(row => visible(row) && !row.classList.contains('ant-table-placeholder'))
                    .map(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    const nameCell = nameIndex === undefined ? null : cells[nameIndex];
                    serialBadges.push(nameCell ? Array.from(nameCell.querySelectorAll('*')).some(node =>
                        visible(node) && (node.innerText || node.textContent || '').trim() === '序') : null);
                    return cells.map(cell => (cell.innerText || cell.textContent || '').replace(/\\s+/g, ' ').trim());
                });
                const totalNode = Array.from(document.querySelectorAll('.ant-pagination-total-text'))
                    .filter(visible).pop();
                const next = Array.from(document.querySelectorAll('.ant-pagination-next')).filter(visible).pop();
                const activePage = Array.from(document.querySelectorAll('.ant-pagination-item-active'))
                    .filter(visible).pop();
                const loading = Array.from(document.querySelectorAll(
                    '.ant-spin-spinning, .ant-table-loading, [aria-busy="true"]'
                )).some(visible);
                return {
                    headers,
                    rows,
                    serial_badges: serialBadges,
                    total: totalNode ? (totalNode.innerText || totalNode.textContent || '') : null,
                    page_number: activePage ? (activePage.innerText || activePage.textContent || '').trim() : null,
                    loading,
                    has_next: !!(next && !next.classList.contains('ant-pagination-disabled') &&
                        next.getAttribute('aria-disabled') !== 'true')
                };
            }""",
            report,
        )
        if snapshot is None:
            raise GYJInventoryReadError("GYJ 报表表格未加载")
        return snapshot

    def _next_page(self):
        if hasattr(self.page, "next_page"):
            self.page.next_page()
            return
        next_button = self.browser_page.locator(
            ".ant-pagination-next:not(.ant-pagination-disabled)"
        ).last
        if next_button.count() != 1:
            raise GYJInventoryReadError("GYJ 报表分页下一页不可用")
        next_button.click()

    @staticmethod
    def _page_marker(snapshot):
        page_number = _text(snapshot.get("page_number"))
        rows = snapshot.get("rows") or []
        row_signature = tuple(
            tuple(_text(value) for value in (row.values() if isinstance(row, dict) else row))
            for row in rows
        )
        return page_number, row_signature

    def _wait_for_page_change(self, report, previous):
        previous_page, previous_rows = self._page_marker(previous)
        waiter = self.page if hasattr(self.page, "wait_for_timeout") else self.browser_page
        for _attempt in range(150):
            snapshot = self._read_table_page(report)
            page_number, rows = self._page_marker(snapshot)
            page_changed = bool(
                previous_page and page_number and page_number != previous_page
            )
            rows_changed = rows != previous_rows
            if not snapshot.get("loading") and (page_changed or rows_changed):
                return snapshot
            waiter.wait_for_timeout(100)
        raise GYJInventoryReadError("GYJ 报表分页未在限时内更新")

    @staticmethod
    def _total_count(value):
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        text = str(value).replace(",", "")
        total_match = re.search(r"共\s*(\d+)\s*条", text)
        if total_match:
            return int(total_match.group(1))
        match = re.search(r"\d+", text)
        if not match:
            raise GYJInventoryReadError("GYJ 报表分页总数无法解析")
        return int(match.group())

    def _collect_pages(self, report):
        headers = None
        normalized_headers = None
        rows = []
        badges = []
        expected_total = None
        snapshot = self._read_table_page(report)
        for _page_number in range(1, 1001):
            page_headers = list(snapshot.get("headers") or [])
            page_normalized_headers = _normalized_headers(page_headers)
            if not page_normalized_headers:
                raise GYJInventoryReadError("GYJ 报表缺少表头")
            if headers is None:
                headers = page_headers
                normalized_headers = page_normalized_headers
            elif page_normalized_headers != normalized_headers:
                raise GYJInventoryReadError("GYJ 报表分页表头发生变化")
            page_rows = list(snapshot.get("rows") or [])
            page_badges = list(snapshot.get("serial_badges") or [])
            for index, row in enumerate(page_rows):
                if _is_total_row(row) or not any(_text(value) for value in (row.values() if isinstance(row, dict) else row)):
                    continue
                rows.append(row)
                badges.append(page_badges[index] if index < len(page_badges) else None)
            page_total = self._total_count(snapshot.get("total"))
            if page_total is not None:
                if expected_total is not None and page_total != expected_total:
                    raise GYJInventoryReadError("GYJ 报表分页总数在读取期间变化")
                expected_total = page_total
            if not snapshot.get("has_next"):
                break
            self._next_page()
            snapshot = self._wait_for_page_change(report, snapshot)
        else:
            raise GYJInventoryReadError("GYJ 报表分页超过安全上限")
        if expected_total is not None and expected_total != len(rows):
            raise GYJInventoryReadError(
                f"GYJ 报表分页总数与采集行数不符：{expected_total} != {len(rows)}"
            )
        return headers or [], rows, badges

    @staticmethod
    def _material_page_rows(headers, rows, badges):
        normalized = list(_normalized_headers(headers))

        def index_of(*names):
            for name in names:
                if name in normalized:
                    return normalized.index(name)
            return None

        indexes = {
            "barcode": index_of("条码", "商品条码", "物料编码", "商品编码"),
            "name": index_of("名称", "商品名称", "物料名称"),
            "spec": index_of("规格", "商品规格"),
            "model": index_of("型号", "商品型号"),
            "category": index_of("类别", "商品类别"),
            "unit": index_of("单位", "基本单位"),
            "status": index_of("状态", "启用状态"),
        }
        if indexes["barcode"] is None or indexes["name"] is None or indexes["status"] is None:
            raise GYJInventoryReadError("GYJ 商品信息缺少必需表头")
        if not rows:
            return []
        if isinstance(rows[0], dict):
            for row in rows:
                if not isinstance(row, dict):
                    raise GYJInventoryReadError("GYJ 商品信息行格式无效")
                if not _text(row.get("barcode")) or not _text(row.get("name")):
                    raise GYJInventoryReadError("GYJ 商品信息缺少条码或名称")
                if not isinstance(row.get("serial_badge"), bool):
                    raise GYJInventoryReadError("GYJ 商品缺少序列号设置")
            return rows
        parsed = []
        for position, row in enumerate(rows):
            def value(field):
                index = indexes[field]
                return _text(row[index]) if index is not None and index < len(row) else ""

            status = value("status")
            if position >= len(badges) or not isinstance(badges[position], bool):
                raise GYJInventoryReadError("GYJ 商品缺少序列号设置")
            if not value("barcode") or not value("name"):
                raise GYJInventoryReadError("GYJ 商品信息缺少条码或名称")
            has_serial = badges[position]
            name = value("name")
            if has_serial:
                name = name.removesuffix(" 序").removesuffix("序").rstrip()
            parsed.append({
                "barcode": value("barcode"),
                "name": name,
                "spec": value("spec"),
                "model": value("model"),
                "category": value("category"),
                "unit": value("unit"),
                "enabled": status not in {"停用", "禁用", "否"},
                "serial_badge": has_serial,
            })
        return parsed

    def _read_stock_rows(self, barcode=None):
        self._goto(GYJ_STOCK_URL)
        if barcode is not None:
            barcode = _text(barcode)
            if not barcode:
                raise GYJInventoryReadError("GYJ 库存查询缺少条码")
            self._fill_field(STOCK_SEARCH_FIELD, barcode)
        self._click_query()
        headers, rows, _badges = self._collect_pages("stock")
        return parse_stock_rows(headers, rows)

    @staticmethod
    def _stock_totals(rows):
        grouped = {}
        for row in rows:
            grouped.setdefault(row["barcode"], []).append(row["stock"])
        return {barcode: _sum_quantities(values) for barcode, values in grouped.items()}

    def load_catalog(self):
        self._emit("正在读取 GYJ 商品信息")
        self._goto(GYJ_MATERIAL_URL)
        self._click_query()
        headers, raw_materials, badges = self._collect_pages("material")
        materials = parse_material_rows(
            self._material_page_rows(headers, raw_materials, badges)
        )
        stock_rows = self._read_stock_rows()
        stock_totals = self._stock_totals(stock_rows)
        catalog = []
        for material in materials.values():
            item = dict(material)
            item["initial_stock"] = stock_totals.get(item["barcode"], "0")
            catalog.append(item)
        for stock in stock_rows:
            barcode = stock["barcode"]
            if barcode in materials or any(item["barcode"] == barcode for item in catalog):
                continue
            catalog.append({
                "barcode": barcode,
                "name": stock["name"],
                "spec": stock["spec"],
                "model": stock["model"],
                "category": stock["category"],
                "unit": stock["unit"],
                "initial_stock": stock_totals[barcode],
                "data_error": "无法确认商品序列号设置",
            })
        self._emit(f"已读取 GYJ 商品：{len(catalog)} 个")
        return catalog

    def read_total_stock(self, barcode):
        barcode = _text(barcode)
        rows = self._read_stock_rows(barcode)
        if any(row["barcode"] != barcode for row in rows):
            raise GYJInventoryReadError(
                f"GYJ 库存查询条件未生效：{barcode}"
            )
        return _sum_quantities(row["stock"] for row in rows)

    def read_stock_totals(self):
        return self._stock_totals(self._read_stock_rows())

    def _read_serials(self, field, value, shipped):
        value = _text(value)
        if not value:
            raise GYJInventoryReadError(f"GYJ 序列号查询缺少{field}")
        self._goto(GYJ_SERIAL_URL)
        self._expand_filters()
        self._fill_field(field, value)
        self._select_label("已出库", "是" if shipped else "否")
        self._click_query()
        headers, rows, _badges = self._collect_pages("serial")
        parsed = parse_serial_rows(headers, rows, include_shipped=True)
        result_key = "serial" if field == "序列号" else "barcode"
        if any(row[result_key] != value for row in parsed):
            raise GYJInventoryReadError(f"GYJ 序列号查询条件未生效：{value}")
        return [
            row for row in parsed
            if row["shipped"] is shipped
        ]

    def read_unshipped_serials(self, barcode):
        barcode = _text(barcode)
        return [
            row for row in self._read_serials("商品", barcode, False)
            if row["barcode"] == barcode
        ]

    def lookup_serial(self, serial):
        serial = _text(serial)
        rows = []
        for shipped in (False, True):
            rows.extend(self._read_serials("序列号", serial, shipped))
        if any(row["serial"] != serial for row in rows):
            raise GYJInventoryReadError(f"GYJ 序列号查询条件未生效：{serial}")
        matches = [row for row in rows if row["serial"] == serial]
        if len(matches) > 1:
            raise GYJInventoryReadError(f"GYJ 序列号结果不唯一：{serial}")
        return matches[0] if matches else None
