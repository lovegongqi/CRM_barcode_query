# Shared Query and Summary Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize the latest query job across browser windows, show product-library online-query logs inline, and add inspectable transfer product and failure details.

**Architecture:** Keep Flask job dictionaries as the source of truth. Add explicit latest-job discovery to the background-query status route, normalize transfer failure reasons on the server, and reuse the current polling and modal patterns in the three templates.

**Tech Stack:** Flask, Python threads, vanilla JavaScript, Jinja templates, Python `unittest`.

## Global Constraints

- Browser windows must share query state only when signed in as the same tool account.
- Existing query execution, retry, stop, and Playwright worker behavior must not change.
- Product-library live output must use the existing one-second polling route.
- Direction mismatch rows remain warnings, not failed barcodes.
- Do not add dependencies or unrelated refactors.

---

### Task 1: Add failing backend behavior tests

**Files:**
- Modify: `tests/test_background_jobs.py`

**Interfaces:**
- Produces: expected `latest=1` status behavior and transfer `failure_details` payload.

- [ ] **Step 1: Test stale job replacement**

Create two background jobs for the same account, mark the second as latest, then request the first with `latest=1`. Assert the response returns the second job ID.

- [ ] **Step 2: Test normalized transfer failures**

Build a summary containing unmatched, incomplete, and excluded barcodes. Run the failure normalizer and assert each barcode has the expected literal reason and category.

- [ ] **Step 3: Verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_background_jobs.BackgroundJobTests.test_latest_query_discovery_replaces_stale_job_id \
  tests.test_background_jobs.BackgroundJobTests.test_transfer_summary_failure_details_include_reasons -v
```

Expected: the stale job is returned and the failure normalizer is missing.

### Task 2: Implement shared query discovery and failure normalization

**Files:**
- Modify: `app.py`
- Test: `tests/test_background_jobs.py`

**Interfaces:**
- `GET /api/crm/background-batch/status?latest=1`
- `_refresh_transfer_summary_failure_details(summary: dict) -> list[dict]`

- [ ] **Step 1: Implement latest discovery**

When `latest=1`, read `latest_background_query_job_by_owner[owner]` even if the request contains `job_id`. Preserve ownership checks.

- [ ] **Step 2: Implement failure normalization**

Generate stable rows for `missing`, `incomplete`, and `excluded`. Prefer the unmatched-exclusion reason for entries in `excluded_unmatched`, and attach the result as `summary["failure_details"]`.

- [ ] **Step 3: Refresh failure details at summary boundaries**

Call the normalizer before `build_transfer_summary` returns and after `_exclude_unmatched_transfer_barcodes` mutates the summary.

- [ ] **Step 4: Verify GREEN**

Run the two focused tests and all background-job tests.

### Task 3: Add failing frontend contracts

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces: required UI hooks for Tasks 4-6.

- [ ] **Step 1: Test shared query discovery hooks**

Assert `crm.html` contains a two-second shared sync timer and calls status polling with `{latest: true}` on load and page return.

- [ ] **Step 2: Test inline product-library live output**

Assert `lookupBox` is an `aria-live` region and `renderLibraryQueryInlineStatus` is called from status rendering.

- [ ] **Step 3: Test transfer detail controls**

Assert `transferSummaryDetailModal`, `openTransferSummaryProduct`, and `openTransferSummaryFailures` exist.

- [ ] **Step 4: Verify RED**

Run the three focused frontend contract tests and confirm the new hooks are absent.

### Task 4: Implement cross-browser query synchronization

**Files:**
- Modify: `templates/crm.html`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- `pollMultiBatchStatus(options={})`
- `querySharedSyncTimer`

- [ ] **Step 1: Add latest discovery option**

When `options.latest` is true, send `latest=1` and omit the locally stored job ID.

- [ ] **Step 2: Add idle synchronization**

Every two seconds, discover the latest job while the current page is not tracking a running job. Also discover latest on initial load, visibility return, and `pageshow`.

- [ ] **Step 3: Keep active polling unchanged**

Once a job is adopted, poll its explicit job ID at the existing one-second interval.

### Task 5: Render product-library logs in the search result area

**Files:**
- Modify: `templates/product_library.html`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- `renderLibraryQueryInlineStatus(data)`

- [ ] **Step 1: Mark the result area live**

Add `aria-live="polite"` and `aria-atomic="false"` to `lookupBox`.

- [ ] **Step 2: Render current state and recent logs**

Show barcode, channel, status, elapsed time, and the latest six log messages. Keep all text escaped.

- [ ] **Step 3: Integrate with polling**

Call the inline renderer whenever status logs arrive and show a readable polling error without deleting prior job data.

### Task 6: Add transfer product and failed-barcode drill-down

**Files:**
- Modify: `templates/transfer.html`
- Modify: `static/aurora.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- `transferSummaryData`
- `openTransferSummaryProduct(index)`
- `openTransferSummaryFailures()`
- `closeTransferSummaryDetail()`

- [ ] **Step 1: Add the detail modal**

Use the existing overlay composition with a title, metadata area, scrollable table body, and close button.

- [ ] **Step 2: Render compact summary rows**

Show product name/model, code, and quantity as clickable rows. Add a failed/excluded row only when `failure_details` is non-empty.

- [ ] **Step 3: Render modal details**

Product mode lists every barcode. Failure mode lists barcode, category label, and reason.

- [ ] **Step 4: Add responsive styling**

Keep rows within the card width and make the detail table horizontally scrollable on narrow screens.

### Task 7: Verify the complete change

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run focused tests**

Run backend and frontend tests added above.

- [ ] **Step 2: Run full tests**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

- [ ] **Step 3: Check inline JavaScript syntax**

Extract each modified template script and run `node --check`.

- [ ] **Step 4: Run repository checks**

```bash
git diff --check
git status --short
```

- [ ] **Step 5: Launch and visually inspect**

Start the Flask app on an unused local port and inspect `/crm`, `/product-library`, and `/transfer` at desktop and mobile widths.
