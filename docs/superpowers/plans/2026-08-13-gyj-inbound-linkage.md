# GYJ 采购入库联动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已成功读取的 CRM 装箱单在 GYJ 登录后自动创建、核对并普通保存一张采购入库单。

**Architecture:** `gyj_inbound.py` 只把 CRM 结果转换成可保存的 GYJ 行，并驱动已登录 GYJ 页面完成新增、选商品、序列号、校验和普通保存。`app.py` 持有独立 GYJ Playwright worker、账号隔离的登录/保存任务和 API；`templates/inbound.html` 在 CRM 与 GYJ 工作页之间切换并显示状态。

**Tech Stack:** Python 3.14、Flask、Playwright sync API、原生 JavaScript、unittest。

## Global Constraints

- 只允许当前账号最近一次成功的 CRM 入库提取结果作为来源，不接收前端结果行。
- 供应商固定为 `昆山怡口净水系统有限公司`；结算账户为 `江西天麓`；仓库为 `沈桥仓`；备注为 `装箱单号：<SH...>`。
- 有条码物料每行最多 100 个条码且英文逗号连接后不超过 2000 字符；无条码配件按数量单独建行。
- 保存前任一校验失败立即停止且不得点击保存。
- 只点击普通 `保存`；不得点击 `保存并审核`、`提交`、`审核`、`转采购退货`。
- 不保存 GYJ 密码、会话、来源结果历史或 CRM 原始数据。
- 不刷新、关闭或删除正在填写的 GYJ 采购入库页。

---

### Task 1: GYJ 行准备与保存前校验

**Files:**
- Create: `gyj_inbound.py`
- Create: `tests/test_gyj_inbound.py`

**Interfaces:**
- Produces: `GYJInboundError(RuntimeError)`
- Produces: `build_gyj_purchase_lines(result) -> list[dict]`
- Produces: `validate_gyj_purchase_lines(result, lines) -> None`
- Each returned line has `product_code`, `description`, `serials`, `quantity`, `record_type` and `source_order_numbers`.

- [ ] **Step 1: Write the failing line-preparation tests**

```python
def test_splits_200_serials_into_two_100_serial_lines(self):
    result = {"items": [{"product_code": "926019528", "description": "滤芯", "serials": [f"SN{i:03d}" for i in range(200)], "expected_quantity": 200, "quantity_mismatch": False, "unbarcoded_quantity": 0}], "duplicate_serials": []}
    lines = build_gyj_purchase_lines(result)
    self.assertEqual([len(line["serials"]) for line in lines], [100, 100])
    self.assertTrue(all(line["quantity"] == len(line["serials"]) for line in lines))

def test_keeps_unbarcoded_accessory_as_quantity_line(self):
    result = {"items": [{"product_code": "247296319", "description": "面贴", "serials": [], "expected_quantity": 10, "quantity_mismatch": False, "unbarcoded_quantity": 10}], "duplicate_serials": []}
    line = build_gyj_purchase_lines(result)[0]
    self.assertEqual(line["serials"], [])
    self.assertEqual(line["quantity"], 10)

def test_rejects_duplicate_source_result(self):
    with self.assertRaisesRegex(GYJInboundError, "重复条码"):
        build_gyj_purchase_lines({"items": [], "duplicate_serials": ["SN001"]})
```

- [ ] **Step 2: Run the test to verify RED**

Run: `python3 -m unittest tests.test_gyj_inbound -v`

Expected: `ModuleNotFoundError: No module named 'gyj_inbound'`.

- [ ] **Step 3: Implement the minimal pure transformation**

```python
def build_gyj_purchase_lines(result):
    if result.get("duplicate_serials"):
        raise GYJInboundError("存在重复条码，不能创建 GYJ 入库单")
    # Preserve item order, split serials by both caps, then append quantity-only lines.
```

Use `result["items"]`; reject any `quantity_mismatch`, missing product code, empty item list, or serial chunk beyond 100/2000 characters. Use `record_type="条码"` or `"无条码配件"`.

- [ ] **Step 4: Run Task 1 tests to verify GREEN**

