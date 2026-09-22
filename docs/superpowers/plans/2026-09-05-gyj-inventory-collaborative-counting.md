# GYJ Inventory Collaborative Counting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single-value, device-locked inventory counting with editable multi-entry totals, immediate lock-free serial reconciliation, partial completion, complete audit history, and administrator-only task reopening.

**Architecture:** Store every partial quantity as a versioned row and calculate the authoritative product total on the server. Use row-level optimistic concurrency for edits, a task version only for client invalidation, and database uniqueness for serial de-duplication. Keep GYJ access read-only and serialized through the existing per-account worker while allowing all tool devices to collaborate on persisted inventory state.

**Tech Stack:** Python 3, Flask, SQLite/WAL, Playwright-backed GYJ worker, vanilla JavaScript, HTML/CSS, `unittest`, Node `vm`, `openpyxl`

**Spec:** `docs/superpowers/specs/2026-09-05-gyj-inventory-collaborative-counting-design.md`

## Global Constraints

- Do not write to GYJ; only read product stock and serial-number reports.
- Do not persist, log, return, render, or export any price or amount fields.
- Do not record storage locations; partial counts are anonymous locations represented only by quantities such as `12 + 13 = 25`.
- Any inventory-authorized device may add, edit, or delete any partial count and may scan or delete serial records.
- Every mutation records actor, device, time, and before/after data in the audit log.
- Keep one active inventory task per tool-account owner.
- Preserve restart recovery, approximately one-second tool-state synchronization, and the existing 60-second GYJ refresh for counted products.
- Use test-driven development: add one focused failing test, confirm the expected failure, implement the minimum production change, then rerun focused and affected tests.
- Keep compatibility with existing SQLite files and previously completed inventory tasks.

## File Structure

- `inventory_store.py`: schema migration, partial-count persistence, optimistic concurrency, serial persistence without locks, task completion/reopening, audit and discrepancy generation.
- `inventory_service.py`: GYJ reads and orchestration for partial counts, serial reconciliation, completion confirmation, and task reopening.
- `app.py`: inventory REST endpoints, administrator enforcement, confirmation error payloads, public response shaping, and Excel exports.
- `templates/inventory.html`: partial-count editor, completion confirmation dialog, modification history, and administrator reopen controls.
- `static/inventory.js`: collaborative quantity editor, live totals, immediate serial workflow, partial completion, reopen flow, and live refresh behavior.
- `static/aurora.css`: styles for count-entry rows, equations, audit history, warnings, and action controls.
- `tests/test_inventory_store.py`: migration, count-entry transactions, serial concurrency, partial completion, reopening, and audit persistence.
- `tests/test_inventory_service.py`: GYJ-backed count-entry and serial orchestration behavior.
- `tests/test_inventory_routes.py`: endpoint contracts, permissions, confirmation payloads, and export behavior.
- `tests/test_inventory_frontend.py`: browser-state and DOM behavior under concurrent updates.
- `tests/test_frontend_contract.py`: required IDs and inventory page asset/markup contract.

---

### Task 1: Add the Versioned Partial-Count Schema and Compatibility Migration

**Files:**
- Modify: `inventory_store.py:145-315`
- Test: `tests/test_inventory_store.py`

**Interfaces:**
- Produces: table `inventory_count_entries(entry_id, task_id, barcode, quantity, version, created_by, created_device_id, created_at, updated_by, updated_device_id, updated_at)`.
- Produces: `InventoryStore._count_entries(connection, task_id, barcode) -> list[dict]`.
- Produces: `InventoryStore._item_with_counts(connection, row) -> dict` with `count_entries`, `count_total`, and `count_expression`.
- Consumes: existing `normalize_quantity()` and `_decimal_text()` helpers.

- [ ] **Step 1: Write migration tests before changing production code**

Add tests proving a new database creates the table and an old database backfills exactly one entry for each item that has `completed_counted_quantity`:

