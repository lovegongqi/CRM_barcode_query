# 装箱单历史与明细交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将成功读取的 CRM 装箱单存为共享且重启后保留的历史记录，并提供历史选择、GYJ 跳转、删除、物料折叠与复制操作。

**Architecture:** 在 `CONFIG_DIR` 新增 SQLite 快照库；成功读取任务完成时写入历史。受现有 `inbound` 权限保护的接口提供列表、详情和删除；前端加载历史并保留既有实时读取、Excel 下载和 GYJ 流程。

**Tech Stack:** Python 3、Flask、SQLite、原生 JavaScript、unittest。

## Global Constraints

- 所有工具账号共用历史，项目重启后仍保留。
- 只保存成功读取结果；相同装箱单号更新为最新快照。
- 删除只删除本工具 SQLite 快照，不影响 CRM、GYJ 或已建采购入库单。
- 保留既有逐页显示的“每页行数”。
- 产品不显示订单号；条码默认折叠；每批复制不超过 100 个、英文逗号分隔。

---

### Task 1: 持久化装箱单历史并提供接口

**Files:**

- Modify: `app.py:6040-6055`, `app.py:5280-5345`, `app.py:9360-9570`
- Modify: `tests/test_inbound_routes.py:1-250, 400-520`

**Interfaces:**

- Produces `load_inbound_history() -> list[dict]`、`get_inbound_history(packing_slip_no: str) -> dict | None`、`upsert_inbound_history(result: dict, finished_at: str) -> dict`、`delete_inbound_history(packing_slip_no: str) -> bool`。
- Produces `GET /api/inbound/history`、`GET /api/inbound/history/<packing_slip_no>` 和 `DELETE /api/inbound/history/<packing_slip_no>`。

- [ ] **Step 1: Write failing persistence and route tests**

```python
def test_inbound_history_survives_job_cleanup_and_is_shared(self):
    result = {"packing_slip_no": PACKING_SLIP_NO, "items": [{"product_code": "916000024"}]}
    app_module.upsert_inbound_history(result, "2026-08-13 16:00:00")
    app_module.inbound_jobs.clear()
    self.assertEqual(app_module.get_inbound_history(PACKING_SLIP_NO)["result"], result)
```

Add a route test that lists one stored history row, returns its full detail, deletes it, then receives 404 for detail. Use a second logged-in account to prove the shared list is visible to both accounts.

- [ ] **Step 2: Run the red tests**

Run `python3 -m unittest tests.test_inbound_routes.InboundRouteTest.test_inbound_history_survives_job_cleanup_and_is_shared tests.test_inbound_routes.InboundRouteTest.test_inbound_history_routes_list_detail_and_delete -v`.

Expected: FAIL because the store and endpoints do not exist.

- [ ] **Step 3: Implement the smallest SQLite store and API**

```python
INBOUND_HISTORY_DB_FILE = os.path.join(CONFIG_DIR, "inbound_history.sqlite3")

def _inbound_history_connection():
    os.makedirs(os.path.dirname(INBOUND_HISTORY_DB_FILE), exist_ok=True)
    connection = sqlite3.connect(INBOUND_HISTORY_DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection
```

Create `inbound_history` with `packing_slip_no` primary key, `read_at`, `page_counts_json`, `result_json`, and a compact `summary_json`. Upsert a deep-serializable copy after `build_inbound_result` succeeds. Preserve the existing in-memory completed-job purge. Add list/detail/delete endpoints alongside status/export, using the same inbound permission behavior and 404 for absent records.

- [ ] **Step 4: Run focused green tests**

Run `python3 -m unittest tests.test_inbound_routes.InboundRouteTest.test_inbound_history_survives_job_cleanup_and_is_shared tests.test_inbound_routes.InboundRouteTest.test_inbound_history_routes_list_detail_and_delete -v`.

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

Run `git add app.py tests/test_inbound_routes.py`.

Run `git commit -m "feat: persist packing slip history"`.

### Task 2: 历史工作台、折叠条码与复制操作

**Files:**

- Modify: `templates/inbound.html:19-58, 91-130, 176-355`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_frontend_routes.py:120-165`

**Interfaces:**

- Consumes list and detail endpoints from Task 1.
- Produces `loadInboundHistory()`、`selectInboundHistory(packingSlipNo)`、`deleteInboundHistory(packingSlipNo)`、`copyInboundText(value)`、`toggleInboundSerials(productId)`。

- [ ] **Step 1: Write failing frontend contracts**

```python
def test_inbound_page_has_shared_history_actions(self):
    inbound = self.source("inbound.html")
    self.assertIn('id="inboundHistory"', inbound)
    self.assertIn("function selectInboundHistory", inbound)
    self.assertIn("function deleteInboundHistory", inbound)
    self.assertIn("/api/inbound/history", inbound)

def test_inbound_products_fold_serials_and_copy_chunked_values(self):
    inbound = self.source("inbound.html")
    self.assertIn("function toggleInboundSerials", inbound)
    self.assertIn("serials.slice(index, index + 100)", inbound)
    self.assertIn("copyInboundText(item.product_code)", inbound)
    self.assertNotIn("· 订单 ${escapeHtml(orders", inbound)
```

- [ ] **Step 2: Run the red frontend contracts**

Run `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_inbound_page_has_shared_history_actions tests.test_frontend_contract.FrontendContractTest.test_inbound_products_fold_serials_and_copy_chunked_values -v`.

Expected: FAIL because history and batch-copy interactions are absent.

- [ ] **Step 3: Implement the minimum page behavior**

Replace only the CRM status-card area with `#inboundHistory`. A history number copies itself, fills `#packingSlipInput`, and fetches/renders its detail. Each row includes GYJ jump and delete actions; delete calls DELETE and clears the view only when it is the selected snapshot. Refresh history after a successful read. Render title as clickable code + name + total quantity, omit order numbers, use a toggle for initially hidden serials, and create one copy button per `serials.slice(index, index + 100).join(',')`. Keep page counts as direct rows.

- [ ] **Step 4: Run focused green tests**

Run `python3 -m unittest tests.test_frontend_contract tests.test_frontend_routes -v`.

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run `git add templates/inbound.html tests/test_frontend_contract.py tests/test_frontend_routes.py`.

Run `git commit -m "feat: add inbound history workspace"`.

### Task 3: 回归验证与本地视觉检查

**Files:**

- Verify only: `app.py`, `templates/inbound.html`, `tests/test_inbound_routes.py`, `tests/test_frontend_contract.py`, `tests/test_frontend_routes.py`

**Interfaces:**

- Consumes Tasks 1–2 completed endpoints and rendered page.

- [ ] **Step 1: Run inbound and frontend regression suites**

Run `python3 -m unittest tests.test_inbound_routes tests.test_frontend_contract tests.test_frontend_routes -v`.

Expected: PASS.

- [ ] **Step 2: Run complete suite and static checks**

Run `python3 -m unittest discover -s tests -v`.

Run `git diff --check`.

Expected: all tests pass and no whitespace errors.

- [ ] **Step 3: Restart local Flask process and inspect `/inbound`**

Verify no CRM/GYJ mutation; history loads; selection updates page rows; product body folds/unfolds; all required copy buttons exist.

- [ ] **Step 4: Commit only if verification reveals a code fix**

Run `git add app.py templates/inbound.html tests`.

Run `git commit -m "fix: verify inbound history workflow"`.