Run: `python3 -m unittest tests.test_gyj_inbound -v`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add gyj_inbound.py tests/test_gyj_inbound.py
git commit -m "feat: prepare GYJ inbound lines"
```

### Task 2: GYJ browser adapter and dedicated worker

**Files:**
- Modify: `app.py:90-770,3400-3665`
- Modify: `gyj_inbound.py`
- Test: `tests/test_gyj_inbound.py`

**Interfaces:**
- Produces: `GYJPurchaseInboundWriter(page, log=None, progress=None).save_packing_slip(packing_slip_no, lines) -> dict`
- Produces: `GYJSession.open_login() -> tuple[bool, str]`
- Produces: `GYJSession.check_login_status() -> tuple[bool, str]`
- Produces: `GYJSession.save_purchase_inbound(packing_slip_no, lines, log=None, progress=None) -> tuple[bool, object]`
- Produces: `GYJWorker.open_login()`, `GYJWorker.check_login_status()`, `GYJWorker.save_purchase_inbound(...)`.

- [ ] **Step 1: Write the failing browser-contract tests**

```python
def test_writer_uses_only_plain_save_after_verification(self):
    page = FakeGYJPage()
    writer = GYJPurchaseInboundWriter(page)
    writer.save_packing_slip("SH202607210002", prepared_lines)
    self.assertEqual(page.clicked, ["新增", "保存"])
    self.assertNotIn("保存并审核", page.clicked)

def test_writer_stops_before_save_when_product_lookup_fails(self):
    page = FakeGYJPage(product_found=False)
    with self.assertRaisesRegex(GYJInboundError, "未找到物料编码"):
        GYJPurchaseInboundWriter(page).save_packing_slip("SH202607210002", prepared_lines)
    self.assertNotIn("保存", page.clicked)
```

- [ ] **Step 2: Run the contract tests to verify RED**

Run: `python3 -m unittest tests.test_gyj_inbound.GYJInboundWriterTest -v`

Expected: `ImportError` for `GYJPurchaseInboundWriter`.

- [ ] **Step 3: Implement the page writer**

Implement these exact visible operations in `GYJPurchaseInboundWriter`:

```python
writer.open_new_form()
writer.select_header("供应商", "昆山怡口净水系统有限公司")
writer.select_header("结算账户", "江西天麓")
writer.select_header("仓库", "沈桥仓")
writer.fill_remark(f"装箱单号：{packing_slip_no}")
writer.add_product_line(line)
writer.verify_form(lines)
writer.click_plain_save()
```

Scope every modal selector to `.ant-modal:visible`; use the row 商品 magnifier, never the barcode dropdown; use the row sequence-number magnifier for serial lines; call `批量添加` and the visible modal `确定`. `click_plain_save` must match exact `保存` text and reject labels containing `审核`, `提交`, `转采购退货`.

- [ ] **Step 4: Add a dedicated GYJ session worker in `app.py`**

Use an independent persistent Playwright context in `GYJSession`, with a task queue in `GYJWorker` mirroring `CRMWorker`. `open_login` opens only `https://cloud.gyjerp.com/user/login`; `check_login_status` recognizes a logged-in GYJ page without reading credentials. Do not call CRM login code or session directories. `save_purchase_inbound` first checks login, then delegates to `GYJPurchaseInboundWriter`.

- [ ] **Step 5: Run Task 2 tests to verify GREEN**

Run: `python3 -m unittest tests.test_gyj_inbound -v`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add app.py gyj_inbound.py tests/test_gyj_inbound.py
git commit -m "feat: automate GYJ purchase inbound save"
```

### Task 3: Account-isolated GYJ login and save jobs

**Files:**
- Modify: `app.py:3930-4970,8870-9040`
- Modify: `tests/test_inbound_routes.py`

**Interfaces:**
- Produces routes: `POST /api/inbound/gyj/login`, `GET /api/inbound/gyj/login-status`, `POST /api/inbound/gyj/start`, `GET /api/inbound/gyj/status`.
- Produces: `_inbound_gyj_status_payload(job, owner) -> dict`.

- [ ] **Step 1: Write failing API tests with a fake GYJ worker**

```python
def test_gyj_start_requires_successful_current_owner_inbound_result(self):
    response = self.admin.post("/api/inbound/gyj/start")
    self.assertEqual(response.status_code, 409)
    self.assertIn("成功装箱单", response.get_json()["error"])

def test_gyj_job_uses_server_result_and_returns_saved_order(self):
    self._install_successful_inbound_result_for_admin()
    with mock.patch.object(app_module, "gyj_worker", FakeGYJWorker()):
        started = self.admin.post("/api/inbound/gyj/start")
        done = self._wait_for_gyj_job(self.admin, started.get_json()["job_id"])
    self.assertTrue(done["success"])
    self.assertEqual(done["result"]["packing_slip_no"], PACKING_SLIP_NO)

