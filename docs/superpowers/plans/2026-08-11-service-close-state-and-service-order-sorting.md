# 批量结单状态恢复与服务单号排序 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让结果页在同一浏览器标签切页返回后恢复批量结单任务，并仅按最新安装服务单号聚合条码卡片。

**Architecture:** 结果页在 `sessionStorage` 中仅保存批量结单任务的 ID、通道 ID 和开始时间；页面加载时用既有状态接口重新读取完整保留日志并恢复轮询。前后端分别用同一“服务类型含安装”规则筛除维修和保养服务单：后端批量结单只选择最新安装单，前端卡片与排序只使用最新安装单。

**Tech Stack:** Flask、原生浏览器 JavaScript、Python `unittest`。

## Global Constraints

- 任务恢复仅限启动任务的同一浏览器标签，不新增跨账号任务发现接口。
- 恢复任务时从服务端保留日志的开头读取，避免遗漏离开页面前的日志。
- 一个条码有多个安装服务单时，只按卡片当前显示的最新安装服务单号分组。
- 维修、保养及其它非安装服务单不参与卡片服务单号、排序或批量结单。
- 空安装服务单号始终排在有安装服务单号条码之后。

---

### Task 1: 添加结果页行为契约

**Files:**
- Modify: `tests/test_frontend_contract.py`, `tests/test_background_jobs.py`
- Test: `tests/test_frontend_contract.py`, `tests/test_background_jobs.py`

**Interfaces:**
- Consumes: `templates/index.html` 的结果页脚本源码。
- Produces: 三个防回归断言，分别约束任务恢复、安装服务单筛选和服务单号主排序。

- [ ] **Step 1: 写入失败的任务恢复契约测试**

在 `FrontendContractTest` 中新增：

```python
def test_results_page_restores_service_close_job_from_session(self):
    source = self.source("index.html")
    for token in (
        "const SERVICE_CLOSE_SESSION_KEY = 'crm_service_close_job_v1'",
        "function saveServiceCloseJob()",
        "function clearSavedServiceCloseJob()",
        "function restoreServiceCloseJob()",
        "sessionStorage.setItem(SERVICE_CLOSE_SESSION_KEY",
        "sessionStorage.removeItem(SERVICE_CLOSE_SESSION_KEY)",
        "restoreServiceCloseJob();",
    ):
        with self.subTest(token=token):
            self.assertIn(token, source)
```

- [ ] **Step 2: 运行任务恢复测试，确认其失败**

Run: `.venv/bin/python -m unittest tests.test_frontend_contract.FrontendContractTest.test_results_page_restores_service_close_job_from_session -v`

Expected: FAIL，因为结果页尚未定义会话键和恢复函数。

- [ ] **Step 3: 写入失败的服务单主排序契约测试**

在同一测试类中新增：

```python
def test_results_page_groups_barcodes_by_latest_service_order(self):
    source = self.source("index.html")
    for token in (
        "function isInstallationServiceOrder(row)",
        "function getServiceOrderSortKey(item)",
        "const aServiceNo = getServiceOrderSortKey(a)",
        "const bServiceNo = getServiceOrderSortKey(b)",
        "aServiceNo.localeCompare(bServiceNo, undefined, {numeric: true})",
    ):
        with self.subTest(token=token):
            self.assertIn(token, source)
```

- [ ] **Step 4: 运行服务单排序测试，确认其失败**

Run: `.venv/bin/python -m unittest tests.test_frontend_contract.FrontendContractTest.test_results_page_groups_barcodes_by_latest_service_order -v`

Expected: FAIL，因为当前排序没有安装服务单筛选，也仅按多服务单结单状态和查询时间排序。

- [ ] **Step 5: 写入失败的后端安装服务单选单测试**

在 `BackgroundJobTests` 中新增：

```python
def test_service_close_uses_latest_installation_order_only(self):
    fields = {
        "sr2": [
            {"servno1": "INSTALL-20260701", "typestr1": "安装", "servdate1": "2026-07-01"},
            {"servno1": "REPAIR-20260730", "typestr1": "维修", "servdate1": "2026-07-30"},
            {"servno1": "MAINT-20260731", "typestr1": "保养", "servdate1": "2026-07-31"},
        ]
    }

    latest = app_module._latest_service_record(fields)

    self.assertEqual(latest["service_no"], "INSTALL-20260701")
```

- [ ] **Step 6: 运行后端安装服务单选单测试，确认其失败**

Run: `.venv/bin/python -m unittest tests.test_background_jobs.BackgroundJobTests.test_service_close_uses_latest_installation_order_only -v`

Expected: FAIL，当前 `_latest_service_record` 会选择日期更晚的保养服务单。

