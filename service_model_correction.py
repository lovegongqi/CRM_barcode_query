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


def build_barcode_model_correction_preview(order, service, library, overrides=None):
    """Offer prefix or selected manual corrections for one unambiguous order line."""
    options = []
    overrides = overrides or {}
    order_rows = order.get('rows') or []
    for line in (service.get('rows') or []) if service.get('editable') else []:
        barcode = str(line.get('new_userprofile_number') or '').strip()
        if overrides and barcode not in overrides:
            continue
        rule = overrides.get(barcode) or library.get(barcode) or library.get(barcode[:-10]) or {}
        old_code = str(line.get('productnumber') or '').strip().upper()
        target = str(rule.get('product_code') or '').strip().upper()
        if not barcode or not old_code or not target or old_code == target:
            continue
        linked = line.get('new_mem_salesorderlineid') or ''
        linked = linked.get('id') if isinstance(linked, dict) else linked
        candidates = [row for row in order_rows if row.get('detailid') and
                      (not linked or row['detailid'] == linked) and
                      str(row.get('new_productname') or '').strip().upper() in {old_code, target}]
        if len(candidates) != 1 or not line.get('id'):
            continue
        row = candidates[0]
        order_code = str(row.get('new_productname') or '').strip().upper()
        if order_code == old_code and (row.get('new_isgifts') or row.get('new_type') != 1 or
                                       float(row.get('new_qty') or 0) != 1 or
                                       float(row.get('new_installedqty') or 0) != 0 or
                                       float(row.get('new_returnqty') or 0) != 0):
            continue
        options.append({
            'barcode': barcode, 'service_line_id': line['id'], 'order_detail_id': row['detailid'],
            'seq': row.get('new_seq'), 'old_code': old_code,
            'old_name': (line.get('new_product_id') or {}).get('name') or old_code,
            'new_code': target, 'new_name': rule.get('product_name') or target,
            'quantity': 1, 'source': 'manual' if barcode in overrides else 'prefix',
            'steps': (['order'] if order_code != target else []) + ['service'],
        })
    fingerprint = hashlib.sha256(json.dumps(
        [order, service, library, overrides], ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    return {**order, 'service': service, 'options': options, 'fingerprint': fingerprint, 'mode': 'barcode'}


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

MODEL_LOOKUP_JS = r"""async (code) => {
    const lookup = await rt.get('../api/crmlookupview/getdata?entityName=product&page=1&count=50&select=name,productid,productnumber&orderby=new_salesvolume%20asc&filter=name,productnumber&filterValue=' + encodeURIComponent(code) + '&condition=');
    const targets = (lookup?.Data || []).filter(row => String(row.productnumber || '').trim().toUpperCase() === code);
    if (targets.length !== 1 || !targets[0].productid || !targets[0].name)
        throw new Error('未找到唯一的目标型号，请核对手填产品编码');
    return {product_code:code, product_name:targets[0].name};
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


SERVICE_SNAPSHOT_JS = r"""async (serviceNo) => {
    const match = location.hash.match(/^#\/workOrder\/edit\/([A-Za-z0-9-]+)/);
    if (!match) throw new Error('当前不是 CRM 服务单详情页');
    const id = match[1];
    const workOrder = await rt.get('api/WorkOrder/GetWorkOrderById/' + encodeURIComponent(id));
    if (workOrder?.new_name !== serviceNo) throw new Error('CRM 服务单号已变化，请重新查询');
    const rows = await rt.get('api/WorkOrder/GetSrvProductline/' + encodeURIComponent(id));
    if (!Array.isArray(rows)) throw new Error('服务单产品明细读取失败');
    const details = [];
    for (const row of rows) {
        if (!row.id) throw new Error('服务单产品行缺少明细编号');
        const detail = await rt.get('api/WorkOrder/GetSrvProductlineById/' + encodeURIComponent(row.id));
        if (!detail || (detail.id && detail.id !== row.id)) throw new Error('服务单产品行读取不一致');
        details.push({...detail,id:row.id});
    }
    return {service_no:serviceNo, work_order_id:id,
        editable:[1,2,4].includes(Number(workOrder.new_approvalstatus)), rows:details};
}"""


SERVICE_APPLY_JS = r"""async ({preview, option}) => {
    const sorted = value => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
    const canonical = value => JSON.stringify(sorted(value));
    const service = preview.service;
    const expected = service.rows.filter(row => row.id === option.service_line_id);
    if (expected.length !== 1) throw new Error('服务单产品行不唯一，请重新预览');
    const workOrder = await rt.get('api/WorkOrder/GetWorkOrderById/' + encodeURIComponent(service.work_order_id));
    if (workOrder?.new_name !== service.service_no || ![1,2,4].includes(Number(workOrder.new_approvalstatus)))
        throw new Error('服务单状态已变化，CRM 当前不允许修改明细');
    const order = await rt.get('api/SalesOrder/GetOrderLineInfo?id=' + encodeURIComponent(option.order_detail_id));
    if (String(order?.new_productname || '').trim().toUpperCase() !== option.new_code)
        throw new Error('必须先修正关联订单型号，服务单尚未修改');
    const read = async () => {
        const row = await rt.get('api/WorkOrder/GetSrvProductlineById/' + encodeURIComponent(option.service_line_id));
        if (!row || (row.id && row.id !== option.service_line_id)) throw new Error('服务单产品行读取不一致');
        return {...row,id:option.service_line_id};
    };
    const before = await read();
    if (canonical(before) !== canonical(expected[0])) throw new Error('服务单产品行已变化，请重新预览');
    const lookup = await rt.get('../api/crmlookupview/getdata?entityName=product&page=1&count=50&select=name,productid,productnumber&orderby=new_salesvolume%20asc&filter=name,productnumber&filterValue=' + encodeURIComponent(option.new_code) + '&condition=');
    const targets = (lookup?.Data || []).filter(row => String(row.productnumber).trim().toUpperCase() === option.new_code);
    if (targets.length !== 1 || !targets[0].productid) throw new Error('未找到唯一的目标型号，请核对产品编码');
    const target = targets[0];
    const payload = {...before, id:option.service_line_id, new_srv_workorder_id:service.work_order_id,
        new_product_id:{id:target.productid,name:target.name,logicalname:'product'}, productnumber:target.productnumber};
    if (canonical(await read()) !== canonical(before)) throw new Error('保存前服务单已变化，请重新预览');
    await rt.post('api/WorkOrder/UpdateSrvProductline', payload);
    const saved = await read();
    if (saved?.new_product_id?.id !== target.productid || String(saved.productnumber).trim().toUpperCase() !== option.new_code)
        throw new Error('提交后未确认服务单新型号，请在 CRM 核对，勿重复提交');
    for (const key of ['new_userprofile_number','new_mem_salesorderlineid']) {
        if (canonical(saved[key]) !== canonical(before[key])) throw new Error('服务单型号已提交，但条码或关联订单行发生变化，请在 CRM 核对');
    }
    return {service_line_id:option.service_line_id, new_code:option.new_code,
        new_name:target.name, barcode:before.new_userprofile_number,
        message:'订单与服务单型号已修正并核验'};
}"""