```python
def test_initialize_creates_count_entries_and_backfills_legacy_count_once(self):
    store = InventoryStore(self.db_path)
    task = store.create_task("admin", "管理员", self.catalog())
    with store.connect() as connection:
        connection.execute(
            "UPDATE inventory_items SET completed_counted_quantity = '25', "
            "counted_quantity = '25', completed_at = updated_at "
            "WHERE task_id = ? AND barcode = 'A1'",
            (task["task_id"],),
        )
        connection.commit()

    InventoryStore(self.db_path)
    InventoryStore(self.db_path)

    with sqlite3.connect(self.db_path) as connection:
        rows = connection.execute(
            "SELECT quantity, version, created_by FROM inventory_count_entries "
            "WHERE task_id = ? AND barcode = 'A1'",
            (task["task_id"],),
        ).fetchall()
    self.assertEqual(rows, [("25", 1, "system:migration")])
```

Also assert that an uncounted item is not backfilled and that no price-named columns exist in `PRAGMA table_info(inventory_count_entries)`.

- [ ] **Step 2: Run the migration tests and verify the expected failure**

Run:

```bash
python -m unittest tests.test_inventory_store.InventoryStoreTests.test_initialize_creates_count_entries_and_backfills_legacy_count_once -v
```

Expected: FAIL because `inventory_count_entries` does not exist.

- [ ] **Step 3: Implement schema creation and idempotent backfill**

Add the table and indexes inside `InventoryStore.initialize()`:

```sql
CREATE TABLE IF NOT EXISTS inventory_count_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    barcode TEXT NOT NULL,
    quantity TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_device_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id, barcode)
        REFERENCES inventory_items(task_id, barcode) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_inventory_count_entries_item
    ON inventory_count_entries(task_id, barcode, entry_id);
```

Backfill with one `NOT EXISTS`-guarded `INSERT ... SELECT`, using `completed_counted_quantity`, `completed_at OR updated_at`, and `system:migration`. Delete all legacy inventory lock rows during initialization because the new workflow does not honor them, but leave the lock table in place for schema compatibility.

Implement `_count_entries()` in entry order and `_item_with_counts()` so the equation omits `= total` for zero entries, returns `12 + 13 = 25` for multiple entries, and normalizes decimal totals without floating-point arithmetic.

- [ ] **Step 4: Verify migration and item serialization**

Run:

```bash
python -m unittest tests.test_inventory_store -v
```

Expected: all store tests PASS, including repeated initialization without duplicate backfill rows.

- [ ] **Step 5: Commit the migration**

```bash
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): add partial count entry storage"
```

---

### Task 2: Implement Atomic Partial-Count CRUD and Audit Events

**Files:**
- Modify: `inventory_store.py:820-965`
- Test: `tests/test_inventory_store.py`

**Interfaces:**
- Produces: `add_count_entry(owner, task_id, barcode, actor, device_id, quantity, book_quantity) -> dict`.
- Produces: `update_count_entry(owner, task_id, barcode, entry_id, entry_version, actor, device_id, quantity, book_quantity) -> dict`.
- Produces: `delete_count_entry(owner, task_id, barcode, entry_id, entry_version, actor, device_id, book_quantity) -> dict`.
- Produces: returned item fields `count_entries`, `count_total`, `count_expression`, `difference`, `state`, and `task_version`.
- Consumes: Task 1 schema and item serializers.

- [ ] **Step 1: Add failing tests for collaboration, calculation, and row conflicts**

Create focused tests with the following observable behavior:

```python
first = store.add_count_entry("admin", task_id, "A1", "甲", "device-a", "12", "30")
second = store.add_count_entry("admin", task_id, "A1", "乙", "device-b", "13", "30")
self.assertEqual(second["count_total"], "25")
self.assertEqual(second["count_expression"], "12 + 13 = 25")
self.assertEqual([row["created_by"] for row in second["count_entries"]], ["甲", "乙"])

edited = store.update_count_entry(
    "admin", task_id, "A1", first["count_entries"][0]["entry_id"], 1,
    "乙", "device-b", "14", "30",
)
self.assertEqual(edited["count_expression"], "14 + 13 = 27")
with self.assertRaises(InventoryVersionConflict):
    store.update_count_entry(
        "admin", task_id, "A1", first["count_entries"][0]["entry_id"], 1,
        "甲", "device-a", "15", "30",
    )
```

