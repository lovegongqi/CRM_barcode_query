# Unified Query Picker and Account Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace desktop query-channel chips with the existing shared multi-select dropdown, standardize the nine mobile result actions, and place the plain tool-account username immediately before logout on every work page.

**Architecture:** Keep `selectedQuerySlotIds` as the only query-channel selection state and expose its existing dropdown at every viewport width. Keep desktop result actions unchanged while adding a mobile-only 3×3 grid. Reuse each page's existing account-status fetch and move the existing username element into one shared right-aligned account-session cluster.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript, CSS, Python `unittest`, local in-app browser.

## Global Constraints

- Default query selection remains all 10 channels and at least one channel must remain selected.
- Query scheduling, result filtering, CRM login, transfer behavior, permissions, and file storage are unchanged.
- The five work pages are `crm.html`, `index.html`, `transfer.html`, `product_library.html`, and `accounts.html`.
- Account copy is the plain username followed by `退出工具账号`; do not show `当前工具账号：`, `工具账号：`, or `（管理员）`.
- The mobile result action layout applies at widths up to 640px; desktop action density remains unchanged.
- Preserve the dark glass theme and prevent page-level horizontal scrolling.

---

### Task 1: Use one query-channel dropdown on desktop and mobile

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `templates/crm.html`
- Modify: `static/aurora.css`

**Interfaces:**
- Consumes: `selectedQuerySlotIds: string[]`, `querySlots: object[]`, `toggleQuerySlotMenu(event)`, `closeQuerySlotMenu()`, `selectAllQuerySlots()`.
- Produces: one visible `#querySlotMobileTrigger` and one `#querySlotMobileMenu` at all viewport widths; no `#querySlotSelector` chip surface.

- [ ] **Step 1: Replace the mobile-only contract with an all-width dropdown contract**

Add this test and update the old `test_mobile_query_channels_use_one_shared_multiselect_state` expectations:

```python
def test_query_channels_use_one_shared_dropdown_at_all_widths(self):
    query = self.source("crm.html")
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    desktop_css = css.split("@media (max-width: 720px)", 1)[0]
    for token in (
        'id="querySlotMobileTrigger"',
        'id="querySlotMobileMenu"',
        'id="querySlotMobileCount"',
        "toggleQuerySlotMenu",
        "selectAllQuerySlots",
        "selectedQuerySlotIds",
    ):
        with self.subTest(token=token):
            self.assertIn(token, query)
    self.assertNotIn('id="querySlotSelector"', query)
    self.assertNotIn("document.getElementById('querySlotSelector')", query)
    self.assertNotRegex(
        desktop_css,
        r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*none",
    )
    self.assertRegex(
        desktop_css,
        r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*inline-flex",
    )
    self.assertRegex(
        desktop_css,
        r"\.aurora-channel-mobile-menu\s*\{[^}]*position:\s*absolute",
    )
```

- [ ] **Step 2: Run the focused test and verify the old desktop behavior fails**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_query_channels_use_one_shared_dropdown_at_all_widths -v
```

Expected: `FAIL` because `#querySlotSelector` still exists and the desktop trigger is hidden.

- [ ] **Step 3: Remove the desktop chip surface and its render branch**

Change the channel-picker markup in `templates/crm.html` to:

```html
<div class="aurora-channel-picker" data-default-selection="all">
    <div class="aurora-channel-heading">
        <div><strong>查询通道</strong><small>默认全选，系统自动调度空闲通道</small></div>
        <button class="aurora-channel-mobile-trigger" id="querySlotMobileTrigger" type="button" aria-expanded="false" aria-controls="querySlotMobileMenu" onclick="toggleQuerySlotMenu(event)">
            <span id="querySlotMobileCount">已选 0/0</span><span aria-hidden="true">⌄</span>
        </button>
    </div>
    <div class="aurora-channel-mobile-menu" id="querySlotMobileMenu" hidden></div>
</div>
```

In `renderQuerySlotSelector()`, remove the `querySlotSelector` lookup, chip markup, and `box.innerHTML` assignment. Keep the count, trigger disabled state, menu markup, and all existing selection functions unchanged.

- [ ] **Step 4: Promote the existing dropdown styles to the base desktop rules**

Replace the base picker/trigger/menu rules in `static/aurora.css` with all-width styles equivalent to:

```css
.aurora-channel-picker { position: relative; margin: 0 0 14px; padding: 10px 12px; color: #a5bbc9; background: rgba(4, 14, 26, .48); border: 1px solid var(--aurora-line); border-radius: 13px; }
.aurora-channel-heading { min-width: 0; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.aurora-channel-mobile-trigger { min-height: 34px; padding: 7px 10px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; flex: 0 0 auto; border: 1px solid rgba(87, 227, 255, .28); border-radius: 10px; color: #dff8fd; background: rgba(73, 203, 233, .10); font: inherit; font-size: 11px; cursor: pointer; }
.aurora-channel-mobile-trigger:disabled { opacity: .55; cursor: default; }
.aurora-channel-mobile-menu { position: absolute; z-index: 40; top: calc(100% - 4px); right: 10px; width: min(360px, calc(100% - 20px)); max-height: 360px; padding: 8px; display: grid; gap: 5px; overflow-y: auto; border: 1px solid rgba(87, 227, 255, .24); border-radius: 14px; background: rgba(6, 18, 32, .98); box-shadow: 0 18px 40px rgba(0, 0, 0, .38); }
.aurora-channel-mobile-menu[hidden] { display: none; }
```

