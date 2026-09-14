# Service Order Product Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recoverable background action that reads the single CRM order related to a service order and compares order quantities with service-order barcode-row counts by product code.

**Architecture:** Extend the existing `CRMSession` service-order flow with read-only store-order navigation and table extraction, then expose it through `CRMWorker`. A per-service background job obtains one shared query channel through the priority dispatcher, persists only successful order results inside the existing service-order JSON, and exposes start/status routes. The current service-detail modal polls that job and renders comparison totals and statuses without changing the existing close-service workflow.

**Tech Stack:** Python 3.11, Flask, Playwright, browser JavaScript embedded in `templates/index.html`, `unittest`/pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-service-order-product-comparison-design.md`

## Global Constraints

- Compare by normalized product code and aggregated quantity only; order rows have no barcode.
- Each service order has at most one related order.
- Service quantity is the count of service-product barcode rows for a product code.
- Keep each existing service barcode as its own displayed row.
- Add `订单数量`, `服务单数量`, and `对比结果` columns.
- Add order-only products as synthetic rows with barcode `—` and status `服务单缺少`.
- Do not automatically query orders during batch service closing.
- Do not modify CRM service orders or store orders.
- Failed refreshes must preserve the last successful cached order comparison.
- Reuse query channels; the new work priority is below purchase inbound and service closing.

---

### Task 1: Product-code quantity comparison

**Files:**
- Create: `tests/test_service_order_product_comparison.py`
- Modify: `app.py:8086-8170`

**Interfaces:**
- Consumes: service products shaped as `{product_name, product_code, barcode}` and order products shaped as `{product_name, product_code, quantity}`.
- Produces: `_normalize_comparison_product_code(value) -> str`, `_parse_order_product_quantity(value) -> int`, and `_build_service_order_product_comparison(service_products, order_products) -> list[dict]`.
- Each comparison row contains `product_code`, `product_name`, `order_quantity`, `service_quantity`, `status`, and `status_label`.

- [ ] **Step 1: Write failing comparison tests**

```python
import unittest

import app as app_module


