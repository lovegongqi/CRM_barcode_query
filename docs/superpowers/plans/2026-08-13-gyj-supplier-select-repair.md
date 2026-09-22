# GYJ Supplier Select Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the project’s background GYJ purchase-inbound flow reliably recognize or select the default supplier without timing out on a detached global dropdown locator.

**Architecture:** Keep the writer contract unchanged. Adapt `GYJPlaywrightPage.select_header` so the supplier field first accepts its existing visible value; otherwise it uses the select trigger’s `aria-controls` / expanded state to scope the dropdown, then selects the supplier. No token access, default-header changes, or save behavior changes.

**Tech Stack:** Python, unittest, Playwright sync API used by the existing background worker.

## Global Constraints

- Only change supplier-header selection behavior.
- Preserve default warehouse and settlement-account behavior.
- Do not click 保存并审核, 提交, 审核, or access browser credential/token stores.
- Live verification stops before any save action.

---

### Task 1: Supplier select behavior

**Files:**
- Modify: `gyj_inbound.py:118-148`
- Modify: `tests/test_gyj_inbound.py:300-490`

**Interfaces:**
- Consumes: `GYJPlaywrightPage.select_header(label, value)`.
- Produces: supplier selection that returns when the visible default matches or chooses the option from the trigger-bound dropdown.

- [ ] **Step 1: Write the failing test**

```python
def test_keeps_supplier_when_the_form_already_shows_the_default(self):
    adapter = GYJPlaywrightPage(_ActualGYJSavePage())
    adapter.form = _SelectedSupplierForm("昆山怡口净水")

    adapter.select_header("供应商", "昆山怡口净水")

    self.assertFalse(adapter.form.trigger.clicked)
    self.assertEqual(adapter._headers, {"供应商": "昆山怡口净水"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_gyj_inbound.GYJPurchaseInboundPageTest.test_keeps_supplier_when_the_form_already_shows_the_default -v`

Expected: FAIL because the current implementation always waits for a global dropdown.

- [ ] **Step 3: Write the minimal implementation**

```python
selected = trigger.locator('.ant-select-selection-selected-value, .ant-select-selection-item').first
if selected.count() == 1 and selected.inner_text().strip() == value:
    self._headers[label] = value
    return
```

Then wait for the dropdown associated with the trigger’s `aria-controls` when present, falling back to the current visible dropdown locator only for the legacy control.

- [ ] **Step 4: Run focused verification**

Run: `python3 -m unittest tests.test_gyj_inbound -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gyj_inbound.py tests/test_gyj_inbound.py docs/superpowers/plans/2026-08-13-gyj-supplier-select-repair.md
git commit -m "fix: select GYJ default supplier reliably"
```

### Task 2: Project-level verification

**Files:**
- Modify: none

**Interfaces:**
- Consumes: the supplier selection change from Task 1.
- Produces: evidence that the project flow reaches the GYJ form without the dropdown timeout, stopping before plain save.

- [ ] **Step 1: Run full automated suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS with no failures or errors.

- [ ] **Step 2: Restart the local project process**

Run the existing Flask app from the repository so it loads the committed code.

- [ ] **Step 3: Live verify through the project UI**

Use the existing logged-in GYJ background session, start the purchase-inbound flow for the selected historical packing slip, and observe the progress until the supplier step completes. Stop before `保存` / `创建并保存` causes an external write.

- [ ] **Step 4: Inspect source state**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no uncommitted source changes.
