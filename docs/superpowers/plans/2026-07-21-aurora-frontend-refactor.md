# Aurora Frontend Refactor Implementation Plan

> **For Codex:** Execute this plan in the current user-approved local implementation session. Preserve all existing API contracts and inline workflow logic.

**Goal:** Replace the seven approved prototype pages with a cohesive, responsive Aurora glass UI while preserving the existing Flask workflows and adding visible per-item realtime activity where requested.

**Architecture:** Keep Flask routes and endpoint contracts unchanged. Add one shared design-system stylesheet and one shared shell/interaction script, then adapt each existing Jinja template around its current IDs and functions. Reuse the approved transparent EcoWater asset. Add source-contract tests plus Flask route smoke tests before each implementation slice.

**Tech Stack:** Flask/Jinja2, vanilla HTML/CSS/JavaScript, Python `unittest`, Playwright already present in the project for final local browser smoke testing.

---

### Task 1: Establish frontend contracts and shared assets

**Files:**
- Create: `tests/test_frontend_contract.py`
- Create: `static/aurora.css`
- Create: `static/aurora.js`
- Create: `static/ecowater-logo.png`
- Modify: `templates/index.html`, `templates/crm.html`, `templates/transfer.html`, `templates/product_library.html`, `templates/accounts.html`, `templates/login.html`, `templates/no_permission.html`

**Steps:**
1. Add failing tests that require every template to load the shared Aurora stylesheet, use the EcoWater logo, and identify its page.
2. Add tests for fixed mobile navigation, query/transfer/product log dialog hooks, and the 10/5 default channel contract.
3. Run the new test file and confirm failures describe the missing frontend contract.
4. Add the shared CSS/JS files and approved Logo asset; wire them into all templates without removing existing scripts.
5. Run the tests and confirm the shared-shell contract passes.
6. Commit the shared foundation.

### Task 2: Rebuild login and access-denied pages

**Files:**
- Modify: `templates/login.html`
- Modify: `templates/no_permission.html`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for the branded desktop/mobile login composition and accessible feature icons.
2. Replace the two small templates with the approved Aurora structures while retaining login API calls, `next` handling and permission links.
3. Run frontend contract and app-auth tests.
4. Commit the authentication pages.

### Task 3: Rebuild the online query workbench

**Files:**
- Modify: `templates/index.html`
- Modify: `static/aurora.js`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for default-all channel selection, realtime barcode rows and a per-barcode log dialog trigger.
2. Adapt the existing query controls and polling output into the approved input, stats and realtime table/card layout.
3. Route status clicks to the existing detailed log data through the shared dialog.
4. Run contract tests and query-related Flask tests.
5. Commit the query workbench.

### Task 4: Rebuild results management

**Files:**
- Modify: `templates/crm.html`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for the four approved filters and seven result columns.
2. Apply the Aurora operations board while preserving selection, copy, export, close, transfer and delete functions.
3. Add compact mobile result cards without removing desktop table semantics.
4. Run contract tests and result/export tests.
5. Commit the result page.

### Task 5: Rebuild transfer workflow and realtime records

**Files:**
- Modify: `templates/transfer.html`
- Modify: `static/aurora.js`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for move-in/move-out, distributor history, summary, submit, realtime record columns and channel-log triggers.
2. Replace the raw log presentation with realtime transfer records derived from the existing job polling data.
3. Keep the full raw entries available in the channel detail dialog.
4. Run contract tests and transfer route smoke tests.
5. Commit the transfer page.

### Task 6: Rebuild product matching with visible online-query activity

**Files:**
- Modify: `templates/product_library.html`
- Modify: `static/aurora.js`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for an inline realtime query panel and clickable barcode status.
2. Convert existing product-query polling logs into structured live rows showing queue, assigned channel, state and elapsed time.
3. Open full barcode logs through the shared dialog and refresh matching results when complete.
4. Preserve rule CRUD and local search behavior.
5. Run contract tests and product-library route tests.
6. Commit the product page.

### Task 7: Rebuild settings and account management

**Files:**
- Modify: `templates/accounts.html`
- Modify: `app.py`
- Test: `tests/test_frontend_contract.py`

**Steps:**
1. Add failing assertions for 10 query channels, 5 transfer channels, 5-column channel grid, batch login and compact account table.
2. Change only the initial worker defaults to 10/5; retain configured persisted values and validation.
3. Adapt the settings/account template to the approved desktop ratios and fixed mobile navigation.
4. Run contract, auth and settings tests.
5. Commit the settings page.

### Task 8: Full local verification

**Files:**
- Modify only files required by verified defects.

**Steps:**
1. Run the complete Python test suite.
2. Start the Flask app using the repository's documented local command.
3. Open the exact local URL in the in-app browser once.
4. Check all seven routes at desktop width and 390px mobile width, including fixed navigation and dialog interactions.
5. Exercise one safe local flow for login, query input, result filtering, transfer summary presentation, product lookup and settings rendering without triggering external CRM mutations.
6. Fix verified regressions and rerun the complete suite.
7. Report the local preview URL and remaining limitations, if any.