Add a delete test proving any device can delete another device's entry, totals recalculate, and the audit rows contain JSON `before`/`after` values for add, update, and delete.

- [ ] **Step 2: Run the new CRUD tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_store.InventoryStoreTests.test_partial_counts_are_shared_summed_and_row_versioned tests.test_inventory_store.InventoryStoreTests.test_any_device_can_delete_count_entry_with_audit -v
```

Expected: FAIL because the three store methods do not exist.

- [ ] **Step 3: Implement the minimum transactional CRUD path**

Add `_recalculate_item_from_entries(connection, task, item, book_quantity, timestamp)` which:

```python
total = sum((Decimal(row["quantity"]) for row in entries), Decimal("0"))
difference = quantity_difference(_decimal_text(total), book_quantity)
state = (
    "pending" if not entries else
    "matched" if difference == "0" else
    "serial_pending" if item["has_serial"] else
    "variance"
)
```

For add, use `BEGIN IMMEDIATE`, insert the normalized quantity, recalculate, bump the task version once, and audit `count_entry_added` with JSON details. For update/delete, select the entry by task and barcode and require the supplied entry version in the `UPDATE`/`DELETE` predicate. A zero affected-row result raises `InventoryVersionConflict` with the current task version. Do not call `claim_item`, inspect `inventory_item_locks`, or reject an already-counted item.

When a changed item remains `serial_pending`, set `serial_synced_at = NULL` so completion requires a fresh GYJ serial read. When its difference becomes zero, set `status = 'matched'` while retaining old serial scans only as audit history.

- [ ] **Step 4: Run store tests and inspect audit details**

Run:

```bash
python -m unittest tests.test_inventory_store -v
```

Expected: PASS; audit JSON includes `entry_id`, `before`, and `after`, and unrelated entry updates do not conflict.

- [ ] **Step 5: Commit partial-count persistence**

```bash
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): support collaborative partial counts"
```

---

### Task 3: Expose GYJ-Validated Partial-Count APIs and Retire Quantity Locks

**Files:**
- Modify: `inventory_service.py:90-160`
- Modify: `app.py:10730-10860`
- Test: `tests/test_inventory_service.py`
- Test: `tests/test_inventory_routes.py`

**Interfaces:**
- Produces service methods `open_count_item(owner, task_id, barcode, actor)`, `add_count_entry(...)`, `update_count_entry(...)`, and `delete_count_entry(...)`.
- Produces routes:
  - `GET /api/inventory/tasks/<task_id>/items/<barcode>/count-entries`
  - `POST /api/inventory/tasks/<task_id>/items/<barcode>/count-entries`
  - `POST /api/inventory/tasks/<task_id>/items/<barcode>/count-entries/<entry_id>`
  - `DELETE /api/inventory/tasks/<task_id>/items/<barcode>/count-entries/<entry_id>`
- Consumes: Task 2 store methods.

- [ ] **Step 1: Add failing service tests for a fresh GYJ read on every write**

Test that opening a completed item is permitted and that add, update, and delete each call `read_inventory_stock(barcode)` before persistence:

```python
created = self.service.add_count_entry(
    "admin", task_id, "A1", "device-a", "甲", "12"
)
entry = created["count_entries"][0]
self.worker.stock["A1"] = "31"
updated = self.service.update_count_entry(
    "admin", task_id, "A1", entry["entry_id"], entry["version"],
    "device-b", "乙", "13",
)
self.assertEqual(self.worker.stock_reads, ["A1", "A1"])
self.assertEqual(updated["book_qty"], "31")
```

Add a failure test where GYJ returns an error and assert the entry is unchanged.

- [ ] **Step 2: Add failing route contract and permission tests**

Assert JSON bodies use exact fields:

```json
{"device_id":"device-a","quantity":"12"}
{"device_id":"device-b","quantity":"13","entry_version":1}
```

Assert the DELETE request also requires `device_id` and `entry_version`, a user without inventory permission receives 403, and old `/claim`, `/heartbeat`, and `/count` mutations return 409 with `refresh_required: true`.

- [ ] **Step 3: Run focused service and route tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_service.InventoryServiceTests.test_partial_count_writes_refresh_gyj_without_locks tests.test_inventory_routes.InventoryRouteTest.test_partial_count_entry_routes -v
```