def test_other_account_cannot_read_gyj_job(self):
    self.assertEqual(self.other.get(f"/api/inbound/gyj/status?job_id={job_id}").status_code, 404)
```

- [ ] **Step 2: Run API tests to verify RED**

Run: `python3 -m unittest tests.test_inbound_routes.InboundRouteTest.test_gyj_start_requires_successful_current_owner_inbound_result -v`

Expected: 404 because `/api/inbound/gyj/start` does not exist.

- [ ] **Step 3: Implement GYJ jobs and routes**

Create `inbound_gyj_job_lock`, `inbound_gyj_jobs`, and `latest_inbound_gyj_job_by_owner`. `start` obtains the owner's latest successful inbound job, calls `build_gyj_purchase_lines` on the server-side result only, rejects malformed data before starting, and then launches a daemon job. `login` calls `gyj_worker.open_login` without credentials. `status` is owner-isolated. Keep no completed job history beyond the owner's latest job, and reject a second running job with 409.

- [ ] **Step 4: Run Task 3 tests to verify GREEN**

Run: `python3 -m unittest tests.test_inbound_routes -v`

Expected: all inbound and GYJ route tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add app.py tests/test_inbound_routes.py
git commit -m "feat: add GYJ inbound job APIs"
```

### Task 4: CRM/GYJ work-page UI

**Files:**
- Modify: `templates/inbound.html:1-320`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_frontend_routes.py`

**Interfaces:**
- Produces buttons with ids `showCrmInboundBtn`, `showGyjInboundBtn`, `gyjLoginBtn`, `startGyjInboundBtn`.
- Consumes `/api/inbound/gyj/login`, `/api/inbound/gyj/login-status`, `/api/inbound/gyj/start`, `/api/inbound/gyj/status`.

- [ ] **Step 1: Write failing template contracts**

```python
def test_inbound_template_has_crm_and_gyj_work_buttons(self):
    source = read_template("inbound.html")
    self.assertIn('id="showCrmInboundBtn"', source)
    self.assertIn('id="showGyjInboundBtn"', source)
    self.assertIn('id="gyjLoginBtn"', source)
    self.assertIn('id="startGyjInboundBtn"', source)
    self.assertIn('/api/inbound/gyj/start', source)
```

- [ ] **Step 2: Run template contracts to verify RED**

Run: `python3 -m unittest tests.test_frontend_contract -v`

Expected: fail because GYJ work-page controls are absent.

- [ ] **Step 3: Implement the work-page switch and GYJ panel**

Put the `CRM装箱单` and `GYJ采购入库` buttons in `.aurora-account-status`, before the account name. Keep the CRM form and results inside a CRM panel. Add a hidden GYJ panel that displays: login status, source packing slip, supplier, settlement account, warehouse, validation/save stage, job log, and the two GYJ buttons. Request GYJ login status on page load and poll a running GYJ job every second. Disable the save button until login is confirmed and the API accepts a successful current CRM result. Do not put credentials in the DOM.

- [ ] **Step 4: Run Task 4 tests to verify GREEN**

Run: `python3 -m unittest tests.test_frontend_contract tests.test_frontend_routes -v`

Expected: all template and route tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add templates/inbound.html tests/test_frontend_contract.py tests/test_frontend_routes.py
git commit -m "feat: add GYJ inbound workspace"
```

### Task 5: Full verification and live GYJ acceptance

**Files:**
- Modify: no source files expected

- [ ] **Step 1: Run full automated verification**

Run: `python3 -m unittest discover -s tests -p 'test_*.py' -q && git diff --check`

Expected: exit status 0.

- [ ] **Step 2: Run GYJ live acceptance after user login**

Use a completed CRM result. User completes GYJ login through the new button. Start a GYJ task, verify supplier `昆山怡口净水系统有限公司`, account `江西天麓`, warehouse `沈桥仓`, remark, every serial chunk, every quantity-only row, and a saved order number. Confirm no audit/submit action appears in logs.

- [ ] **Step 3: Commit only required source changes**

```bash
git status --short
git add app.py gyj_inbound.py templates/inbound.html tests/test_gyj_inbound.py tests/test_inbound_routes.py tests/test_frontend_contract.py tests/test_frontend_routes.py
git commit -m "feat: link packing slips to GYJ inbound"
```
