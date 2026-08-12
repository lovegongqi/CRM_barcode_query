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
    "明细类型",
    "来源页码",
]


def normalize_packing_slip_no(value):
    normalized = str(value or "").strip().upper()
    if not PACKING_SLIP_PATTERN.fullmatch(normalized):
        raise ValueError("装箱单号格式不正确")
    return normalized


def build_inbound_result(packing_slip_no, rows, page_counts, shipment_rows=None):
    packing_slip_no = normalize_packing_slip_no(packing_slip_no)
    items_by_code = OrderedDict()
    clean_rows = []
    seen_serials = set()
    duplicate_serials = []
    reported_duplicates = set()

    for shipment in shipment_rows or []:
        product_code = _clean_text(shipment.get("product_code"))
        if not product_code:
            continue
        item = items_by_code.setdefault(product_code, _new_item(product_code))
        item["from_shipment_detail"] = True
        description = _clean_text(shipment.get("description"))
        if description and not item["description"]:
            item["description"] = description
        order_number = _clean_text(shipment.get("order_number"))
        if order_number and order_number not in item["order_numbers"]:
            item["order_numbers"].append(order_number)
        expected_quantity = _parse_quantity(shipment.get("expected_quantity"))
        if expected_quantity is not None:
            item["expected_quantity"] = (item["expected_quantity"] or 0) + expected_quantity

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
            elif not item["from_shipment_detail"] and expected_quantity != item["expected_quantity"]:
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
            "record_type": "条码",
        }
        clean_rows.append(clean_row)
        item["serials"].append(serial)

    items = list(items_by_code.values())
    for item in items:
        item["serial_count"] = len(item["serials"])
        item["unbarcoded_quantity"] = max(
            0,
            (item["expected_quantity"] or 0) - item["serial_count"],
        ) if item["from_shipment_detail"] else 0
        item["quantity_mismatch"] = (
            item["expected_quantity"] is not None
            and (
                item["serial_count"] > item["expected_quantity"]
                if item["from_shipment_detail"]
                else item["expected_quantity"] != item["serial_count"]
            )
        )
        if item["unbarcoded_quantity"]:
            clean_rows.append({
                "page": "发货明细",
                "row_index": None,
                "order_number": item["order_numbers"][0] if item["order_numbers"] else "",
                "product_code": item["product_code"],
                "description": item["description"],
                "expected_quantity": item["unbarcoded_quantity"],
                "serial": "",
                "record_type": "无条码配件",
            })
        item.pop("from_shipment_detail", None)

    if not clean_rows:
        raise ValueError("装箱单没有可用的产品明细")

    expected_total = sum(
        item["expected_quantity"]
        for item in items
        if item["expected_quantity"] is not None
    )
    total_serials = sum(item["serial_count"] for item in items)
    has_warnings = bool(duplicate_serials) or any(
        item["quantity_mismatch"] or item["warnings"] for item in items
    )
    return {
        "packing_slip_no": packing_slip_no,
        "page_counts": list(page_counts),
        "pages_read": [entry["page"] for entry in page_counts],
        "expected_total": expected_total,
        "total_serials": total_serials,
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
            row.get("record_type", "条码"),
            row["page"],
        ])
    sheet.auto_filter.ref = sheet.dimensions
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEFGH", (20, 18, 18, 24, 12, 20, 14, 12)):
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
        "from_shipment_detail": False,
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
