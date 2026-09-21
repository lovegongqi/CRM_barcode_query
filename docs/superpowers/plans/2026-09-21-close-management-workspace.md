# Close Management Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the results page focused on barcode browsing and provide a separate, cross-client close-management page that starts bulk closes, shows active progress, retains close history, and lets administrators delete history safely.

**Architecture:** Flask remains the sole source of truth. A finished close job is copied to a small JSON history store while its live job remains in the existing in-memory job registry. The new `/service-close` page polls the live-job endpoint and history endpoint, so a second browser sees the same activity without relying on session storage. The results page links to this workspace and keeps its existing native date inputs unchanged.

**Tech Stack:** Python/Flask, standard-library JSON storage and locking, Jinja templates, vanilla JavaScript/CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-results-and-close-management-design.md`

## Global constraints

- Keep `#dateStart` and `#dateEnd` as their current native `input type="date"` controls; do not change their typed-input or calendar-picker behavior.
- Do not change the close execution flow itself or its CRM channel scheduling. Reuse `/api/service-close/start` and the existing threaded worker.
- A service close has at most one related order; this workspace does not alter the already-delivered service-order/product comparison feature.
- History mutations are administrator-only. The server, rather than button visibility, enforces that rule.
- Preserve existing result selection, copy/export, transfer, and deletion actions. Only remove the bulk-close action and its progress panel from the results page.
- Store only a bounded, sanitized finished-job snapshot; never serialize worker/browser objects or credentials.

## Review focus

- Cross-device means signed-in clients read the same current job and persisted history, not that separate close jobs may run concurrently.
- The history view shows newest finished records first and its records remain after page reload or service restart.
- A delete/clear confirmation occurs in the browser, but the matching endpoint also rejects non-admin callers.

---

### Task 1: Add a durable, server-owned close-history layer and enforce its API permissions

**Files:**
- Modify: `app.py`
- Create: `tests/test_service_close_history.py`

- [ ] **Step 1: Write failing persistence and permission tests.**
  - In a temporary runtime config directory (using the same module-variable patching pattern as the existing persistence tests), add two completed close-job payloads and assert `GET /api/service-close/history` returns the newer job first after reloading from disk.
  - Assert each returned record contains only serializable job fields needed by the UI: `job_id`, timestamps, counts, selected/missing/no-service barcodes, error/success state, logs, and service rows/results.
  - Log in as a non-admin results user and assert history reading succeeds but `DELETE /api/service-close/history/<job_id>` and `DELETE /api/service-close/history` receive `403` and leave the file unchanged.
  - Log in as admin and assert deleting one known ID removes only that record; then assert clearing removes all records.
  - Run: `PYTHONPATH=. pytest -q tests/test_service_close_history.py`
  - Expected: FAIL because the history helpers and endpoints do not exist.

- [ ] **Step 2: Define the history file and lock beside existing runtime data paths.**
  - Add `SERVICE_CLOSE_HISTORY_FILE = os.path.join(CONFIG_DIR, "service_close_history.json")`, `SERVICE_CLOSE_HISTORY_LIMIT = 500`, and a dedicated `service_close_history_lock` next to the existing close-job globals/config files.
  - Add narrow helpers: `_service_close_history_record(job)`, `load_service_close_history()`, `_save_service_close_history(records)`, `_append_service_close_history(job)`, `delete_service_close_history(job_id)`, and `clear_service_close_history()`.
  - `_service_close_history_record` must copy only JSON-safe primitives/lists/dicts used by the existing status payload. Deep-copy by JSON round trip (or equivalent) while holding no worker references. It must keep the completed `service_rows` with their service number, related barcodes, customer/product labels, state/message, and detail URL.
  - On malformed/missing history data, return an empty list rather than fail a result or close request. On write, create `CONFIG_DIR`, deduplicate by `job_id`, keep the newest 500 records, and write atomically through a same-directory temporary file plus replace.