class ServiceOrderProductComparisonTests(unittest.TestCase):
    def test_comparison_aggregates_codes_and_reports_all_four_business_states(self):
        service_products = [
            {"product_code": " ab-01 ", "product_name": "A", "barcode": "SN-A1"},
            {"product_code": "AB-01", "product_name": "A", "barcode": "SN-A2"},
            {"product_code": "B-02", "product_name": "B", "barcode": "SN-B1"},
            {"product_code": "D-04", "product_name": "D", "barcode": "SN-D1"},
        ]
        order_products = [
            {"product_code": "AB-01", "product_name": "A", "quantity": "1"},
            {"product_code": "ab-01", "product_name": "A", "quantity": "1.00"},
            {"product_code": "B-02", "product_name": "B", "quantity": 3},
            {"product_code": "C-03", "product_name": "C", "quantity": 1},
        ]

        rows = app_module._build_service_order_product_comparison(
            service_products, order_products
        )

        self.assertEqual(rows, [
            {"product_code": "AB-01", "product_name": "A", "order_quantity": 2,
             "service_quantity": 2, "status": "matched", "status_label": "一致"},
            {"product_code": "B-02", "product_name": "B", "order_quantity": 3,
             "service_quantity": 1, "status": "quantity_mismatch", "status_label": "数量不一致"},
            {"product_code": "D-04", "product_name": "D", "order_quantity": 0,
             "service_quantity": 1, "status": "order_missing", "status_label": "订单缺少"},
            {"product_code": "C-03", "product_name": "C", "order_quantity": 1,
             "service_quantity": 0, "status": "service_missing", "status_label": "服务单缺少"},
        ])

    def test_quantity_parser_rejects_missing_negative_and_fractional_values(self):
        for value in (None, "", "-1", "1.5", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    app_module._parse_order_product_quantity(value)

    def test_product_code_normalization_preserves_leading_zeroes(self):
        self.assertEqual(
            app_module._normalize_comparison_product_code(" 00ab-01 "),
            "00AB-01",
        )
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `PYTHONPATH=. pytest tests/test_service_order_product_comparison.py -q`

Expected: FAIL because the three comparison helpers do not exist.

- [ ] **Step 3: Implement the minimal pure comparison functions**

Add `from decimal import Decimal, InvalidOperation` with the existing standard-library imports, then add:

```python
def _normalize_comparison_product_code(value):
    return _clean_export_value(value).upper()


def _parse_order_product_quantity(value):
    text = _clean_export_value(value).replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"订单产品数量无法解析：{value}")
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError(f"订单产品数量必须是非负整数：{value}")
    return int(number)


def _build_service_order_product_comparison(service_products, order_products):
    service_counts = {}
    service_names = {}
    order_counts = {}
    order_names = {}
    service_order = []
    order_order = []
    for product in service_products or []:
        code = _normalize_comparison_product_code((product or {}).get("product_code"))
        if not code:
            continue
        if code not in service_counts:
            service_order.append(code)
        service_counts[code] = service_counts.get(code, 0) + 1
        service_names.setdefault(code, _clean_export_value((product or {}).get("product_name")))
    for product in order_products or []:
        code = _normalize_comparison_product_code((product or {}).get("product_code"))
        if not code:
            raise ValueError("订单产品缺少产品编码")
        if code not in order_counts:
            order_order.append(code)
        order_counts[code] = order_counts.get(code, 0) + _parse_order_product_quantity(
            (product or {}).get("quantity")
        )
        order_names.setdefault(code, _clean_export_value((product or {}).get("product_name")))
    rows = []
    for code in [*service_order, *[code for code in order_order if code not in service_counts]]:
        order_quantity = order_counts.get(code, 0)
        service_quantity = service_counts.get(code, 0)
        if not service_quantity:
            status, label = "service_missing", "服务单缺少"
        elif code not in order_counts:
            status, label = "order_missing", "订单缺少"
        elif order_quantity == service_quantity:
            status, label = "matched", "一致"
        else:
            status, label = "quantity_mismatch", "数量不一致"
        rows.append({
            "product_code": code,
            "product_name": service_names.get(code) or order_names.get(code) or "",
            "order_quantity": order_quantity,
            "service_quantity": service_quantity,
            "status": status,
            "status_label": label,
        })
    return rows
```

- [ ] **Step 4: Run the comparison tests and verify GREEN**

Run: `PYTHONPATH=. pytest tests/test_service_order_product_comparison.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the comparison unit**

```bash
git add app.py tests/test_service_order_product_comparison.py
git commit -m "feat: compare service and order product quantities"
```

---

### Task 2: Read the related store order through CRM

**Files:**
- Modify: `app.py:1590-2128, 2275-2325, 3520-3680`
- Modify: `tests/test_service_order_product_comparison.py`

**Interfaces:**
- Consumes: an authenticated `CRMSession`, a service number, and the existing service-detail page selectors.
- Produces: `CRMSession.query_related_order_products(service_no, log=None) -> tuple[bool, dict]` and the matching `CRMWorker` forwarding method.
- Success payload: `{service_no, order_no, service_fields, order_products}` where each order product has `product_name`, `product_code`, and integer `quantity`.

- [ ] **Step 1: Add failing tests for related-order selection and workflow output**

Add tests that exercise the worker orchestration while replacing only browser navigation boundaries:

```python
from unittest import mock


def make_worker():
    worker = object.__new__(app_module.CRMSession)
    worker.lock = __import__("threading").RLock()
    worker.logged_in = True
    worker.page = mock.Mock()
    return worker


class CRMRelatedOrderTests(unittest.TestCase):
    def test_related_order_number_uses_only_explicit_supported_labels(self):
        fields = [
            {"label": "备注", "value": "订单 SO-WRONG"},
            {"label": "关联订单号", "value": "SO20260914001"},
        ]
        self.assertEqual(
            app_module._related_order_no_from_service_fields(fields),
            "SO20260914001",
        )

    def test_query_related_order_products_returns_service_fields_and_order_rows(self):
        worker = make_worker()
        fields = [{"label": "关联订单号", "value": "SO20260914001"}]
        products = [{"product_name": "前置过滤器", "product_code": "916046216", "quantity": 2}]
        with mock.patch.object(worker, "is_alive", return_value=True), \
             mock.patch.object(worker, "_is_current_page_logged_in", return_value=True), \
             mock.patch.object(worker, "_open_service_order_list", return_value=(True, "")), \
             mock.patch.object(worker, "_search_service_order", return_value=(True, "")), \
             mock.patch.object(worker, "_open_service_order_detail", return_value=(True, "")), \
             mock.patch.object(worker, "_service_detail_fields", return_value=fields), \
             mock.patch.object(worker, "_open_store_order_list", return_value=(True, "")), \
             mock.patch.object(worker, "_search_store_order", return_value=(True, "")), \
             mock.patch.object(worker, "_open_store_order_detail", return_value=(True, "")), \
             mock.patch.object(worker, "_store_order_detail_products", return_value=products):
            ok, result = worker.query_related_order_products("FWD20260914001")
        self.assertTrue(ok)
        self.assertEqual(result, {
            "service_no": "FWD20260914001",
            "order_no": "SO20260914001",
            "service_fields": fields,
            "order_products": products,
        })
```

Add separate failure tests for no related order, exact order search failure, and empty product table. Each failure asserts the returned `error` text names the failed stage.

- [ ] **Step 2: Run the worker tests and verify RED**

Run: `PYTHONPATH=. pytest tests/test_service_order_product_comparison.py -q`

Expected: FAIL because `_related_order_no_from_service_fields` and the store-order worker methods do not exist.

- [ ] **Step 3: Implement explicit related-order extraction**

```python
RELATED_ORDER_FIELD_LABELS = ("关联订单号", "订单号", "销售订单号")


def _related_order_no_from_service_fields(fields):
    for wanted in RELATED_ORDER_FIELD_LABELS:
        for field in fields or []:
            if _clean_export_value((field or {}).get("label")).rstrip("：:") == wanted:
                return _clean_export_value((field or {}).get("value"))
    return ""
```

- [ ] **Step 4: Implement store-order browser boundaries**

Add these `CRMSession` methods adjacent to the service-order methods. Keep the scripts header-driven so they survive column reordering:

```python
def _store_order_list_ready(self):
    try:
        compact = re.sub(r"\s+", "", self.page.inner_text("body", timeout=3000) or "")
        return "订单列表" in compact and "订单号" in compact
    except Exception:
        return False


def _open_store_order_list(self, emit):
    emit("打开 CRM 门店管理订单列表...")
    if not self._click_visible_crm_text("门店管理"):
        return False, "未找到门店管理菜单"
    if not self._click_visible_crm_text("订单列表"):
        return False, "未找到门店管理订单列表"
    for _ in range(20):
        if self._store_order_list_ready():
            return True, ""
        time.sleep(0.5)
    return False, "未进入门店管理订单列表"


def _set_store_order_search_keyword(self, order_no):
    return bool(self.page.evaluate("""(orderNo) => {
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const clean = value => (value || '').replace(/\s+/g, '').trim();
        const items = Array.from(document.querySelectorAll('.el-form-item,.ant-form-item,.form-group'));
        let input = items.filter(visible).map(item => ({
            item,
            input: item.querySelector('input:not([disabled]),textarea:not([disabled])'),
            text: clean(item.innerText || item.textContent || '')
        })).find(row => row.input && row.text.includes('订单号'))?.input;
        if (!input) input = Array.from(document.querySelectorAll('input:not([disabled])'))
            .filter(visible).find(el => clean(el.placeholder).includes('订单号'));
        if (!input) return false;
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        input.focus();
        if (setter) setter.call(input, orderNo); else input.value = orderNo;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.setAttribute('data-codex-service-search', '1');
        return true;
    }""", str(order_no)))


def _click_store_order_search_button(self):
    return self._click_service_search_button()


def _store_order_search_snapshot(self, order_no):
    return self.page.evaluate("""(orderNo) => {
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const clean = value => (value || '').replace(/\s+/g, '').trim();
        const rows = Array.from(document.querySelectorAll('tbody tr')).filter(visible);
        const found = rows.some(row => Array.from(row.querySelectorAll('td'))
            .some(cell => clean(cell.innerText || cell.textContent || '') === clean(orderNo)));
        const loading = Array.from(document.querySelectorAll('.el-loading-mask,.ant-spin,[aria-busy="true"]'))
            .some(visible);
        const body = clean(document.body?.innerText || '');
        return {found, loading, noData: /暂无数据|无数据|暂无记录|没有数据/.test(body)};
    }""", str(order_no))


def _search_store_order(self, order_no):
    if not self._set_store_order_search_keyword(order_no):
        return False, "未找到订单号搜索输入框"
    if not self._click_store_order_search_button():
        return False, "未找到订单查询按钮"
    stable_empty = 0
    for _ in range(35):
        time.sleep(0.8)
        snapshot = self._store_order_search_snapshot(order_no) or {}
        if snapshot.get("found"):
            return True, ""
        if snapshot.get("loading"):
            stable_empty = 0
        elif snapshot.get("noData"):
            stable_empty += 1
            if stable_empty >= 4:
                return False, f"订单列表未找到订单号：{order_no}"
        else:
            stable_empty = 0
    return False, f"订单搜索后未找到精确订单号：{order_no}"


def _open_store_order_detail(self, order_no):
    clicked = self.page.evaluate("""(orderNo) => {
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const clean = value => (value || '').replace(/\s+/g, '').trim();
        const row = Array.from(document.querySelectorAll('tbody tr')).filter(visible)
            .find(tr => Array.from(tr.querySelectorAll('td'))
                .some(td => clean(td.innerText || td.textContent || '') === clean(orderNo)));
        const target = row && Array.from(row.querySelectorAll('a,button,td,span'))
            .filter(visible).find(el => clean(el.innerText || el.textContent || '') === clean(orderNo));
        const clickable = target && (target.closest('a,button') || target);
        if (!clickable) return false;
        clickable.click();
        return true;
    }""", str(order_no))
    if not clicked:
        return False, f"未找到可打开的订单：{order_no}"
    for _ in range(20):
        time.sleep(0.5)
        compact = re.sub(r"\s+", "", self.page.inner_text("body", timeout=3000) or "")
        if str(order_no).replace(" ", "") in compact and "产品" in compact:
            return True, ""
    return False, f"点击订单后未进入订单详情：{order_no}"


def _store_order_detail_products(self):
    rows = self.page.evaluate("""() => {
        const clean = value => (value || '').replace(/\s+/g, ' ').trim();
        const labels = {
            name: ['产品名称', '商品名称', '物料名称'],
            code: ['产品编码', '商品编码', '物料编码'],
            quantity: ['数量', '产品数量', '订单数量', '购买数量']
        };
        for (const table of document.querySelectorAll('.el-table,.ant-table,table')) {
            const headers = Array.from(table.querySelectorAll('thead th')).map(cell => clean(cell.innerText));
            const index = key => headers.findIndex(text => labels[key].some(label => text === label || text.includes(label)));
            const nameIndex = index('name');
            const codeIndex = index('code');
            const quantityIndex = index('quantity');
            if (codeIndex < 0 || quantityIndex < 0) continue;
            return Array.from(table.querySelectorAll('tbody tr')).map(tr => {
                const cells = Array.from(tr.querySelectorAll(':scope > td'));
                return {
                    product_name: nameIndex >= 0 && cells[nameIndex] ? clean(cells[nameIndex].innerText) : '',
                    product_code: cells[codeIndex] ? clean(cells[codeIndex].innerText) : '',
                    quantity: cells[quantityIndex] ? clean(cells[quantityIndex].innerText) : ''
                };
            }).filter(row => row.product_code || row.quantity);
        }
        return [];
    }""")
    products = []
    for row in rows or []:
        code = _normalize_comparison_product_code((row or {}).get("product_code"))
        if not code:
            raise ValueError("订单产品缺少产品编码")
        products.append({
            "product_name": _clean_export_value((row or {}).get("product_name")),
            "product_code": code,
            "quantity": _parse_order_product_quantity((row or {}).get("quantity")),
        })
    return products
```

Implementation requirements for the embedded page scripts:

- `_open_store_order_list` clicks visible `门店管理`, then visible `订单列表`, and validates the destination by the simultaneous presence of `订单列表` and `订单号` text.
- `_set_store_order_search_keyword` selects a visible enabled input whose label or placeholder contains `订单号`, sets its native value, and dispatches `input` and `change` events.
- `_store_order_search_snapshot` returns `{found, loading, noData}`; `found` is true only when a visible table row contains the complete normalized order number.
- `_open_store_order_detail` clicks the exact matching order-number link/button and waits for a page containing the same order number plus a product table.
- `_store_order_detail_products` locates table headers by labels: product name (`产品名称`, `商品名称`, `物料名称`), product code (`产品编码`, `商品编码`, `物料编码`), and quantity (`数量`, `产品数量`, `订单数量`, `购买数量`). It returns raw rows to Python, where `_parse_order_product_quantity` validates quantity and duplicate codes remain separate until Task 1 aggregation.

- [ ] **Step 5: Implement the authenticated read-only workflow**

```python
def query_related_order_products(self, service_no, log=None):
    def emit(message, level="info"):
        if log:
            log(message, level)
    try:
        service_no = _clean_export_value(service_no)
        if not service_no:
            return False, {"error": "服务单号为空"}
        with self.lock:
            if not self.is_alive() and not self._ensure_browser():
                return False, {"error": "浏览器未启动，请先登录 CRM"}
            if not self.logged_in and not self._is_current_page_logged_in():
                return False, {"error": "CRM 当前未登录，请先登录 CRM"}
            for operation in (
                lambda: self._open_service_order_list(emit),
                lambda: self._search_service_order(service_no),
                lambda: self._open_service_order_detail(service_no),
            ):
                ok, message = operation()
                if not ok:
                    return False, {"error": message}
            fields = self._service_detail_fields()
            order_no = _related_order_no_from_service_fields(fields)
            if not order_no:
                return False, {"error": "服务单没有关联订单号"}
            for operation in (
                lambda: self._open_store_order_list(emit),
                lambda: self._search_store_order(order_no),
                lambda: self._open_store_order_detail(order_no),
            ):
                ok, message = operation()
                if not ok:
                    return False, {"error": message}
            order_products = self._store_order_detail_products()
            if not order_products:
                return False, {"error": "订单详情未读取到产品明细"}
            return True, {
                "service_no": service_no,
                "order_no": order_no,
                "service_fields": fields,
                "order_products": order_products,
            }
    except Exception as error:
        return False, {"error": str(error)}
```

- [ ] **Step 6: Expose the method through `CRMWorker`**

Add a forwarding method next to `refresh_service_order_products`:

```python
def query_related_order_products(self, service_no, log=None):
    return self._call("query_related_order_products", service_no, log)
```

- [ ] **Step 7: Run the worker and existing service-order tests**

Run: `PYTHONPATH=. pytest tests/test_service_order_product_comparison.py tests/test_background_jobs.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the CRM reader**

```bash
git add app.py tests/test_service_order_product_comparison.py
git commit -m "feat: read related CRM order products"
```

---

### Task 3: Recoverable background job, cache, and API

**Files:**
- Modify: `app.py:4526-4580, 4740-4995, 7570-7650, 8138-8170, 9390-9420, 10235-10330`
- Modify: `tests/test_background_jobs.py`
- Modify: `tests/test_service_order_product_comparison.py`

**Interfaces:**
- Consumes: `CRMWorker.query_related_order_products(service_no, log)` and `_build_service_order_product_comparison(...)`.
- Produces: `POST /api/service-orders/<service_no>/order-products/start` and `GET /api/service-orders/<service_no>/order-products/status`.
- Successful service-detail JSON gains `order_lookup: {order_no, products, comparison, queried_at}`.

- [ ] **Step 1: Write failing job and route tests**

Add a fake worker:

```python
class FakeRelatedOrderWorker:
    def __init__(self, result, release=None):
        self.slot_id = "query-2"
        self.result = result
        self.release = release

    def query_related_order_products(self, service_no, log=None):
        if self.release:
            self.release.wait(timeout=2)
        return self.result
```

Add tests that:

1. Seed `service_orders/FWD20260914001.json` with two service product rows.
2. Start the route with an immediate fake priority dispatcher.
3. Poll status until done and assert `order_no`, `slot_label`, `success`, and `detail_url`.
4. Read the saved JSON and assert `order_lookup.products`, `order_lookup.comparison`, and `queried_at`.
5. Start a blocking job twice and assert the second request returns HTTP 409 with the running `job_id`.
6. Seed an old successful `order_lookup`, return a worker failure, and assert the file is byte-for-byte unchanged.

- [ ] **Step 2: Run route tests and verify RED**

Run: `PYTHONPATH=. pytest tests/test_background_jobs.py tests/test_service_order_product_comparison.py -q`

Expected: FAIL with 404 for the new endpoints.

- [ ] **Step 3: Add job state and priority registration**

Add `order_products: 3` without changing the existing inbound/service-close order:

```python
PRIORITY_QUERY_WORK_ORDER = {
    "inbound": 1,
    "service_close": 2,
    "order_products": 3,
    "library": 3,
}
order_product_job_lock = threading.RLock()
order_product_jobs = {}
latest_order_product_job_by_service = {}
```

Define `_empty_order_product_job(service_no)` with keys `job_id`, `service_no`, `order_no`, `slot_id`, `slot_label`, `running`, `done`, `success`, `stage`, `error`, `started_at`, and `finished_at`.

- [ ] **Step 4: Implement the background runner and success-only cache write**

Implement `_run_order_product_job(job_id, worker)` so it:

- reads the service number under `order_product_job_lock`;
- calls the worker and converts exceptions into a failed job;
- on success reads the current service-detail JSON, builds comparison rows from `detail.products` and returned order products, updates `detail.fields` from `service_fields`, and writes `detail.order_lookup`;
- writes the JSON only after extraction and comparison both succeed;
- updates the job to `done=True` and exposes the existing detail URL;
- leaves the JSON untouched for every failure path.

- [ ] **Step 5: Implement protected start and status routes**

The start route validates the service number, returns HTTP 409 for an existing running job, creates a waiting job, and queues this launcher:

```python
def launch(worker, slot_id, slot_label):
    with order_product_job_lock:
        current = order_product_jobs.get(job_id)
        current.update({
            "slot_id": slot_id,
            "slot_label": slot_label,
            "stage": "querying_service_order",
        })
    def run():
        try:
            _run_order_product_job(job_id, worker)
        finally:
            _release_priority_query_slot(slot_id)
    threading.Thread(target=run, daemon=True).start()

enqueue_priority_query_work("order_products", job_id, launch)
```

The status route returns the latest job for the path service number when `job_id` is omitted. Do not return private worker objects or internal timestamps.

- [ ] **Step 6: Normalize cached order data without discarding legacy fields**

Extend `_normalize_service_order_detail` so `order_lookup` defaults to an empty dictionary and its `products`/`comparison` values are lists. Do not synthesize a successful lookup for legacy files.

- [ ] **Step 7: Run backend tests and verify GREEN**

Run: `PYTHONPATH=. pytest tests/test_background_jobs.py tests/test_service_order_product_comparison.py tests/test_frontend_routes.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the background API**

```bash
git add app.py tests/test_background_jobs.py tests/test_service_order_product_comparison.py
git commit -m "feat: query order products in background"
```

---

### Task 4: Service-detail controls and comparison columns

**Files:**
- Modify: `templates/index.html:448-520, 1480-1500, 2510-2630`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `detail.order_lookup` and the two Task 3 endpoints.
- Produces: `startOrderProductQuery(serviceNo)`, `pollOrderProductQuery(serviceNo, jobId)`, and comparison-aware `renderServiceOrderDetail(detail)`.

- [ ] **Step 1: Write failing frontend contract tests**

Add assertions that the rendered page contains:

```python
def test_service_detail_can_query_and_compare_related_order_products(self):
    html = self.client.get("/").get_data(as_text=True)
    for text in (
        "查询订单产品明细",
        "订单数量",
        "服务单数量",
        "对比结果",
        "startOrderProductQuery",
        "pollOrderProductQuery",
        "/order-products/start",
        "/order-products/status",
        "服务单缺少",
    ):
        self.assertIn(text, html)
```

- [ ] **Step 2: Run the frontend contract test and verify RED**

Run: `PYTHONPATH=. pytest tests/test_frontend_contract.py::FrontendContractTest::test_service_detail_can_query_and_compare_related_order_products -q`

Expected: FAIL because the new button and columns are absent.

- [ ] **Step 3: Render comparison-aware service rows**

In `renderServiceOrderDetail`:

- create `comparisonByCode` from `detail.order_lookup.comparison`;
- for every service product, normalize its code with `trim().toUpperCase()`, then render order quantity, service quantity, and the status label from the matching comparison row;
- render `—`, `—`, `—` when no successful `order_lookup` exists;
- render `无法比对` for a service product with no code after a successful lookup;
- append one synthetic row for every comparison record with `status === 'service_missing'` and barcode `—`;
- retain the existing relation badge for real service rows and render no relation badge for synthetic rows.

Use CSS classes `is-matched`, `is-mismatch`, `is-order-missing`, `is-service-missing`, and `is-unmatchable` to style the result badge.

- [ ] **Step 4: Add the button and query metadata**

Render this control next to the existing refresh button:

```javascript
const orderQueryButton = `<button type="button" class="service-detail-refresh service-detail-order-query"
    data-service-no="${escapeHtml(detail.service_no || '')}"
    onclick="startOrderProductQuery(this.dataset.serviceNo)">查询订单产品明细</button>`;
```

When `detail.order_lookup.order_no` exists, show `关联订单：<number>` and `查询时间：<queried_at>` under the section header.

- [ ] **Step 5: Add start, status polling, and modal refresh**

Implement `startOrderProductQuery(serviceNo)` to POST the start endpoint, accept HTTP 409 by resuming the returned job, disable only the order-query button, and start polling.

Implement `pollOrderProductQuery(serviceNo, jobId)` to GET status every second while `running` is true. Map stages to `等待通道…` and `查询中…`. On success, fetch `/api/service-orders/<serviceNo>`, call `renderServiceOrderDetail`, and show the returned order number. On failure, restore the button and show the exact backend error. Stop updating the modal if its current service number has changed.

Call the status endpoint from `showServiceOrderDetail` after the initial detail render so a page refresh or reopened modal resumes a running query by service number.

- [ ] **Step 6: Run frontend and service-detail tests**

Run: `PYTHONPATH=. pytest tests/test_frontend_contract.py tests/test_background_jobs.py tests/test_service_order_product_comparison.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the frontend**

```bash
git add templates/index.html tests/test_frontend_contract.py
git commit -m "feat: show service and order product comparison"
```

---

### Task 5: Full verification and deployment

**Files:**
- Verify: `app.py`
- Verify: `templates/index.html`
- Verify: `tests/test_service_order_product_comparison.py`
- Verify: `tests/test_background_jobs.py`
- Verify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: all Task 1–4 deliverables.
- Produces: deployed CRM barcode-query release with rollback image retained.

- [ ] **Step 1: Run the full suite**

Run: `PYTHONPATH=. pytest -q`

Expected: all tests and subtests pass with exit code 0.

- [ ] **Step 2: Check the final diff and repository state**

Run: `git diff --check && git status -sb`

Expected: no whitespace errors; the branch contains only the planned commits and is ahead of `origin/main`.

- [ ] **Step 3: Push the reviewed commits**

Run: `git push origin main`

Expected: remote `main` advances without force-push.

- [ ] **Step 4: Deploy through the existing immutable release workflow**

Create a `git archive` named with the full commit SHA, verify its SHA-256 checksum after upload, extract it to `/volume1/docker/CRM_barcode_query/releases/<full-sha>`, tag the current image as a rollback image, build `crm-barcode-query:latest`, and recreate only the `crm-barcode-query` Compose service. Enter SSH and sudo credentials interactively; do not place them in commands, files, or shell history.

- [ ] **Step 5: Verify the remote release**

Verify all of the following with fresh commands:

- container state is `running`;
- Compose working-directory label equals the new immutable release directory;
- `crm_barcode_query_app_data` and `crm_barcode_query_browser_session` remain mounted;
- public `/login` returns HTTP 200 and unauthenticated `/` redirects to `/login`;
- container source contains the new start/status routes and frontend button;
- container logs show no startup exception.

- [ ] **Step 6: Perform authenticated read-only UI verification**

Open one saved service detail, click “查询订单产品明细”, and verify the button progresses through waiting/querying to completion. Confirm the exact related order number is displayed, the three new columns render, and at least one known matching or mismatch row agrees with a hand-count of the service barcodes and order quantity. Do not click any CRM save, close, or edit action during verification.
