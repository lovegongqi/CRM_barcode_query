class GYJInboundError(RuntimeError):
    pass


MAX_SERIALS_PER_LINE = 100
MAX_SERIAL_TEXT_LENGTH = 2000


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
