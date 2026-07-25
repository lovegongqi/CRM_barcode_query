# Persistent Transfer Records and Static Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist CRM transfer records in SQLite until the user explicitly deletes one record or clears all records, and keep the top navigation labels stable while switching pages.

**Architecture:** Add a dedicated SQLite database under the existing runtime `CONFIG_DIR` for transfer records. Expose authenticated JSON endpoints for list/upsert/delete/clear, use the browser session data only as a one-time legacy migration fallback, and render the database result as the source of truth. Keep the shared navigation label map fixed in `static/aurora.js`; page-specific headings remain unchanged.

**Tech Stack:** Flask, SQLite, vanilla JavaScript, unittest, existing Docker Compose deployment.

## Global Constraints

- Existing product-library SQLite behavior and legacy JSON migration must remain unchanged.
- Existing row-level delete and clear buttons remain available; both operations are explicit user actions.
- `sources/` is read-only.
- Do not delete existing runtime data, Docker volumes, or backups during local/server deployment.

### Task 1: Add regression tests for transfer persistence and stable navigation

**Files:**
- Modify: `tests/test_product_library_persistence.py` or create `tests/test_transfer_persistence.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing backend tests**
  - Redirect the app's transfer database path to a temporary directory.
  - Assert a record can be upserted, listed after a fresh load, updated by `record_id`, deleted individually, and removed by the clear operation.
  - Assert logs and order number survive the round trip.

- [ ] **Step 2: Run the focused tests and verify they fail**
  - Run `python3 -m unittest tests.test_transfer_persistence -v`.
  - Expected: endpoint/helper missing or persistence assertions fail.

- [ ] **Step 3: Add a frontend contract test**
  - Assert `static/aurora.js` contains fixed labels for the five navigation items and does not derive the visible label from changing page text.
  - Assert `templates/transfer.html` loads persisted transfer records and calls explicit delete/clear endpoints.

- [ ] **Step 4: Run the focused frontend test and verify it fails**
  - Run `python3 -m unittest tests.test_frontend_contract -v`.
  - Expected: the new contract assertions fail before implementation.

### Task 2: Implement SQLite transfer-record storage and authenticated API

**Files:**
- Modify: `app.py` around runtime data paths and API routes
- Test: `tests/test_transfer_persistence.py`

**Interfaces:**
- `TRANSFER_RECORDS_DB_FILE`: SQLite file under `CONFIG_DIR`.
- `load_transfer_records() -> list[dict]`.
- `upsert_transfer_record(record: dict) -> dict`.
- `delete_transfer_record(record_id: str) -> bool`.
- `clear_transfer_records() -> int`.
- `GET /api/transfer-records` returns `{success, records}`.
- `POST /api/transfer-records` upserts one record and returns `{success, record}`.
- `DELETE /api/transfer-records/<record_id>` deletes one record.
- `DELETE /api/transfer-records` clears all records.

- [ ] **Step 1: Create the SQLite schema helper**
  - Create the table with a stable text `record_id` primary key and JSON text for logs.
  - Store slot, job, order number, state/status, distributor, timestamps, elapsed, transfer type, remark, and update timestamp.
  - Use a module-level reentrant lock and `with sqlite3.connect(...)` transactions.

- [ ] **Step 2: Implement load/upsert/delete/clear helpers**
  - Normalize missing optional fields and deserialize logs safely.
  - Order records newest-first by creation/update timestamp.
  - Keep delete operations explicit; no startup cleanup or TTL.

- [ ] **Step 3: Add the four API routes**
  - Apply the existing transfer permission guard through the existing path permission mechanism.
  - Return clear JSON errors for missing record IDs or malformed payloads.

- [ ] **Step 4: Run backend tests and verify they pass**
  - Run `python3 -m unittest tests.test_transfer_persistence -v`.

### Task 3: Make the transfer page use the database as source of truth

**Files:**
- Modify: `templates/transfer.html`
- Test: `tests/test_frontend_contract.py`

- [ ] **Step 1: Replace session-only restore with API loading**
  - On page initialization, fetch `/api/transfer-records` and render the returned rows.
  - If the database is empty and legacy `sessionStorage` contains records, migrate those records once via the upsert endpoint, then reload from the API.

- [ ] **Step 2: Persist record creation and status updates**
  - Upsert immediately after creating a record and after job ID/order/status/log changes.
  - Keep session storage only for current browser job-resume IDs, not as durable record storage.

- [ ] **Step 3: Implement explicit deletion**
  - Row delete calls `DELETE /api/transfer-records/<record_id>` and only removes the row locally after success.
  - Clear calls `DELETE /api/transfer-records` after the existing confirmation prompt and only clears the UI after success.
  - Preserve the current active-job polling behavior.

- [ ] **Step 4: Run frontend contract tests**
  - Run `python3 -m unittest tests.test_frontend_contract -v`.

### Task 4: Make shared navigation labels stable

**Files:**
- Modify: `static/aurora.js`
- Test: `tests/test_frontend_contract.py`

- [ ] **Step 1: Keep `NAV` as the only visible label source**
  - Map `/`, `/crm`, `/results`, `/transfer`, `/product-library`, and `/accounts` to the fixed short labels.
  - Keep the existing special route mapping for `/` when it represents the results page.
  - Do not let later DOM text mutations replace `.aurora-nav-label`.

- [ ] **Step 2: Run the contract test**
  - Run `python3 -m unittest tests.test_frontend_contract -v`.

### Task 5: Full verification and deployment

**Files:**
- Modify: `README.md` only if the persistent file list needs documentation.

- [ ] **Step 1: Run the complete test suite**
  - Run `python3 -m unittest discover -s tests -p 'test_*.py'`.
  - Run `git diff --check`.

- [ ] **Step 2: Start the local app and verify persistence manually**
  - Open `/transfer`, confirm an existing row loads after reload, confirm one-row delete, and confirm the clear confirmation deletes all rows.
  - Switch all top navigation items and confirm labels do not change during refresh.

- [ ] **Step 3: Commit and push to GitHub**
  - Commit only the intended files with a focused message.
  - Push `main` to `origin`.

- [ ] **Step 4: Deploy to the cloud server without clearing volumes**
  - Pull the pushed commit with fast-forward only.
  - Rebuild/restart the Compose service.
  - Verify the container is healthy, `/login` returns HTTP 200, and the transfer-record SQLite file is on the persistent `/app/data` volume.
