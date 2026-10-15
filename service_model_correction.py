"""Guarded model correction for an existing CRM retail order line."""
import hashlib
import json
from collections import Counter


def build_model_correction_preview(snapshot, service_products):
    counts = Counter(str(p.get('product_code') or '').strip().upper() for p in service_products)
    names = {str(p.get('product_code') or '').strip().upper(): p.get('product_name') or '' for p in service_products}
    rows = snapshot['rows']
    order_codes = {str(row.get('new_productname') or '').strip().upper() for row in rows}
    options = []
    for row in rows:
        code = str(row.get('new_productname') or '').strip().upper()
        if (not row.get('detailid') or not code or code in counts or row.get('new_isgifts')
                or row.get('new_type') != 1 or float(row.get('new_installedqty') or 0) != 0
                or float(row.get('new_returnqty') or 0) != 0):
            continue
        for target, quantity in counts.items():
            if not target or target in order_codes or float(row.get('new_qty') or 0) != quantity:
                continue
            options.append({
                'detail_id': row['detailid'], 'seq': row.get('new_seq'),
                'old_code': code, 'old_name': (row.get('new_productid') or {}).get('name') or code,
                'new_code': target, 'new_name': names[target], 'quantity': quantity,
            })
    fingerprint = hashlib.sha256(json.dumps(
        [snapshot, service_products], ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    return {**snapshot, 'options': options, 'fingerprint': fingerprint}


# Read current server data, not stale editor values left after cancelling a dialog.
MODEL_SNAPSHOT_JS = r"""async (orderNo) => {
    const pages = [...new Set([...document.querySelectorAll('*')].map(el => el.__vue__))]
        .filter(v => v?.$refs?.['product-line'] && v?.editForm?.new_name === orderNo);
    if (pages.length !== 1) throw new Error('未找到唯一的关联订单页面');
    const page = pages[0], list = page.$refs['product-line'], id = page.$route?.params?.id;
    if (!id || list.id !== id || list.loading || !Array.isArray(list.faultData)) throw new Error('订单产品明细尚未加载完成');
    const rows = await rt.get('api/SalesOrder/GetOrderLineList?id=' + encodeURIComponent(id));
    if (!Array.isArray(rows)) throw new Error('订单产品明细读取失败');
    const details = [];
    for (const row of rows) {
        if (!row.detailid) throw new Error('订单产品行缺少明细编号');
        const detail = await rt.get('api/SalesOrder/GetOrderLineInfo?id=' + encodeURIComponent(row.detailid));
        if (!detail || detail.detailid !== row.detailid) throw new Error('订单产品行读取不一致');
        details.push(detail);
    }
    return {order_id:id, order_no:orderNo, rows:details};
}"""

MODEL_APPLY_JS = r"""async ({preview, option}) => {
    const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
    const canonical = value => JSON.stringify(sorted(value));
    const page = [...document.querySelectorAll('*')].map(el => el.__vue__)
        .find(v => v?.$route?.params?.id === preview.order_id && v?.$refs?.['product-line']);
    const list = page?.$refs['product-line'], dialog = list?.$refs?.productLine;
    if (page?.editForm?.new_name !== preview.order_no || list?.id !== preview.order_id || list.loading)
        throw new Error('关联订单页面已变化，请重新预览');
    if (!dialog || typeof dialog.updateFormDate !== 'function' || dialog.dialogFormVisible || dialog.loading || dialog.isSubmit)
        throw new Error('产品编辑窗口忙碌或页面版本不兼容，请稍后重试');
    const expected = preview.rows.filter(row => row.detailid === option.detail_id);
    if (expected.length !== 1) throw new Error('产品行不唯一，请重新预览');
    const read = () => rt.get('api/SalesOrder/GetOrderLineInfo?id=' + encodeURIComponent(option.detail_id));
    const before = await read();
    if (!before || canonical(before) !== canonical(expected[0])) throw new Error('订单产品行已变化，请重新预览');
    if (Number(before.new_installedqty || 0) !== 0 || Number(before.new_returnqty || 0) !== 0)
        throw new Error('产品已有安装或退货数量，不能修改型号');
    const lookup = await rt.get('../api/crmlookupview/getdata?entityName=product&page=1&count=50&select=name,productid,productnumber&orderby=new_salesvolume%20asc&filter=name,productnumber&filterValue=' + encodeURIComponent(option.new_code) + '&condition=');
    const targets = (lookup?.Data || []).filter(row => String(row.productnumber).trim().toUpperCase() === option.new_code);
    if (targets.length !== 1 || !targets[0].productid) throw new Error('未找到唯一的目标型号，请核对产品编码');
    // Match RtLookup.selectValue + equipmentLine.lookupchange and preserve all other fields.
    const target = targets[0];
    const payload = {...before, id:preview.order_id, new_activetype:before.new_activetype > 0 ? before.new_activetype : '',
        new_productid:{id:target.productid, name:target.name, logicalname:'product'}, productnumber:target.productnumber};
    const latest = await read();
    if (canonical(latest) !== canonical(before)) throw new Error('保存前订单已变化，请重新预览');
    dialog.isSubmit = true;
    try {
        await dialog.updateFormDate(payload);
        const saved = await read();
        if (!saved || saved.new_productid?.id !== target.productid || String(saved.new_productname).trim().toUpperCase() !== option.new_code)
            throw new Error('提交后未确认新型号，请重新查询订单核对，勿重复提交');
        for (const key of ['new_qty','new_price','new_amount','new_installedqty','new_returnqty','new_returnamount']) {
            if (Number(saved[key] || 0) !== Number(before[key] || 0)) throw new Error('型号已提交，但订单数量或金额发生变化，请在 CRM 核对');
        }
        return {detail_id:option.detail_id, old_code:option.old_code, new_code:option.new_code, message:'型号已修正并核验'};
    } finally { dialog.isSubmit = false; }
}"""