Expected: FAIL because the methods and routes are absent.

- [ ] **Step 4: Implement service orchestration and Flask routes**

Use one helper in `InventoryService`:

```python
def _live_stock(self, owner, barcode):
    value = self._call_worker(owner, "read_inventory_stock", barcode)
    try:
        return normalize_quantity(value)
    except ValueError as exc:
        raise InventoryServiceError("GYJ 返回的库存数量无效") from exc
```

Each mutation reads stock first and then invokes the matching Task 2 store method. Keep actor from `_inventory_identity()` and device ID from `_inventory_device_id(data)`. Validate `entry_id`, `entry_version`, and `quantity` before calling the service. Return the standard mutation envelope with the latest task version.

Change the old quantity endpoints to a deterministic 409 response that tells stale open pages to refresh; do not silently translate a legacy single-value submission into a partial entry.

- [ ] **Step 5: Verify the backend contract**

Run:

```bash
python -m unittest tests.test_inventory_service tests.test_inventory_routes -v
```

Expected: PASS with no lock acquisition or heartbeat in partial-count tests.

- [ ] **Step 6: Commit the API layer**

```bash
git add inventory_service.py app.py tests/test_inventory_service.py tests/test_inventory_routes.py
git commit -m "feat(inventory): expose partial count APIs"
```

---

### Task 4: Build the Editable Partial-Count Interface

**Files:**
- Modify: `templates/inventory.html:120-147`
- Modify: `static/inventory.js:300-760`
- Modify: `static/aurora.css`
- Test: `tests/test_inventory_frontend.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces DOM IDs `inventoryCountEntries`, `inventoryCountExpression`, `inventoryCountNewQuantity`, `inventoryCountAdd`, and `inventoryCountAuditButton`.
- Consumes: Task 3 REST routes and item response fields.

- [ ] **Step 1: Write failing markup and frontend behavior tests**

Extend the contract test to require the five DOM IDs. Add Node `vm` tests proving:

```javascript
inventoryTask = {task_id: 'T1', version: 4, phase: 'counting', items: [{
  barcode: 'A1', state: 'matched', count_total: '25',
  count_expression: '12 + 13 = 25',
  count_entries: [
    {entry_id: 1, quantity: '12', version: 1, created_by: '甲'},
    {entry_id: 2, quantity: '13', version: 1, created_by: '乙'},
  ],
}]};
```

renders two editable rows, keeps the counted card enabled, and POSTs an edit with `entry_version`. Add a concurrent refresh test proving an unsaved `inventoryCountNewQuantity.value` is preserved while committed rows update.

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```bash
python -m unittest tests.test_frontend_contract tests.test_inventory_frontend -v
```

Expected: FAIL on missing DOM IDs and single-value count behavior.

- [ ] **Step 3: Replace the single-value modal with entry rows**

Keep `openCountItem(barcode)` but make it a lock-free GET. Render each row with quantity, creator/time, updater/time, `修改`, and `删除`. Provide a separate add input and calculate the displayed equation from the server response rather than JavaScript floating-point arithmetic.

Convert inventory cards from disabled completed buttons into accessible containers with explicit `修改数量` actions. Remove count heartbeat timers and all frontend calls to `/claim`, `/heartbeat`, and legacy `/count`.

On add/edit/delete success, replace the item in `inventoryTask.items`, rerender summary/card/modal, and force a task poll. On entry-version conflict, preserve unsaved input, show the server message, and fetch the latest item.

- [ ] **Step 4: Verify UI logic and asset contracts**

Run:

```bash
python -m unittest tests.test_frontend_contract tests.test_inventory_frontend -v
```

Expected: PASS; no test observes a count claim or heartbeat request.

- [ ] **Step 5: Commit the quantity UI**

```bash
git add templates/inventory.html static/inventory.js static/aurora.css tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): add editable partial count UI"
```

---

### Task 5: Remove Serial Locks and Permit Immediate Reconciliation

**Files:**
- Modify: `inventory_store.py:1070-1475`
- Modify: `inventory_service.py:250-410`
- Modify: `app.py:10855-10940`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_service.py`
- Test: `tests/test_inventory_routes.py`