- [ ] **Step 3: Persist one finished snapshot from the existing close worker.**
  - After `_run_service_close_job` has marked a job `done` and assigned its final counts/results, copy it under `service_close_job_lock` and append that copy to history outside the lock.
  - Do the same for the outer exception path so an unexpected terminal failure remains auditable.
  - Use a `history_saved`/deduplication guard keyed by `job_id`, ensuring delayed polling or a worker retry cannot create duplicate records.

- [ ] **Step 4: Add history API endpoints.**
  - Add `GET /api/service-close/history` returning `{success: true, records: [...]}` newest first.
  - Add `DELETE /api/service-close/history/<job_id>` and `DELETE /api/service-close/history`; both return a useful success message and 404 for a missing individual record.
  - Reuse the existing request permission gate for `results`; inside both mutation handlers, reject non-admin callers with `403` JSON. This allows normal results users to inspect shared history but not alter it.

- [ ] **Step 5: Verify the data contract.**
  - Run: `PYTHONPATH=. pytest -q tests/test_service_close_history.py tests/test_background_jobs.py`
  - Expected: PASS, including the pre-existing close-worker status tests.

- [ ] **Step 6: Commit the focused backend change.**
  - `git add app.py tests/test_service_close_history.py && git commit -m "Persist service close history"`

### Task 2: Expose a dedicated close-management route and keep results navigation/layout compact

**Files:**
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `static/aurora.css`
- Create: `templates/service_close.html`
- Modify: `tests/test_frontend_contract.py`

- [ ] **Step 1: Write failing route and template-contract tests.**
  - Extend the frontend page map with `service-close: service_close.html` and assert the new page uses the established Aurora shell assets and `data-aurora-page="service-close"`.
  - Assert both `index.html` and `service_close.html` contain workspace links labelled `条码列表` (`/`) and `结单管理` (`/service-close`), while only the new page contains the `批量结单` control.
  - Assert the result filter rule uses four compact desktop columns and retains `input type="date"` for `dateStart`/`dateEnd`.
  - Add a Flask route test showing `/service-close` is available to a results-permitted account and protected by the existing permission system.
  - Run: `PYTHONPATH=. pytest -q tests/test_frontend_contract.py tests/test_service_close_history.py`
  - Expected: FAIL because the route/template/navigation do not exist.

- [ ] **Step 2: Add the page route without expanding the primary navigation.**
  - Route `GET /service-close` renders `service_close.html` with `nav_links=visible_page_links()`, `business_config=business_config()`, and `can_manage_history=is_admin_account()`.
  - Add `/service-close` to `required_permission_for_path` as `results`.
  - Do not insert it into `PAGE_LINKS`: the user asked for workspace buttons at the left of the account/status bar, not another primary top-navigation item.

- [ ] **Step 3: Add shared-looking workspace links to the account bar.**
  - In `index.html` and `service_close.html`, add a left-side `aurora-workspace-links` group before the account-session group: `条码列表` and `结单管理`.
  - Use an active class based on the current page and compact styling in `static/aurora.css`; preserve the right-aligned username/logout controls and existing mobile behavior.

- [ ] **Step 4: Make only the results filters denser.**
  - In `templates/index.html`, replace the current `repeat(auto-fit, minmax(190px, 1fr))` result filter grid with four equal desktop columns sized to keep all four selects in one row at the normal desktop workspace width.
  - Preserve existing select behavior and the separate date row; add responsive 2-column/1-column fallbacks only at narrower widths. Do not modify the date input elements, handlers, or date-clear behavior.

- [ ] **Step 5: Remove close-only UI from the results page.**
  - Remove the `批量结单` action, `transferLogMain` close-progress panel, close-specific CSS, and the close job/session/polling JavaScript from `index.html`.
  - Keep barcode selection and every other result-page action unchanged.

- [ ] **Step 6: Create the structural shell for close management.**
  - Create `templates/service_close.html` using the same page header, top navigation, account-status handling, and dark Aurora styling as the results page.
  - Add labelled, initially empty regions for: selectable barcode list, start button, current-job progress/logs/service rows, and persisted close history. Render the delete/clear controls only when `can_manage_history` is true.

- [ ] **Step 7: Verify layout and access contract.**
  - Run: `PYTHONPATH=. pytest -q tests/test_frontend_contract.py tests/test_service_close_history.py`
  - Expected: PASS.

