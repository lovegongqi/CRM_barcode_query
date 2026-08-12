from collections import OrderedDict
from io import BytesIO
import re

from openpyxl import Workbook


PACKING_SLIP_PATTERN = re.compile(r"^SH\d{8,}$")
WORKBOOK_HEADERS = [
    "装箱单号",
    "订单号",
    "物料编码",
    "物料描述",
    "应发数量",
    "条码",
    "来源页码",
]


def normalize_packing_slip_no(value):
    normalized = str(value or "").strip().upper()
    if not PACKING_SLIP_PATTERN.fullmatch(normalized):
        raise ValueError("装箱单号格式不正确")
    return normalized


def build_inbound_result(packing_slip_no, rows, page_counts):
    packing_slip_no = normalize_packing_slip_no(packing_slip_no)
    items_by_code = OrderedDict()
    clean_rows = []
    seen_serials = set()
    duplicate_serials = []
    reported_duplicates = set()

    for raw_row in rows:
        product_code = _clean_text(raw_row.get("product_code"))
        serial = _clean_text(raw_row.get("serial"))
        if not product_code or not serial:
            continue
        if serial in seen_serials:
            if serial not in reported_duplicates:
                duplicate_serials.append(serial)
                reported_duplicates.add(serial)
            continue
        seen_serials.add(serial)

        item = items_by_code.setdefault(product_code, _new_item(product_code))
        order_number = _clean_text(raw_row.get("order_number"))
        if order_number and order_number not in item["order_numbers"]:
            item["order_numbers"].append(order_number)

        description = _clean_text(raw_row.get("description"))
        if description and not item["description"]:
            item["description"] = description

        expected_quantity = _parse_quantity(raw_row.get("expected_quantity"))
        if expected_quantity is not None:
            if item["expected_quantity"] is None:
                item["expected_quantity"] = expected_quantity
            elif expected_quantity != item["expected_quantity"]:
                warning = "同一物料编码存在冲突的应发数量"
                if warning not in item["warnings"]:
                    item["warnings"].append(warning)

        clean_row = {
            "page": _page_number(raw_row.get("page")),
            "row_index": raw_row.get("row_index"),
            "order_number": order_number,
            "product_code": product_code,
            "description": description,
            "expected_quantity": expected_quantity,
            "serial": serial,
        }
        clean_rows.append(clean_row)
        item["serials"].append(serial)

    if not clean_rows:
        raise ValueError("装箱单没有可用的产品条码明细")

    items = list(items_by_code.values())
    for item in items:
        item["serial_count"] = len(item["serials"])
        item["quantity_mismatch"] = (
            item["expected_quantity"] is not None
            and item["expected_quantity"] != item["serial_count"]
        )

    expected_total = sum(
        item["expected_quantity"]
        for item in items
        if item["expected_quantity"] is not None
    )
    has_warnings = bool(duplicate_serials) or any(
        item["quantity_mismatch"] or item["warnings"] for item in items
    )
    return {
        "packing_slip_no": packing_slip_no,
        "page_counts": list(page_counts),
        "pages_read": [entry["page"] for entry in page_counts],
        "expected_total": expected_total,
        "total_serials": len(clean_rows),
        "duplicate_serials": duplicate_serials,
        "has_warnings": has_warnings,
        "items": items,
        "rows": clean_rows,
    }


def build_inbound_workbook(result):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "入库明细"
    sheet.append(WORKBOOK_HEADERS)
    for row in result["rows"]:
        sheet.append([
            result["packing_slip_no"],
            row["order_number"],
            row["product_code"],
            row["description"],
            row["expected_quantity"],
            row["serial"],
            row["page"],
        ])
    sheet.auto_filter.ref = sheet.dimensions
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEFG", (20, 18, 18, 24, 12, 20, 12)):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _new_item(product_code):
    return {
        "product_code": product_code,
        "description": "",
        "expected_quantity": None,
        "order_numbers": [],
        "serials": [],
        "warnings": [],
    }


def _clean_text(value):
    return str(value or "").strip()


def _parse_quantity(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        quantity = float(text)
    except ValueError:
        return None
    return int(quantity) if quantity.is_integer() else quantity


def _page_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value