**Interfaces:**
- Produces lock-free `open_serial_item`, `refresh_serial_item`, `scan_serial`, `delete_serial_scan`, and `finish_serial_item` behavior in both `counting` and `serial_check` phases.
- Consumes: existing unique index `idx_inventory_serial_scans_active` and Task 2 state recalculation.

- [ ] **Step 1: Write failing serial collaboration tests**

Create a serial-pending item while another catalog item remains uncounted, assert task phase stays `counting`, then call serial open/scan from two devices without claiming:

```python
detail = service.open_serial_item("admin", task_id, "B2", "device-a", "甲")
first = service.scan_serial("admin", task_id, "B2", "device-a", "甲", "B-1")
duplicate = service.scan_serial("admin", task_id, "B2", "device-b", "乙", "B-1")
self.assertEqual(first["serial"], duplicate["serial"])
self.assertEqual(store.serial_reconciliation("admin", task_id, "B2")["counts"]["matched"], 1)
```

Add a test proving device B can delete device A's scan and the audit row identifies device B. Add a finish test requiring a fresh GYJ serial refresh but no lock.

- [ ] **Step 2: Run focused serial tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_service.InventoryServiceTests.test_serial_reconciliation_is_immediate_and_lock_free tests.test_inventory_store.InventoryStoreTests.test_any_device_can_delete_another_devices_serial_scan -v
```

Expected: FAIL with “当前任务不在序列号核对阶段” or missing lock.

- [ ] **Step 3: Replace lock validation with transactional item validation**

Replace `_serial_write_context()` with `_serial_mutation_context()` that accepts phases `counting` and `serial_check`, requires item status `serial_pending`, and does not read `inventory_item_locks`. Keep actor and device ID on serial scan and audit records.

Update service `_serial_item()` to accept both phases. `open_serial_item()` refreshes expected serials directly under the existing per-item process guard, without `claim_item()` or failure-time lock release. Duplicate active inserts must catch the unique constraint, return the existing active scan, and avoid incrementing counts.

Keep a forced GYJ refresh immediately before `complete_serial_item()` changes status to `serial_complete`.

- [ ] **Step 4: Verify service, store, and route behavior**

Run:

```bash
python -m unittest tests.test_inventory_store tests.test_inventory_service tests.test_inventory_routes -v
```

Expected: PASS, including immediate serial reconciliation while other products remain pending.

- [ ] **Step 5: Commit lock-free serial collaboration**

```bash
git add inventory_store.py inventory_service.py app.py tests/test_inventory_store.py tests/test_inventory_service.py tests/test_inventory_routes.py
git commit -m "feat(inventory): allow immediate lock-free serial checks"
```

---

### Task 6: Expose Immediate Multi-Device Serial Reconciliation in the UI

**Files:**
- Modify: `static/inventory.js:320-445, 790-1230`
- Modify: `templates/inventory.html:148-176`
- Modify: `static/aurora.css`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Produces an explicit `核对序列号` action on every `serial_pending` card and a visible serial queue during `counting` or `serial_check`.
- Consumes: Task 5 lock-free serial routes.

- [ ] **Step 1: Add failing frontend tests for immediate serial actions**

Assert `renderSerialQueue()` shows a serial-pending item while `task.phase === 'counting'`, and `openSerialItem()` sends `/serial/open` without a prior claim. Assert two successive task refreshes merge remote scan results while leaving a locally typed, unsubmitted serial in the input.

- [ ] **Step 2: Run the focused frontend tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_counting_phase_exposes_serial_reconciliation tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_serial_refresh_preserves_unsubmitted_scan -v
```