### Task 2: 恢复批量结单任务、筛除非安装服务单并排序

**Files:**
- Modify: `app.py:6315-6324`, `templates/index.html:1341-1345,1618-1638,1752-1764,1958-2157,2395-2398`
- Test: `tests/test_background_jobs.py`, `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `/api/service-close/status?job_id=<id>&slot_id=<slot>&since=<sequence>` 的 `running`、`current`、`total`、`logs`、`results` 和最终统计字段。
- Produces: `_is_installation_service_row(row)`、`saveServiceCloseJob()`、`clearSavedServiceCloseJob()`、`restoreServiceCloseJob()`、`isInstallationServiceOrder(row)` 和 `getServiceOrderSortKey(item)`。

- [ ] **Step 1: 实现会话任务保存与恢复**

在现有批量结单状态变量后定义 `SERVICE_CLOSE_SESSION_KEY`。增加保存、清除和恢复函数：保存 `jobId`、`slotId`、`startedAtMs`；恢复时将 `lastServiceCloseLogSeq` 设为 `0`、清空日志区域、恢复变量和禁用按钮，然后调用 `pollServiceCloseStatus()`。若任务仍在运行，重建 1.2 秒轮询；若读取失败，保留会话数据以便下次重试。

在 `/api/service-close/start` 成功后、赋值 `serviceCloseJobId` 和 `serviceCloseSlotId` 后调用 `saveServiceCloseJob()`；启动新任务前和状态接口报告任务已结束后调用 `clearSavedServiceCloseJob()`。在页面初始化的 `loadAllData()` 后调用 `restoreServiceCloseJob()`。

在 `pollServiceCloseStatus()` 的运行中分支将按钮文本更新为 `结单中 current/total...`，使恢复后的实时进度可见；结束分支沿用现有结果应用和刷新逻辑。

- [ ] **Step 2: 前后端均筛除非安装服务单**

在 `app.py` 增加：

```python
def _is_installation_service_row(row):
    return "安装" in _clean_export_value((row or {}).get("typestr1"))
```

在 `_latest_service_record()` 中跳过 `_is_installation_service_row(row)` 为假的行，保证 `selected_latest_service_orders()` 只把最新安装服务单交给批量结单任务。

在 `templates/index.html` 的 `getLatestServiceNo(rows)` 前增加：

```javascript
function isInstallationServiceOrder(row) {
    return String((row && row.typestr1) || '').trim().includes('安装');
}
```

并在 `getLatestServiceNo(rows)` 遍历时跳过非安装行，保证卡片当前显示的服务单号只来自安装服务单。

- [ ] **Step 3: 实现按最新安装服务单号分组的主排序**

在 `getFilteredBarcodes()` 前增加：

```javascript
function getServiceOrderSortKey(item) {
    const fields = (item && item.fields) || {};
    const sr2 = fields.sr2;
    const rows = Array.isArray(sr2) ? sr2 : (sr2 ? [sr2] : []);
    return getLatestServiceNo(rows);
}
```

在现有排序回调开始处先比较 `aServiceNo` 和 `bServiceNo`。两个都非空时用 `localeCompare(..., undefined, {numeric: true})`；只有一个非空时非空服务单号优先；相同服务单号或两者为空时继续执行既有的多服务单结单优先和查询时间倒序逻辑。

- [ ] **Step 4: 运行三个新测试，确认转绿**

Run: `.venv/bin/python -m unittest tests.test_background_jobs.BackgroundJobTests.test_service_close_uses_latest_installation_order_only tests.test_frontend_contract.FrontendContractTest.test_results_page_restores_service_close_job_from_session tests.test_frontend_contract.FrontendContractTest.test_results_page_groups_barcodes_by_latest_service_order -v`

Expected: PASS，后端只选择安装服务单，结果页源码包含恢复、安装筛选和排序行为。

- [ ] **Step 5: 运行相关完整测试集**

Run: `.venv/bin/python -m unittest tests.test_frontend_contract tests.test_frontend_routes tests.test_background_jobs -v`

Expected: PASS，结果页契约、路由和后台任务测试均通过。

- [ ] **Step 6: 验证页面可用并提交**

Run: `curl -fsS -L -o /tmp/crm_results.html -w '%{http_code} %{url_effective}\n' http://127.0.0.1:5002/`

Expected: `200`，并最终落在登录页或已登录结果页；随后执行：

```bash
git add templates/index.html tests/test_frontend_contract.py docs/superpowers/plans/2026-08-11-service-close-state-and-service-order-sorting.md docs/superpowers/specs/2026-08-11-service-close-state-and-service-order-sorting-design.md
git commit -m "fix: restore service close state and group results"
```