Add the shared select-all and checkbox-option rules beside the base menu rules:

```css
.aurora-channel-select-all,
.aurora-channel-mobile-option {
    width: 100%;
    min-height: 38px;
    padding: 8px 10px;
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    border: 1px solid var(--aurora-line);
    border-radius: 10px;
    color: #b9ceda;
    background: rgba(10, 27, 45, .88);
    font: inherit;
    font-size: 11px;
    text-align: left;
}
.aurora-channel-select-all { display: flex; align-items: center; gap: 8px; color: #9ff2ff; white-space: nowrap; cursor: pointer; }
.aurora-channel-mobile-option.selected { color: #effcff; border-color: rgba(87, 227, 255, .30); background: rgba(73, 203, 233, .12); }
.aurora-channel-mobile-option input { width: 15px; height: 15px; accent-color: #57e3ff; }
.aurora-channel-mobile-option small { margin: 0; color: var(--aurora-green); font-size: 9px; white-space: nowrap; }
```

Delete the duplicate copies from the mobile media query. Keep only the mobile menu-size override below 720px:

```css
.aurora-channel-mobile-menu { width: min(290px, calc(100% - 20px)); max-height: 320px; }
```

- [ ] **Step 5: Run the focused and existing query contracts**

Run:

```bash
python3 -m unittest \
  tests.test_frontend_contract.FrontendContractTest.test_query_channels_use_one_shared_dropdown_at_all_widths \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_query_select_all_stays_on_one_line \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_shell_does_not_create_page_level_horizontal_scroll -v
```

Expected: all three tests pass.

- [ ] **Step 6: Commit the query picker change**

```bash
git add templates/crm.html static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: unify query channel picker"
```

### Task 2: Standardize mobile result action buttons

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `static/aurora.css`

**Interfaces:**
- Consumes: the existing result-page `.action-groups` containing exactly nine buttons.
- Produces: a mobile-only three-column action grid with equal button dimensions.

- [ ] **Step 1: Add a failing mobile action-grid contract**

```python
def test_mobile_results_actions_use_equal_three_by_three_grid(self):
    results = self.source("index.html")
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    self.assertEqual(results.count('<div class="action-groups">'), 1)
    self.assertEqual(
        len(re.findall(r'<div class="action-groups">(.*?)</div>', results, re.S)[0].split('<button')) - 1,
        9,
    )
    self.assertRegex(
        css,
        r'@media\s*\(max-width:\s*640px\)[\s\S]*body\[data-aurora-page="results"\] \.action-groups\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)',
    )
    self.assertRegex(
        css,
        r'body\[data-aurora-page="results"\] \.action-groups \.btn\s*\{[^}]*width:\s*100%[^}]*font-size:\s*11px[^}]*white-space:\s*nowrap',
    )
```

- [ ] **Step 2: Run the contract and verify it fails**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_mobile_results_actions_use_equal_three_by_three_grid -v
```

Expected: `FAIL` because the mobile action group still uses wrapping flex items of different widths.

- [ ] **Step 3: Add the mobile-only equal-size grid**

Append a dedicated `@media (max-width: 640px)` block to `static/aurora.css`:

```css
@media (max-width: 640px) {
body[data-aurora-page="results"] .action-groups {
    width: 100%;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 6px;
}
body[data-aurora-page="results"] .action-groups .btn {
    width: 100%;
    min-width: 0;
    min-height: 38px;
    padding: 6px 3px;
    font-size: 11px !important;
    line-height: 1;
    white-space: nowrap;
}
}
```

- [ ] **Step 4: Run the focused test and mobile overflow contract**

Run:

```bash
python3 -m unittest \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_results_actions_use_equal_three_by_three_grid \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_shell_does_not_create_page_level_horizontal_scroll -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit the result action layout**

```bash
git add static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: standardize mobile result actions"
```

### Task 3: Place the plain username immediately before logout on every work page

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `templates/crm.html`
- Modify: `templates/index.html`
- Modify: `templates/transfer.html`
- Modify: `templates/product_library.html`
- Modify: `templates/accounts.html`
- Modify: `static/aurora.css`

**Interfaces:**
- Consumes: each page's existing `/api/app-auth/status`, `/api/accounts`, or `/api/product-library` response and current account status element.
- Produces: `.aurora-account-session` containing `.aurora-account-name` immediately followed by `.aurora-account-logout`.

- [ ] **Step 1: Add a failing shared account-session contract**