Expected: FAIL because the queue and open handler require `serial_check`.

- [ ] **Step 3: Update serial rendering and remove heartbeat state**

Show the serial queue whenever the active task contains `serial_pending` rows. On each applicable product card, render separate `修改数量` and `核对序列号` controls. Accept `counting` and `serial_check` in `openSerialItem()` and keep the dialog open across ordinary task phase changes.

Delete `sendSerialHeartbeat`, the serial heartbeat timer, lock-owner read-only rendering, and all serial heartbeat calls. Continue refreshing the open reconciliation when task version changes; update committed groups without overwriting a nonempty scan input.

- [ ] **Step 4: Verify frontend behavior**

Run:

```bash
python -m unittest tests.test_inventory_frontend tests.test_frontend_contract -v
```

Expected: PASS; the frontend contains no `/heartbeat` call for inventory quantity or serial workflows.

- [ ] **Step 5: Commit immediate serial UI**

```bash
git add static/inventory.js templates/inventory.html static/aurora.css tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): show immediate serial reconciliation"
```

---

### Task 7: Support Partial Completion and Explicit Unverified-Serial Confirmation

**Files:**
- Modify: `inventory_store.py:1510-1655`
- Modify: `inventory_service.py:400-420`
- Modify: `app.py:10935-10955`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_service.py`
- Test: `tests/test_inventory_routes.py`

**Interfaces:**
- Produces: `InventoryConfirmationRequired(InventoryConflict)` with `pending_serial_count`.
- Changes: `complete_task(owner, task_id, actor, allow_unverified_serials=False, expected_version=None) -> dict`.
- Produces discrepancy kind `serial_unverified`.

- [ ] **Step 1: Write failing partial-completion tests**

Create a task with one uncounted item, one matched item, one ordinary variance, and one `serial_pending` item. Assert the first completion call raises `InventoryConfirmationRequired` without changing task phase or discrepancies. Then call with `allow_unverified_serials=True` and assert:

```python
self.assertEqual(completed["phase"], "completed")
self.assertEqual(completed["uncounted_items"], 1)
self.assertEqual(completed["unverified_serial_items"], 1)
self.assertEqual(
    store.list_discrepancies("admin", "pending")[0]["kind"],
    "serial_unverified",
)
```

Assert the uncounted product creates no discrepancy row and a matched counted product affects statistics but not discrepancies.

- [ ] **Step 2: Run focused completion tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_store.InventoryStoreTests.test_partial_completion_requires_serial_confirmation_and_excludes_uncounted -v
```

Expected: FAIL because completion currently rejects unfinished quantities or serials.

- [ ] **Step 3: Implement transactional partial completion**

Add `InventoryConfirmationRequired` and export it to `app.py`. In `complete_task()`, derive counted scope from existence of `inventory_count_entries`; do not require all catalog rows to be counted. Before any mutation, count `serial_pending` rows in that scope and raise confirmation-required unless the explicit boolean is true.

Generate `serial_unverified` discrepancy rows without invented serial values. Record an audit JSON payload containing `counted_items`, `uncounted_items`, `unverified_serial_items`, and `allow_unverified_serials`. Preserve idempotence for an already completed task.

Update the route to accept only a real JSON boolean for `allow_unverified_serials`. Map the confirmation exception to HTTP 409:

```json
{
  "success": false,
  "confirmation_required": true,
  "pending_serial_count": 1,
  "error": "仍有 1 个商品未核对序列号"
}
```

