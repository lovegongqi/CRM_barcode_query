# GYJ 采购入库表单适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让后台 GYJ 入库在采购入库页面等待工具栏“新增”，按真实表格行录入物料与序列号，并点击唯一的普通保存按钮。

**Architecture:** `GYJPurchaseInboundWriter` 保持业务明细分组和“仅保存”约束；`GYJPlaywrightPage` 只适配已验证的 Ant Design 可见 DOM。所有选择器都限定在当前可见采购入库弹窗或其表格行内，不读取令牌、cookie 或本地存储。

**Tech Stack:** Python 3、Playwright Sync API、unittest、Ant Design Pro。

## Global Constraints

- 只点击 `保存（Ctrl+S）` 普通保存，不点击保存并审核、审核、提交或转采购退货。
- 供应商固定为昆山怡口净水系统有限公司；结算账户为江西天麓；行仓库为沈桥仓。
- 有序列号时，按每行最多 100 个、最多 2000 字符分块，不手填数量。
- 不读取或复制浏览器 token、cookie、localStorage、密码或会话文件。

---

### Task 1: 等待新增按钮并适配真实普通保存按钮

**Files:**
- Modify: `gyj_inbound.py:111-116,229-240`
- Modify: `tests/test_gyj_inbound.py:86-205`

**Interfaces:**
- Consumes: `GYJPlaywrightPage.open_new_form()` 和 `GYJPlaywrightPage.click_plain_save()`。
- Produces: 在采购入库工具栏渲染后点击 `.table-operator button.ant-btn-primary`，并仅点击 `保存（Ctrl+S）`。

- [x] **Step 1: Write the failing test**

```python
def test_waits_for_purchase_inbound_new_button_before_clicking(self):
    page = _PurchaseInboundListPage()
    adapter = GYJPlaywrightPage(page)
    adapter.open_new_form()
    self.assertTrue(page.new_button.clicked)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_gyj_inbound.GYJPurchaseInboundPageTest -v`

Expected: FAIL because the old immediate `get_by_text("新增")` lookup runs before the toolbar button is available.

- [x] **Step 3: Write minimal implementation**

```python
new_button = self.page.locator(".table-operator button.ant-btn-primary")
new_button.wait_for(state="visible", timeout=15000)
new_button.click()
```

Use `self.form.get_by_text("保存（Ctrl+S）", exact=True)` for the explicit ordinary save action.

- [x] **Step 4: Run focused tests to verify passing behavior**

Run: `python3 -m unittest tests.test_gyj_inbound -v`

Expected: PASS, including the new delayed-toolbar test and the no-审核 assertion.

- [x] **Step 5: Commit**

```bash
git add gyj_inbound.py tests/test_gyj_inbound.py docs/superpowers/plans/2026-08-13-gyj-purchase-inbound-form.md
git commit -m "fix: wait for GYJ purchase inbound controls"
```

### Task 2: 以采购入库表格行为界定物料、仓库和序列号操作

**Files:**
- Modify: `gyj_inbound.py:119-225`
- Modify: `tests/test_gyj_inbound.py`

**Interfaces:**
- Consumes: `add_product_line(line)`，其中 `line` 有 `product_code`、`serials` 和 `quantity`。
- Produces: 每个序列号块在独立行中以物料编码选择，序列号通过该行放大镜批量录入；无条码配件填其数量。

- [x] **Step 1: Write failing row-scoped behavior tests**

```python
def test_serial_input_uses_the_current_table_row_magnifier(self):
    adapter = GYJPlaywrightPage(row_scoped_page)
    adapter._fill_serials(row_scoped_page.row, ["SN001"])
    self.assertTrue(row_scoped_page.row.serial_magnifier.clicked)
    self.assertFalse(row_scoped_page.order_search_magnifier.clicked)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_gyj_inbound.GYJPurchaseInboundPageTest -v`

Expected: FAIL if a global or 关联订单放大镜 is selected instead of the row-scoped serial magnifier.

- [x] **Step 3: Write minimal implementation**

```python
serial_button = row.locator('.ant-input-search-icon')
serial_button.click()
```

Keep the `多个序列号` textarea selector exact and restrict all option/modal actions to `.ant-modal:visible`.

- [x] **Step 4: Run focused and full verification**

Run: `python3 -m unittest discover -s tests -p 'test_*.py' -v && python3 -m py_compile app.py gyj_inbound.py && git diff --check`

Expected: all tests pass, syntax check exits 0, diff has no whitespace errors.

- [x] **Step 5: Commit**

```bash
git add gyj_inbound.py tests/test_gyj_inbound.py docs/superpowers/plans/2026-08-13-gyj-purchase-inbound-form.md
git commit -m "fix: scope GYJ inbound row entry"
```