```python
def test_every_work_page_places_plain_username_before_logout(self):
    filenames = ("crm.html", "index.html", "transfer.html", "product_library.html", "accounts.html")
    for filename in filenames:
        with self.subTest(filename=filename):
            source = self.source(filename)
            self.assertIn('class="aurora-account-session"', source)
            self.assertIn('class="aurora-account-name"', source)
            self.assertLess(source.index('class="aurora-account-name"'), source.index('class="aurora-account-logout"'))
            self.assertNotIn("当前工具账号", source)
            self.assertNotIn("工具账号：", source)
            self.assertNotIn("（管理员）", source)
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    self.assertRegex(css, r"\.aurora-account-session\s*\{[^}]*display:\s*inline-flex")
    self.assertRegex(css, r"\.aurora-account-name\s*\{[^}]*text-overflow:\s*ellipsis")
```

- [ ] **Step 2: Run the contract and verify all existing status labels are detected**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_every_work_page_places_plain_username_before_logout -v
```

Expected: `FAIL` because three pages still contain standalone account labels and query/transfer have no account-name element beside logout.

- [ ] **Step 3: Wrap the username and logout link in every template**

Use this exact wrapper in `crm.html`, `index.html`, and `transfer.html`:

```html
<div class="aurora-account-session">
    <span class="aurora-account-name" id="appAccountStatus">检测中</span>
    <a class="aurora-account-logout" href="/logout">退出工具账号</a>
</div>
```

Use this exact wrapper in `product_library.html` and `accounts.html`:

```html
<div class="aurora-account-session">
    <span class="aurora-account-name" id="accountStatus">检测中</span>
    <a class="aurora-account-logout" href="/logout">退出工具账号</a>
</div>
```

Remove the standalone `.status-text` account node from result, product-library, and accounts pages. Keep query browser/CRM status and transfer CRM status on the left.

- [ ] **Step 4: Remove administrator suffixes from existing update functions**

In `accounts.html`, replace the account assignment with:

```javascript
document.getElementById('accountStatus').textContent = account ? account.username : '未登录';
```

In `product_library.html`, replace the account assignment with:

```javascript
if (accountStatus) accountStatus.textContent = data.account ? data.account.username : '未登录';
```

Keep the existing `appAccountStatus` assignments in query, results, and transfer pages because they already emit only `username` or `未登录`.

- [ ] **Step 5: Add the shared right-aligned account-session styles**

Replace the direct logout margin rule in `static/aurora.css` with:

```css
body[data-aurora-page] .aurora-account-session {
    min-width: 0;
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
}
body[data-aurora-page] .aurora-account-name {
    min-width: 0;
    max-width: 180px;
    overflow: hidden;
    color: #b9ceda;
    font-size: 11px;
    font-weight: 700;
    text-overflow: ellipsis;
    white-space: nowrap;
}
```

In the mobile media query, cap the username at `110px` while leaving the logout button non-shrinking.

- [ ] **Step 6: Run focused account and compact-row tests**

Run:

```bash
python3 -m unittest \
  tests.test_frontend_contract.FrontendContractTest.test_every_work_page_places_plain_username_before_logout \
  tests.test_frontend_contract.FrontendContractTest.test_every_work_page_uses_the_shared_tool_account_logout_button \
  tests.test_frontend_contract.FrontendContractTest.test_tool_account_controls_use_compact_status_rows \
  tests.test_frontend_contract.FrontendContractTest.test_query_and_transfer_omit_redundant_tool_account_text -v
```

Expected: all four tests pass.

- [ ] **Step 7: Commit the shared account-session controls**

```bash
git add templates/crm.html templates/index.html templates/transfer.html templates/product_library.html templates/accounts.html static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: unify tool account controls"
```

### Task 4: Regression and visual verification

**Files:**
- Verify only; no production file changes expected.

**Interfaces:**
- Consumes: the completed changes from Tasks 1–3.
- Produces: test and browser evidence that all approved desktop/mobile behaviors are present.

- [ ] **Step 1: Run the complete automated test suite**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: every test passes with zero failures and zero errors.

- [ ] **Step 2: Check patch formatting and repository scope**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only the user's pre-existing untracked QA files remain outside the committed changes.

- [ ] **Step 3: Verify desktop behavior at 1715×1259**

Open `/crm`, expand the query-channel dropdown, uncheck one channel, use “全选通道”, click outside, and press Escape. Confirm the desktop chip row is absent, the trigger count updates, the menu stays within the picker, and the page has no horizontal scroll.

Open `/`, `/transfer`, `/product-library`, and `/accounts`. Confirm each page displays the plain username immediately before “退出工具账号” and no standalone account label remains. Confirm desktop result buttons retain their existing layout.

- [ ] **Step 4: Verify mobile behavior at 430×932**

Open `/crm` and confirm the shared channel dropdown still opens, all option labels stay horizontal, and the bottom navigation remains fixed. Open `/` and confirm the nine action buttons form three equal columns and three equal rows. Open all five work pages and confirm `admin` remains on one line immediately before the logout button without page-level horizontal scrolling.

- [ ] **Step 5: Record the final commit state**

Run:

```bash
git log -4 --oneline
```

Expected: the three implementation commits appear above the design and plan commits.