- [ ] **Step 4: Verify completion and discrepancy tests**

Run:

```bash
python -m unittest tests.test_inventory_store tests.test_inventory_service tests.test_inventory_routes -v
```

Expected: PASS; failed first confirmation leaves task version and discrepancy tables unchanged.

- [ ] **Step 5: Commit partial completion**

```bash
git add inventory_store.py inventory_service.py app.py tests/test_inventory_store.py tests/test_inventory_service.py tests/test_inventory_routes.py
git commit -m "feat(inventory): allow confirmed partial completion"
```

---

### Task 8: Add Administrator-Only Task Reopening

**Files:**
- Modify: `inventory_store.py:1650-1745`
- Modify: `inventory_service.py:410-440`
- Modify: `app.py:10765-10810`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_service.py`
- Test: `tests/test_inventory_routes.py`

**Interfaces:**
- Produces: `reopen_task(owner, task_id, actor) -> dict` in store and service.
- Produces: `POST /api/inventory/tasks/<task_id>/reopen` for administrators only.
- Consumes: Task 1 migration helper and Task 2 item recalculation.

- [ ] **Step 1: Write failing reopen transaction and permission tests**

Assert reopening a completed task clears `completed_at`, sets phase to `counting`, preserves count entries/scans/notes/audit rows, removes old task discrepancies, recalculates items from current GYJ stock, and adds `task_reopened`. Assert a second active task causes an atomic `InventoryConflict` with no mutations.

At the route layer, assert a normal inventory user receives 403 while an administrator receives the reopened task. Use a service mock to prove the unauthorized request never invokes `reopen_task()`.

- [ ] **Step 2: Run focused reopen tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_store.InventoryStoreTests.test_admin_reopens_completed_task_without_losing_entries tests.test_inventory_routes.InventoryRouteTest.test_only_admin_can_reopen_inventory_task -v
```

Expected: FAIL because reopening does not exist.

- [ ] **Step 3: Implement store, service, and route reopening**

The service reads current stock totals through `read_inventory_stock_totals()` before calling the store. The store starts `BEGIN IMMEDIATE`, verifies the target belongs to the owner and is completed, verifies no other task for the owner has an active phase, and then:

```sql
UPDATE inventory_tasks
SET phase = 'counting', completed_at = NULL,
    gyj_status = 'synced', sync_resume_phase = NULL, last_sync_at = ?
WHERE task_id = ?;
DELETE FROM inventory_discrepancies WHERE task_id = ?;
```

Backfill any legacy counts, update each item's latest book value, recalculate counted rows, leave uncounted rows pending, invalidate serial completion when a current nonzero serial difference exists, bump the task version once, and audit the before/after phase.

The route must check `is_admin_account()` before reading the task or calling the service.

- [ ] **Step 4: Verify reopen and restart behavior**

Run:

```bash
python -m unittest tests.test_inventory_store tests.test_inventory_service tests.test_inventory_routes -v
```

Expected: PASS; reloading `InventoryStore` after reopen returns the same task as active with preserved entries.

- [ ] **Step 5: Commit task reopening**

```bash
git add inventory_store.py inventory_service.py app.py tests/test_inventory_store.py tests/test_inventory_service.py tests/test_inventory_routes.py
git commit -m "feat(inventory): let admins reopen completed tasks"
```

---

### Task 9: Complete the UI, Audit View, Exports, and End-to-End Verification

**Files:**
- Modify: `templates/inventory.html:35-118`
- Modify: `static/inventory.js:1200-1810`
- Modify: `static/aurora.css`
- Modify: `app.py:10650-10730, 10940-11000`
- Test: `tests/test_inventory_frontend.py`
- Test: `tests/test_frontend_contract.py`
- Test: `tests/test_inventory_routes.py`
- Test: `tests/test_export_xlsx.py`

