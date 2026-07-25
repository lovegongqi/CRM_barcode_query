# Background Query and Transfer Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make batch barcode queries and CRM transfers continue entirely on the server when the user switches pages, hides the browser, or closes the task page; stopped query barcodes must count as failures and remain available for retry.

**Architecture:** Add a server-owned background query job that receives the complete barcode list and selected query slots, then uses a shared queue and one backend worker thread per slot to process the queue. The browser only starts, polls, restores, and stops the job. Keep transfer execution in its existing backend thread, but move transfer-record creation and every status/log update into backend SQLite synchronization so the frontend is no longer responsible for durable state.

**Tech Stack:** Flask, Python threads and `queue.Queue`, SQLite, vanilla JavaScript, `unittest`.

## Global Constraints

- Browser page changes must not stop or advance either job; only explicit stop endpoints may stop work.
- A manually stopped running or waiting barcode has state `stopped`, increments `failed_count`, and appears in `failed_barcodes`.
- Existing retry limit remains 0–5 retries.
- Existing CRM slot/session workers remain the only objects that call Playwright.
- Existing transfer records and Docker volumes must not be deleted.
- Work directly on the current `main` checkout as explicitly requested by the user.

---

### Task 1: Add backend-job regression tests

**Files:**
- Create: `tests/test_background_jobs.py`

**Interfaces:**
- Produces expected route contracts for Tasks 2 and 3.

- [ ] **Step 1: Write a failing query completion test**
  - Log in with the Flask test client.
  - Replace query workers with deterministic fake workers at the CRM boundary.
  - POST three barcodes once to `/api/crm/background-batch/start` with two slot IDs.
  - Do not call status while the job runs; wait, then GET status once and assert all three items are `success`.

- [ ] **Step 2: Run the focused test and verify RED**
  - Run `python3 -m unittest tests.test_background_jobs.BackgroundJobTests.test_background_query_finishes_without_frontend_polling -v`.
  - Expected: 404 because the background-batch route does not exist.

- [ ] **Step 3: Write a failing stop-accounting test**
  - Start a background query with one slow fake worker.
  - POST `/api/crm/background-batch/stop` after the first item starts.
  - Assert every unfinished item becomes `stopped`, `failed_count == total`, and all barcodes appear in `failed_barcodes`.

- [ ] **Step 4: Write a failing transfer persistence test**
  - Run `_run_transfer_job` with a fake transfer worker and a temporary transfer SQLite path.
  - Assert the final success state, order number, and logs exist in SQLite without any frontend upsert request.

### Task 2: Implement the server-owned background query dispatcher

**Files:**
- Modify: `app.py` around batch job state, helpers, runners, and routes
- Test: `tests/test_background_jobs.py`

**Interfaces:**
- `background_query_jobs: dict[str, dict]` and `latest_background_query_job_by_owner: dict[str, str]`.
- `_empty_background_query_job(owner, barcodes, slot_ids, retry_limit) -> dict`.
- `_run_background_query_job(job_id, workers) -> None`.
- `_background_query_status_payload(job) -> dict`.
- `POST /api/crm/background-batch/start`.
- `GET /api/crm/background-batch/status`.
- `POST /api/crm/background-batch/stop`.

- [ ] **Step 1: Add the job model and log helpers**
  - Store original order, item state, assigned slot, attempts, elapsed time, per-item logs, counts, and global logs.
  - Key latest jobs by logged-in tool account so reopening `/crm` can recover the correct job without browser state.

- [ ] **Step 2: Add the queue runner**
  - Build one `queue.Queue` containing all barcodes.
  - Start one thread per selected query slot.
  - Each thread verifies/restores its CRM login once, repeatedly claims the next barcode, performs retries, updates the item, and continues until the queue is empty or stop is requested.
  - After threads join, convert any unprocessed items to `stopped` on manual stop or `error` when no usable channel remains.

- [ ] **Step 3: Add start/status/stop routes**
  - Start validates barcodes and selected query slot IDs, clears stale worker stop flags, stores the job, and starts one daemon coordinator thread.
  - Status accepts an optional `job_id`; without it, return the current account's latest job.
  - Stop marks the job and calls `request_stop()` on its workers.

- [ ] **Step 4: Run the query tests and verify GREEN**
  - Run both background query tests.

### Task 3: Persist transfer state entirely from the backend

**Files:**
- Modify: `app.py` transfer job model, log function, runner, and start route
- Modify: `templates/transfer.html`
- Test: `tests/test_background_jobs.py`

**Interfaces:**
- Transfer job stores `record_id` and `slot_label`.
- `_sync_transfer_record_from_job(job_id) -> None` upserts SQLite from server job state.

- [ ] **Step 1: Add backend transfer-record synchronization**
  - Create the SQLite record as soon as `/api/crm/transfer` accepts the task.
  - Synchronize after every backend log, order-number progress update, successful completion, failure, and exception.

- [ ] **Step 2: Remove frontend persistence responsibility**
  - Include the frontend-created `record_id` in the transfer start request.
  - Stop posting record state from polling/rendering functions; retain GET/load and explicit delete/clear APIs.
  - On page return, load SQLite records and poll any still-running server job.
  - Refresh the complete transfer-record table from SQLite once per second, independent of the selected transfer slot.

- [ ] **Step 3: Show a dedicated transfer-summary card**
  - After summary completion, show a separate “汇总产品数量明细” card between the form and realtime records.
  - Display product name/model, product code, quantity, barcode list, and totals before enabling submit.

- [ ] **Step 4: Run the transfer persistence test and verify GREEN**
  - Run `python3 -m unittest tests.test_background_jobs.BackgroundJobTests.test_transfer_job_persists_final_state_without_frontend -v`.

### Task 4: Replace the frontend query scheduler with server polling

**Files:**
- Modify: `templates/crm.html`
- Modify: `tests/test_frontend_contract.py` only for the API boundary contract already used by this project

**Interfaces:**
- Frontend stores only `crm_background_query_job_id`.
- `applyBackgroundQueryStatus(data)` maps backend items into the existing realtime table model.

- [ ] **Step 1: Change query start**
  - Send the full barcode list, selected slot IDs, and retry limit once to `/api/crm/background-batch/start`.
  - Remove browser-side barcode dispatch through `/api/crm/batch/start`.

- [ ] **Step 2: Change query polling and restore**
  - Poll `/api/crm/background-batch/status` only to display data.
  - Restore by stored job ID or the account's latest backend job.
  - Rendering may pause in a hidden tab, but the backend job must continue.

- [ ] **Step 3: Change query stop and retry list**
  - Stop through `/api/crm/background-batch/stop`.
  - Use backend `failed_barcodes`, including stopped items, for the retry button.

- [ ] **Step 4: Run frontend and route tests**
  - Run `python3 -m unittest tests.test_frontend_contract tests.test_frontend_routes -v`.

### Task 5: Verify and deploy

**Files:**
- Modify: `README.md` to document page-independent query/transfer execution.

- [ ] **Step 1: Run complete verification**
  - `python3 -m unittest discover -s tests -p 'test_*.py'`.
  - Extract inline scripts and run `node --check`.
  - `git diff --check`.

- [ ] **Step 2: Commit and push**
  - Commit only planned files and push `main` to GitHub.

- [ ] **Step 3: Deploy without deleting volumes**
  - On the cloud server run `git pull --ff-only origin main` and `docker compose up -d --build`.
  - Verify revision, container state, login route, persistent volume names, and server logs.
