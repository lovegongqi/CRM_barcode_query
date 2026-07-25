# All Pages Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce full-payload polling, repeated disk parsing, and large synchronous DOM updates across every active CRM work page.

**Architecture:** Preserve complete backend business state while adding filtered query payloads, revision-based snapshots, incremental logs, adaptive visible-page polling, chunked results rendering, and bounded shared log history. Static pages remain unchanged.

**Tech Stack:** Flask, Python threads and SQLite, vanilla JavaScript, Jinja templates, Python `unittest`.

## Global Constraints

- Do not remove records, logs needed for task inspection, retry data, or result functionality.
- Keep all filtered result rows accessible without pagination.
- Background CRM tasks continue running when a browser tab is hidden.
- Hidden pages may pause display polling and must refresh immediately when visible.
- Do not add third-party dependencies.

---

### Task 1: Limit realtime query payload and DOM rows

**Files:**
- Modify: `tests/test_background_jobs.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `app.py`
- Modify: `templates/crm.html`

**Interfaces:**
- `_background_query_status_payload(job) -> dict`
- `queryVisibleItems(items) -> list`

- [ ] Write a backend test containing waiting, running, success, error, and stopped items. Assert only running/error/stopped are returned, while `total`, `completed`, `success_count`, `failed_count`, and `pending_count` remain unchanged.
- [ ] Write a frontend contract test requiring `queryVisibleItems`, the visible-state set, and `当前无查询中或失败条码`.
- [ ] Run both tests and confirm they fail against the full-item implementation.
- [ ] Filter copied response items in `app.py`, calculate pending count from all internal items, and filter again before frontend rendering.
- [ ] Run focused query tests and the complete background-job suite.

### Task 2: Cache and revision results snapshots

**Files:**
- Create: `tests/test_results_performance.py`
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- `_barcode_scan_signature() -> tuple`
- `_barcode_snapshot() -> dict`
- `GET /api/barcodes?revision=<revision>`
- `renderList(barcodes)` with `resultRenderGeneration`

- [ ] Build a temporary result directory test that patches `extract_fields_from_html`, calls the snapshot twice, and asserts unchanged files are parsed once.
- [ ] Change one file mtime and assert the next snapshot reparses and receives a different revision.
- [ ] Test `/api/barcodes` returns `filters` and `revision`, then returns `unchanged: true` for the same revision.
- [ ] Implement a lock-protected cached snapshot keyed by file and metadata signatures.
- [ ] Return filters with the barcode payload and preserve the legacy filter-options route using the same cache.
- [ ] Update `loadAllData` to use one request, send the current revision, guard concurrent loads, and skip hidden refresh.
- [ ] Split result row insertion into batches of 100 using `requestAnimationFrame` and cancel stale generations.
- [ ] Run results tests and frontend contracts.

### Task 3: Add revision-aware adaptive transfer refresh

**Files:**
- Modify: `tests/test_transfer_persistence.py`
- Modify: `app.py`
- Modify: `templates/transfer.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- `_transfer_records_revision() -> str`
- `GET /api/transfer-records?revision=<revision>`
- `scheduleTransferRealtimeRefresh(delay=None)`

- [ ] Test an initial records response contains a revision, a repeated revision returns `unchanged: true`, and an upsert changes the revision.
- [ ] Add a process token and monotonic counter incremented after successful upsert, delete, and clear writes.
- [ ] Return unchanged responses before loading and serializing full records.
- [ ] Store the revision in the transfer page and rerender only changed responses.
- [ ] Replace the fixed interval with one-second active and five-second idle scheduling; skip hidden refresh and resume immediately on visibility return.
- [ ] Run transfer persistence and frontend tests.

### Task 4: Send only new product-library logs

**Files:**
- Modify: `tests/test_background_jobs.py`
- Modify: `app.py`
- Modify: `templates/product_library.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- `library_query_job["log_seq"]`
- `GET /api/product-library/query/status?since=<id>`
- `mergeLibraryQueryLogs(rows)`

- [ ] Test log rows receive increasing IDs and `since` returns only later rows.
- [ ] Add `log_seq` to the job and reset it when a new online query starts.
- [ ] Filter status logs by `since` while returning current task state.
- [ ] Track the last sequence in the frontend, merge unique rows, and skip hidden polling.
- [ ] Run focused matching and frontend tests.

### Task 5: Bound shared logs and hidden-page polling

**Files:**
- Modify: `static/log_modal.js`
- Modify: `templates/accounts.html`
- Modify: `templates/crm.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- `MAX_HISTORY = 1000`
- `MAX_RENDERED_HISTORY = 500`
- `schedulePersistHistory()`

- [ ] Add frontend contracts for bounded history, debounced persistence, lazy log rendering, and hidden-page polling guards.
- [ ] Keep only 1,000 log records, render the latest 500 only when the modal opens, and debounce storage writes.
- [ ] Avoid adding hidden log DOM nodes while the modal is closed.
- [ ] Skip query status/account polling while hidden and rely on existing visibility events to catch up.
- [ ] Run frontend contracts.

### Task 6: Full verification

**Files:**
- Verify all modified files.

- [ ] Run `python3 -m unittest discover -s tests -p 'test_*.py'`.
- [ ] Run `python3 -m py_compile app.py`.
- [ ] Run `git diff --check`.
- [ ] Reload `/crm`, `/`, `/transfer`, `/product-library`, `/accounts`, and `/login` in the local browser.
- [ ] Confirm no console errors and verify periodic requests stop while the page is hidden where supported.
