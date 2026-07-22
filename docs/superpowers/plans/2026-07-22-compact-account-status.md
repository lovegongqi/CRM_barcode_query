# Compact Account Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the redundant account-card heading, return logout controls from page headers to account status areas, and keep those status areas at one shared compact height.

**Architecture:** Keep the existing independent Jinja templates and shared Aurora stylesheet. Each work page uses its existing status region; the results page receives the same compact account-status row, while the settings page converts its large account card into that shared row.

**Tech Stack:** Flask/Jinja2 templates, CSS, Python `unittest` frontend contracts.

## Global Constraints

- Do not change navigation order, page widths, business forms, or API behavior.
- Keep current account and role visible on the settings page.
- Do not place “退出工具账号” in a page title bar.

---

### Task 1: Shared compact account status row

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `templates/crm.html`
- Modify: `templates/index.html`
- Modify: `templates/transfer.html`
- Modify: `templates/product_library.html`
- Modify: `templates/accounts.html`
- Modify: `static/aurora.css`

**Interfaces:**
- Consumes: existing `accountStatus` and `appAccountStatus` DOM targets used by `static/aurora.js`.
- Produces: `.aurora-account-status` single-row layout and `.aurora-account-logout` link inside each page's account status region.

- [ ] **Step 1: Write the failing frontend contract**

Add a test that checks all five work templates contain `aurora-account-status`, none contain `aurora-header-logout`, `accounts.html` has no `<h2>当前工具账号</h2>`, and `static/aurora.css` defines the compact row:

```python
def test_tool_account_controls_use_compact_status_rows(self):
    filenames = ("crm.html", "index.html", "transfer.html", "product_library.html", "accounts.html")
    for filename in filenames:
        source = self.source(filename)
        self.assertIn("aurora-account-status", source)
        self.assertNotIn("aurora-header-logout", source)
    self.assertNotIn("<h2>当前工具账号</h2>", self.source("accounts.html"))
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    self.assertRegex(css, r"\.aurora-account-status\s*\{[^}]*min-height:\s*50px")
```

- [ ] **Step 2: Run the contract and verify RED**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_tool_account_controls_use_compact_status_rows
```

Expected: FAIL because title-bar logout links still use `aurora-header-logout` and the settings card still contains its heading.

- [ ] **Step 3: Implement the minimal template and CSS changes**

For each work page, remove the title-bar logout link and place this link in the page's account status region:

```html
<a class="aurora-account-logout" href="/logout">退出工具账号</a>
```

Use the shared status-row class:

```html
<div class="status-bar aurora-account-status">
    <div class="status-text">当前工具账号：<span id="accountStatus">检测中</span></div>
    <a class="aurora-account-logout" href="/logout">退出工具账号</a>
</div>
```

On the query and transfer pages, add `aurora-account-status` to the existing status bar rather than creating a second bar. On the results page, insert the compact row immediately after `.app-header`. Remove the settings-page `<h2>当前工具账号</h2>`.

Define a common desktop row without changing widths:

```css
body[data-aurora-page] .aurora-account-status {
    min-height: 50px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}
```

Delete the unused `.aurora-header-logout` positioning rule.

- [ ] **Step 4: Run the targeted contract and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_tool_account_controls_use_compact_status_rows
```

Expected: one test passes.

- [ ] **Step 5: Verify real browser geometry**

At desktop width, inspect `/crm`, `/`, `/transfer`, `/product-library`, and `/accounts`. Confirm each `.aurora-account-status` is 50px high, contains the logout link, and the page title/nav geometry remains identical.

- [ ] **Step 6: Run the full regression suite**

Run:

```bash
python3 -m unittest discover -s tests
git diff --check
```

Expected: all tests pass and `git diff --check` prints no errors.
