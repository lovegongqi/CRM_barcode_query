# 服务单详情与安装模板导出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在服务单详情展示新增字段，并按安装导入模板 B–U 列导出已保存的服务单资料。

**Architecture:** `app.py` 从服务单详情 JSON 和条码查询结果构建去重的安装服务单导出行，生成一个与模板第一页列顺序一致的 XLSX。`index.html` 显示三个新增详情字段，并提供优先导出已选条码、否则当前筛选结果的入口。

**Tech Stack:** Flask、openpyxl、原生 JavaScript、unittest。

## Global Constraints

- 只使用最新的“安装”服务单；维修和保养服务单不导出。
- 不自动打开 CRM 或重新抓取详情；缺少本地详情的服务单跳过。
- 模板列为 B–U；第 1 行字段名、第 2 行标题、第 3 行开始写数据。
- 受理时间映射“购买日期”，客户预约时间映射“安装日期”，服务人员映射“安装人员”，服务单号写入“备注”。

---

### Task 1: 服务单详情扩展字段

**Files:**
- Modify: `templates/index.html:2500-2545`
- Test: `tests/test_frontend_contract.py:48-62`

**Interfaces:**
- Consumes: `detail.fields`, 每项为 `{label: str, value: str}`。
- Produces: `renderServiceOrderDetail(detail)` 显示受理时间、客户预约时间、服务人员。

- [ ] **Step 1: Write the failing test**

```python
for label in ("受理时间", "客户预约时间", "服务人员"):
    self.assertIn(label, results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_frontend_contract.py' -q`

Expected: FAIL because the three labels are absent from the detail renderer.

- [ ] **Step 3: Write minimal implementation**

```javascript
const acceptedAt = fieldValue(['受理时间', '受理日期']) || '—';
const appointmentAt = fieldValue(['客户预约时间', '预约时间']) || '—';
const technician = fieldValue(['服务人员', '安装人员', '服务工程师']) || '—';
```

Render these values in a second `service-detail-section` named “服务信息”.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_frontend_contract.py' -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html tests/test_frontend_contract.py
git commit -m "feat: show service order schedule details"
```

### Task 2: 服务单安装模板 XLSX 接口

**Files:**
- Modify: `app.py:6891-6948,7897-7973`
- Test: `tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: `POST /api/service-orders/export/xlsx` body `{"barcodes": [str]}`。
- Produces: JSON `{success: bool, exported_count: int, skipped_count: int, filename: str}` and `service_order_install_export.xlsx`.

- [ ] **Step 1: Write the failing tests**

```python
response = self.client.post('/api/service-orders/export/xlsx', json={'barcodes': ['TEST001', 'TEST002']})
self.assertTrue(response.get_json()['success'])
self.assertEqual(workbook.active['B1'].value, '用户姓名')
self.assertEqual(workbook.active['E3'].value, '2026-08-01 09:00')
self.assertEqual(workbook.active['K3'].value, '2026-08-03 10:00')
self.assertEqual(workbook.active['L3'].value, '张工')
```

Use two source barcodes for the same installation order and assert only one output data row is written. Add a no-saved-detail case and assert it is skipped.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_export_xlsx.py' -q`

Expected: FAIL because the route does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
@app.route('/api/service-orders/export/xlsx', methods=['POST'])
def api_export_service_orders_xlsx():
    barcodes = normalize_input_barcodes((request.get_json() or {}).get('barcodes') or [])
    rows, skipped_count = _service_order_install_export_rows(barcodes)
    return _write_service_order_install_xlsx(rows, skipped_count)
```

`_service_order_install_export_rows` selects `_latest_service_record`, de-duplicates by `service_no`, reads `SERVICE_ORDER_DIR/<service_no>.json`, and maps template columns B–U. Use a label lookup helper for detail fields; format “备注” as `服务单号：<no>`; map acceptance date to E, appointment date to N, technician to L. Build a new workbook with the template header text and write output from row 3 without copying the template's historical data.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_export_xlsx.py' -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_export_xlsx.py
git commit -m "feat: export service orders in install template"
```

### Task 3: 结果页导出入口

**Files:**
- Modify: `templates/index.html:1377-1386,2009-2035`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `getSelectedBarcodeArray()`, `getCurrentFilteredBarcodes()` and `/api/service-orders/export/xlsx`.
- Produces: `exportServiceOrdersXlsx()` that initiates download and reports exported/skipped counts.

- [ ] **Step 1: Write the failing test**

```python
self.assertIn('onclick="exportServiceOrdersXlsx()"', results)
self.assertIn("fetch('/api/service-orders/export/xlsx'", results)
self.assertIn('getSelectedBarcodeArray()', results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_frontend_contract.py' -q`

Expected: FAIL because the export button and function do not exist.

- [ ] **Step 3: Write minimal implementation**

```javascript
async function exportServiceOrdersXlsx() {
    const selected = getSelectedBarcodeArray();
    const barcodes = selected.length ? selected : getCurrentFilteredBarcodes().map(item => item.barcode);
    const data = await fetch('/api/service-orders/export/xlsx', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({barcodes})}).then(r => r.json());
    if (data.success) window.open(BARCODE_DIR + data.filename, '_blank');
}
```

Use the same disabled/exporting state pattern as `exportXlsx()` and include the skipped count in the success toast.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest discover -s tests -p 'test_frontend_contract.py' -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html tests/test_frontend_contract.py
git commit -m "feat: add service order export action"
```

### Task 4: Full regression verification

**Files:**
- Verify only: `tests/test_*.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: evidence that existing query, export, background-job and frontend contracts remain intact.

- [ ] **Step 1: Run all test modules**

Run:

```bash
for test_file in tests/test_*.py; do
  .venv/bin/python -m unittest discover -s tests -p "$(basename "$test_file")" -q || exit 1
done
```

Expected: every module passes.

- [ ] **Step 2: Verify the working tree**

Run: `git diff --check && git status --short --branch`

Expected: no whitespace errors and only intended changes before the final commit.

- [ ] **Step 3: Commit**

```bash
git add app.py templates/index.html tests/test_export_xlsx.py tests/test_frontend_contract.py
git commit -m "feat: complete service order details export"
```