- [ ] **Step 8: Commit the route and results-surface change.**
  - `git add app.py templates/index.html templates/service_close.html static/aurora.css tests/test_frontend_contract.py && git commit -m "Add close management workspace"`

### Task 3: Implement cross-client live-job and history interaction in the new workspace

**Files:**
- Modify: `app.py`
- Modify: `templates/service_close.html`
- Modify: `tests/test_background_jobs.py`
- Modify: `tests/test_service_close_history.py`

- [ ] **Step 1: Write failing current-job discovery tests.**
  - Start or register a running close job without relying on the caller's session storage, then call `GET /api/service-close/status?latest=1` from a second test client and assert it receives that job ID, running state, progress rows, and logs.
  - Mark the job complete and assert the endpoint returns its final state; assert a no-job request returns the existing empty-job shape.
  - Run: `PYTHONPATH=. pytest -q tests/test_background_jobs.py tests/test_service_close_history.py`
  - Expected: FAIL because `latest=1` is not globally discoverable.

- [ ] **Step 2: Maintain a shared latest close-job ID.**
  - Add `latest_service_close_job_id` guarded by `service_close_job_lock`.
  - Set it when `/api/service-close/start` creates a job. Extend the status endpoint so `latest=1` selects this shared ID before the existing slot-based fallback; preserve explicit `job_id` behavior for compatibility with current callers.
  - Do not add a second scheduler or change worker count: all live progress still comes from the one existing job object.

- [ ] **Step 3: Build the close-management page interaction.**
  - Load the selectable barcode rows from the existing barcode API and keep selection local to the page.
  - On `批量结单`, confirm the selected count, post barcodes to the existing start endpoint, and immediately render the returned service rows.
  - Poll `GET /api/service-close/status?latest=1&since=<last log sequence>` every roughly 1.2 seconds while a job is running, and once on page load. Show current/total, channel/work status, per-service state/message, skipped barcodes, and final counts.
  - Load `GET /api/service-close/history` on page load and after a job completes. Render newest records first with an expandable service-row list and a link/button that opens the existing service-order detail API/document view for each preserved `detail_url`.
  - Do not resurrect the old results-page session-storage close state. The server endpoint is now the synchronization mechanism.

- [ ] **Step 4: Add safe admin history controls.**
  - For visible admin controls only, use `confirm()` before individual delete and clear-all requests; then call the matching DELETE endpoint and refresh history.
  - Show a meaningful empty state and server error message. A non-admin cannot invoke the controls through the UI and remains blocked by the endpoint if they call it directly.

- [ ] **Step 5: Verify end-to-end behavior.**
  - Run: `PYTHONPATH=. pytest -q tests/test_background_jobs.py tests/test_service_close_history.py tests/test_frontend_contract.py`
  - Manually verify in two authenticated browser tabs: start a close in 结单管理, observe progress in the other tab, reload the other tab after completion, and confirm the job appears in history. Confirm the results page has no close button and date controls still accept typing/picker input.

- [ ] **Step 6: Commit the synchronized workspace behavior.**
  - `git add app.py templates/service_close.html tests/test_background_jobs.py tests/test_service_close_history.py && git commit -m "Sync close management progress across clients"`

### Task 4: Perform the final regression check and prepare deployment

**Files:**
- Verify only; no planned code change.

- [ ] **Step 1: Run the full test suite.**
  - Run: `PYTHONPATH=. pytest -q`
  - Expected: all tests pass.

- [ ] **Step 2: Review the final change set.**
  - Run `git diff --check HEAD~3..HEAD` (or against the pre-feature base commit if commit count differs) and inspect `git status --short`.
  - Confirm no credentials, runtime JSON data, or generated files are staged.

- [ ] **Step 3: Deploy only after verification succeeds.**
  - Follow the established deployment workflow to the existing `mlmll.cn:5002` release target, restart the service, and open `/` plus `/service-close` to verify the deployed route.
  - Report the deployed commit and test result; do not claim cross-client completion until the two-tab browser check has succeeded.
