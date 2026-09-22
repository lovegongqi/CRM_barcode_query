# Non-blocking Inventory Count and Serial Prefetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist physical quantity and serial input immediately while GYJ book data loads in the background.

**Architecture:** Count mutations use the item’s cached book quantity and rely on the existing 60-second task stock sync. Serialized count completion schedules a deduplicated daemon prefetch. Serial open is cache-only; forced refresh reads GYJ outside the serial mutation lock and commits reclassification under the lock.

**Tech Stack:** Python 3, Flask, SQLite, threading, vanilla JavaScript, Node `vm`, Python `unittest`/pytest

**Spec:** `docs/superpowers/specs/2026-09-08-inventory-nonblocking-count-serial-prefetch-design.md`

## Global Constraints

- Physical input must never wait for GYJ.
- Existing scans and cached expected serials must survive refresh failure.
- Slow GYJ serial reads must not hold `_serial_guard`.
- Automatic refresh audit noise stays hidden.
- No new external dependency or persistent job queue.

---

### Task 1: Make count mutations cache-first and schedule serial prefetch

**Files:**
- Modify: `inventory_service.py`
- Modify: `app.py`
- Test: `tests/test_inventory_service.py`
- Test: `tests/test_inventory_routes.py`

**Interfaces:**
- Produces `InventoryService._cached_book_quantity(item) -> str`.
- Produces `_ensure_inventory_serial_prefetch(owner, task_id, barcode, device_id) -> bool`.

- [x] **Step 1: Write failing service tests**

Add a worker whose `read_inventory_stock` raises and assert `add_count_entry`, `update_count_entry`, and `delete_count_entry` still persist using literal cached book quantity `"7"`.

- [x] **Step 2: Run the service tests and verify RED**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_service.py -k 'count_entry_mutations_do_not_wait_for_live_stock'`

Expected: FAIL because the current mutations call `_live_stock`.

- [x] **Step 3: Implement cached count mutations**

Select the first non-empty value from `latest_book_qty`, `open_book_qty`, `book_quantity`, and `initial_stock`, normalize it, and pass it to the existing store mutation methods. Do not call `_live_stock` from entry mutations.

- [x] **Step 4: Write and run a failing route prefetch test**

Patch the prefetch launcher, POST a serialized count entry, and assert the successful response returns before a blocked serial read and schedules exactly `(owner, task_id, barcode, device_id)` once. Verify a non-serialized item does not schedule it.

- [x] **Step 5: Implement the deduplicated prefetch launcher**

Use a lock plus a dictionary keyed by `(owner, task_id, barcode)`. The daemon target calls `inventory_service.refresh_serial_item(..., actor="system", force=False)` and always removes its own thread entry in `finally`. Schedule only when the returned item has `has_serial` and `state == "serial_pending"`.

- [x] **Step 6: Verify Task 1**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_service.py tests/test_inventory_routes.py`

---

### Task 2: Allow serial scans during slow refresh

**Files:**
- Modify: `inventory_service.py`
- Test: `tests/test_inventory_service.py`

**Interfaces:**
- `open_serial_item(...)` returns cached reconciliation without a worker call.
- `refresh_serial_item(...)` performs external read outside `_serial_guard` and returns refreshed reconciliation.

- [x] **Step 1: Write the failing concurrency test**

Block `read_inventory_serials` with an event, start `refresh_serial_item` on a thread, call `scan_serial("S001")` before releasing the event, and assert the scan is stored as `unknown` without waiting. Release the event and assert the refreshed result reclassifies `S001` as `matched`.

- [x] **Step 2: Verify RED**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_service.py -k 'scan_does_not_wait_for_serial_refresh'`

Expected: FAIL because refresh currently holds `_serial_guard` during the worker call.

- [x] **Step 3: Split validate/read/commit**

Make `open_serial_item` reopen if needed and return `store.serial_reconciliation` under a short guard. In `refresh_serial_item`, validate under the guard, release it for `_call_worker`, validate rows, then reacquire it, revalidate `serial_pending`, and call `replace_expected_serials`.

- [x] **Step 4: Verify Task 2**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_service.py -k serial`

---

### Task 3: Enable the dialogs before background refresh completes

**Files:**
- Modify: `static/inventory.js`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- `openCountItem(barcode)` renders the task snapshot and enables editing synchronously.
- `openSerialItem(barcode)` awaits cache-only open, enables input/camera, then starts a forced refresh independently.

- [x] **Step 1: Write failing frontend tests**

Use deferred fetch responses. Assert quantity input is enabled before the count GET resolves; assert serial input and camera are enabled after cache-open resolves while forced refresh remains pending; reject refresh and assert input plus “重新获取” remain enabled and existing reconciliation remains rendered.

- [x] **Step 2: Verify RED**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_frontend.py -k 'count_dialog_is_immediately_editable or serial_dialog_refreshes_in_background'`

Expected: FAIL because both dialogs currently gate editing on GYJ-backed requests.

- [x] **Step 3: Implement immediate count editing**

Remove the count-open GET dependency, set `countDialogEditable = true`, enable controls, focus input, and label the shown quantity as cached/background-updating. Make `applyCountEntryResult` render success immediately and trigger `pollInventoryTask({force:true})` without awaiting it.

- [x] **Step 4: Implement background serial refresh and retry state**

After cache-open succeeds, enable controls, start `refreshSerialItem(true, 'opening')` outside the serial mutation queue, show “正在后台刷新账面序列号…”, and on failure keep controls enabled with the manual refresh button available.

- [x] **Step 5: Verify Task 3 and full regression**

Run: `PYTHONPATH=. pytest -q tests/test_inventory_frontend.py tests/test_frontend_contract.py`

Run: `PYTHONPATH=. pytest -q`

Expected: all tests pass and `git diff --check` is clean.

---

### Task 4: Review and deploy

**Files:**
- Verify only; no planned production edits.

**Interfaces:**
- GitHub `main` and NAS container run the same verified commit.

- [x] **Step 1: Review the complete diff against the spec**

Reject any synchronous GYJ call in count entry mutations or any worker call made while `_serial_guard` is held.

- [ ] **Step 2: Push and deploy the exact commit**

Push `main`, build the same Git archive on NAS, preserve a rollback image tag, recreate the existing Compose service with its named volumes, and verify `/login` is HTTP 200 while unauthenticated `/inventory` and `/transfer` redirect.