**Interfaces:**
- Produces DOM controls `inventoryCompleteTask`, `inventoryCompletionConfirmDialog`, `inventoryCompletionConfirm`, and `inventoryAuditDialog`.
- Produces administrator-only history action `继续盘点`.
- Consumes: Task 7 completion contract and Task 8 reopen endpoint.

- [ ] **Step 1: Write failing completion, reopen, audit, and export tests**

Frontend tests must prove:

- `完成盘点` is visible during an active task.
- a 409 with `confirmation_required` opens the warning dialog and a second request includes `allow_unverified_serials: true`;
- non-admin history rows never render `继续盘点`;
- admin history rows render it and handle an active-task conflict without removing history content;
- the modification dialog renders actor, time, event type, and before/after quantities.

Route/export tests must prove `serial_unverified` appears in both the product summary and serial detail workbook sheets with Chinese label `序列号未核对`, while uncounted products do not appear. Assert workbook headers and cells contain no price or amount terms.

- [ ] **Step 2: Run the new UI and export tests and verify RED**

Run:

```bash
python -m unittest tests.test_inventory_frontend tests.test_frontend_contract tests.test_inventory_routes tests.test_export_xlsx -v
```

Expected: FAIL on missing controls, reopen action, audit rendering, and unverified export classification.

- [ ] **Step 3: Implement completion and reopen interactions**

Add the active-task completion button and an accessible confirmation dialog. `completeInventoryTask(false)` sends the first request; on confirmation-required it displays the returned count, and the confirm control calls `completeInventoryTask(true)`. Disable controls only while their own request is in flight.

In history rendering, append `继续盘点` only when `INVENTORY_IS_ADMIN` is true. After success, switch to the current tab, clear history pagination, reset `lastInventoryVersion`, and poll the reopened task. On conflict, retain the history row and show the server error.

- [ ] **Step 4: Implement modification history and export labels**

Expose a read-only audit endpoint filtered by task and optional barcode using existing audit rows. Render event labels for count entry add/update/delete, serial add/delete/reclassification, task completion, and task reopening. Parse audit JSON only on the server and return allowed display fields; never inject raw details with `innerHTML`.

Update `_inventory_public_item`, summary counts, history payloads, and Excel writers to use authoritative `count_total`. Add `序列号未核对` rows without fabricating a serial number. Keep existing discrepancy notes, archive/restore rules, and price-field exclusion.

- [ ] **Step 5: Run all inventory and export tests**

Run:

```bash
python -m unittest tests.test_inventory_store tests.test_inventory_service tests.test_inventory_worker tests.test_inventory_routes tests.test_inventory_frontend tests.test_frontend_contract tests.test_export_xlsx -v
```

Expected: PASS with no warnings or leaked background processes.

- [ ] **Step 6: Run the complete regression suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 7: Perform local browser verification**

Restart the local service, then verify in two browser tabs:

1. tab A adds `12`, tab B adds `13`, and both show `12 + 13 = 25`;
2. tab B edits tab A's entry and the audit view names tab B's account;
3. a serial-difference item opens reconciliation while other products remain pending;
4. both tabs can scan and see merged serial results without a lock badge;
5. partial completion warns about unverified serials, then completes after confirmation;
6. a normal account cannot see or call reopen;
7. an administrator reopens the task when no other task is active and sees preserved entries.

Use the browser network and server log to confirm no inventory claim or heartbeat requests are emitted.

- [ ] **Step 8: Commit the completed workflow**

```bash
git add templates/inventory.html static/inventory.js static/aurora.css app.py tests/test_inventory_frontend.py tests/test_frontend_contract.py tests/test_inventory_routes.py tests/test_export_xlsx.py
git commit -m "feat(inventory): finish collaborative stocktake workflow"
```

- [ ] **Step 9: Record final verification evidence**

Run:

```bash
git status --short --branch
git log --oneline -10
```

Expected: clean working tree; the nine task commits are present on the current feature branch. Do not push GitHub or deploy to the NAS unless the user separately requests those external updates.
