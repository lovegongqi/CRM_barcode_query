# Mobile Header and Status Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place the shared EcoWater logo to the left of every work-page title on mobile and remove the tool-account text from the query and transfer status bars.

**Architecture:** Keep the existing five templates and shared Aurora shell. Implement the title alignment with a single mobile-only CSS rule so desktop layout remains unchanged, then surgically remove only the two redundant account-text nodes while preserving logout controls and login-state indicators.

**Tech Stack:** Flask/Jinja templates, shared CSS, Python `unittest`, in-app browser responsive QA.

## Global Constraints

- Work pages are query, results, transfer, product-library, and settings.
- Only the mobile title area changes; desktop layout remains unchanged.
- Query and transfer remove the “工具账号：admin” text.
- Browser state, CRM login state, logout button, bottom navigation, Aurora colors, and existing logo asset remain unchanged.
- Verify 320px, 390px, and 430px mobile widths with no page-level horizontal overflow.

---

### Task 1: Shared Mobile Title Lockup

**Files:**
- Modify: `tests/test_frontend_contract.py:58-66`
- Modify: `static/aurora.css:864-904`

**Interfaces:**
- Consumes: existing `.aurora-logo`, `.header`, `.app-header`, `.app-title`, and the five `data-aurora-page` values.
- Produces: one shared mobile title lockup where the logo is absolutely positioned at the 12px page inset and the title copy reserves 50px on its left.

- [ ] **Step 1: Replace the existing mobile logo inset test with a failing title-lockup contract**

```python
def test_mobile_logo_sits_left_of_every_work_page_title(self):
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    mobile_css = css.split("@media (max-width: 720px)", 1)[1]
    self.assertRegex(
        mobile_css,
        r"\.aurora-logo\s*\{[^}]*position:\s*absolute;[^}]*top:\s*18px;[^}]*left:\s*12px;[^}]*margin:\s*0",
    )
    self.assertRegex(
        mobile_css,
        r"\.header > div:first-child,[^}]*\.app-header > \.app-title\s*\{[^}]*padding-left:\s*50px",
    )
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_mobile_logo_sits_left_of_every_work_page_title`

Expected: FAIL because the current mobile logo is `position: relative` and the mobile title padding is `0`.

- [ ] **Step 3: Implement the minimal shared mobile layout**

Replace the mobile `.aurora-logo` declaration in `static/aurora.css` with:

```css
.aurora-logo {
    position: absolute;
    top: 18px;
    left: 12px;
    width: 38px;
    height: 38px;
    padding: 5px;
    border-radius: 11px;
    margin: 0;
}
```

Replace the mobile title rule with:

```css
body[data-aurora-page]:not([data-aurora-page="login"]):not([data-aurora-page="no-permission"]) .header > div:first-child,
body[data-aurora-page="results"] .app-header > .app-title {
    min-height: 50px;
    padding-left: 50px;
    padding-right: 82px;
}
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_mobile_logo_sits_left_of_every_work_page_title`

Expected: PASS.

- [ ] **Step 5: Commit the mobile title lockup**

```bash
git add static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: align mobile logo with work page titles"
```

---

### Task 2: Query and Transfer Status Cleanup

**Files:**
- Modify: `tests/test_frontend_contract.py:105-116`
- Modify: `templates/crm.html:125-131`
- Modify: `templates/transfer.html:103-108`

**Interfaces:**
- Consumes: the existing `.aurora-account-status` status bars and `.aurora-account-logout` control.
- Produces: query and transfer status bars without a visible tool-account label, while retaining the logout link and all CRM/browser state nodes.

- [ ] **Step 1: Write the failing template contract**

```python
def test_query_and_transfer_omit_redundant_tool_account_text(self):
    query = self.source("crm.html")
    transfer = self.source("transfer.html")
    self.assertNotIn('<span>工具账号：</span>', query)
    self.assertNotIn('工具账号：<span id="appAccountStatus">', transfer)
    self.assertIn('class="aurora-account-logout"', query)
    self.assertIn('class="aurora-account-logout"', transfer)
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_query_and_transfer_omit_redundant_tool_account_text`

Expected: FAIL because both templates still render the redundant tool-account text.

- [ ] **Step 3: Remove only the redundant nodes**

In `templates/crm.html`, change the non-desktop account block to:

```html
{% if not is_desktop_app %}
<a class="aurora-account-logout" href="/logout">退出工具账号</a>
{% endif %}
```

In `templates/transfer.html`, change the non-desktop account block to:

```html
{% if not is_desktop_app %}
<a class="aurora-account-logout" href="/logout">退出工具账号</a>
{% endif %}
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_query_and_transfer_omit_redundant_tool_account_text`

Expected: PASS.

- [ ] **Step 5: Run all automated tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass with no failures or errors.

- [ ] **Step 6: Commit the status cleanup**

```bash
git add templates/crm.html templates/transfer.html tests/test_frontend_contract.py
git commit -m "fix: simplify query and transfer account status"
```

---

### Task 3: Responsive Visual Verification

**Files:**
- Modify: `design-qa.md`
- Create: `design-qa-mobile-header-after.png`

**Interfaces:**
- Consumes: the running local app at `http://127.0.0.1:5011` and the selected 430×932 browser annotations.
- Produces: browser measurements and a passed Design QA report covering all five work pages.

- [ ] **Step 1: Verify all five mobile work pages at 320px, 390px, and 430px**

For `/crm`, `/`, `/transfer`, `/product-library`, and `/accounts`, confirm:

```text
document.documentElement.scrollWidth === window.innerWidth
logo.left === 12
logo.right < title.left
bottom navigation remains fixed
```

Expected: every check passes at all three widths.

- [ ] **Step 2: Verify the query and transfer status bars**

Confirm in the rendered DOM:

```text
query: no visible “工具账号：admin”; browser state, CRM state, and logout button remain
transfer: no visible “工具账号：admin”; CRM state and logout button remain
```

Expected: both pages match the approved wording.

- [ ] **Step 3: Verify desktop layout is unchanged**

At 1440×900, inspect the same five routes and confirm the logo remains at the existing desktop position, titles do not shift, and navigation remains below the title.

Expected: no visible desktop regression.

- [ ] **Step 4: Capture and compare the final mobile reference state**

Capture `/crm` at 430×932 to `design-qa-mobile-header-after.png`, open it beside the annotated source screenshot, and evaluate logo/title alignment, text wrapping, log-button clearance, card spacing, and bottom-navigation placement.

Expected: no P0, P1, or P2 differences remain.

- [ ] **Step 5: Update the blocking QA report**

Update `design-qa.md` with the reference viewport, implementation capture, measured responsive results, console result, and exactly:

```markdown
## Final result

passed
```

- [ ] **Step 6: Run final verification**

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass, browser console has no errors or warnings, and `git diff --check` exits successfully.

