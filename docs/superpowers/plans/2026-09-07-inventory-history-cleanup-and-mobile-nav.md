# Inventory History Cleanup and Mobile Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make completed stocktakes safe to resume and delete, make modification history useful, hide exhausted pagination, and collapse the three warehouse-related mobile navigation entries into one expandable entry.

**Architecture:** Keep the inventory behavior inside the existing Flask/store/service boundaries and the page behavior inside the existing vanilla JavaScript/CSS files. Reuse the same GYJ catalog reader for new and reopened tasks, reuse SQLite foreign-key cascades for task deletion, return missing catalog barcodes as ephemeral reopen metadata, and enhance the shared navigation after its existing links are rendered so permission filtering remains authoritative.

**Tech Stack:** Python 3, Flask, SQLite, vanilla JavaScript/CSS, unittest, Node `vm` frontend tests.

**Spec:** User-approved in this task on 2026-09-07.

## Global Constraints

- Only administrators may delete completed historical tasks.
- Deletion is permanent and requires a browser confirmation.
- Reopened tasks read the same current GYJ catalog as new tasks; historical items absent from that catalog remain in the task with current book quantity `0`.
- Automatic serial refresh and automatic reclassification events are omitted from the human modification history.
- Mobile warehouse grouping must preserve server-provided navigation permissions; desktop navigation remains unchanged.

---

### Task 1: Resume tasks containing products missing from GYJ

**Files:**
- Modify: `inventory_store.py`
- Modify: `inventory_service.py`
- Modify: `static/inventory.js`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Produces: `InventoryService.reopen_task(...)` derives current totals from `load_inventory_catalog`, matching new-task catalog acquisition.
- Produces: `InventoryStore.reopen_task(...)` returns `missing_barcodes: list[str]` and recalculates historical items absent from the current catalog with stock `0`.
- Consumes: the existing reopen API response without adding persistent schema.

- [x] **Step 1: Write a failing store test** proving a completed task reopens when a historical barcode is absent from `stock_totals`, retains the item and uses latest book quantity `0`.
- [x] **Step 2: Run the focused store test** and confirm it fails with `GYJ 库存缺少商品`.
- [x] **Step 3: Implement the minimal fallback** by recording missing barcodes and normalizing their totals to `0`.
- [x] **Step 4: Write and run a frontend test** proving the reopen success notice names missing barcodes.
- [x] **Step 5: Implement the warning notice** and run the focused tests to green.

### Task 2: Administrator deletion of completed tasks

**Files:**
- Modify: `inventory_store.py`
- Modify: `app.py`
- Modify: `static/inventory.js`
- Modify: `static/inventory.css`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_routes.py`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Produces: `InventoryStore.delete_completed_task(owner, task_id)`.
- Produces: `DELETE /api/inventory/tasks/<task_id>` with administrator enforcement.

- [x] **Step 1: Write failing store and route tests** for owner-scoped cascade deletion and non-admin rejection.
- [x] **Step 2: Run the focused tests** and confirm missing method/route failures.
- [x] **Step 3: Implement the minimal store method and Flask route** restricted to completed tasks.
- [x] **Step 4: Write a failing frontend test** proving only admins see Delete, cancellation sends no request, and confirmation reloads history.
- [x] **Step 5: Implement the button and confirmation flow** and run focused tests to green.

### Task 3: Human-readable audit history

**Files:**
- Modify: `inventory_store.py`
- Modify: `static/inventory.js`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Extends each visible audit event with `product_name`.
- Omits `serial_expected_refreshed` and `serial_scan_reclassified` from visible modification history.

- [x] **Step 1: Write failing store tests** proving sync-only events are hidden and quantity events contain the product name.
- [x] **Step 2: Run the focused store tests** and confirm the current event list fails those expectations.
- [x] **Step 3: Join task items when reading audit events and narrow the visible allowlist** without deleting stored audit rows.
- [x] **Step 4: Write a failing frontend test** proving a quantity entry displays `barcode · product_name`.
- [x] **Step 5: Implement the product line and run focused tests to green.**

### Task 4: Exhausted history pagination visibility

**Files:**
- Modify: `static/inventory.css`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Preserves the existing 20-task pagination contract and ensures `[hidden]` always removes the button visually.

- [x] **Step 1: Write a failing style/DOM behavior test** for `inventoryHistoryLoadMore.hidden === true` being visually hidden.
- [x] **Step 2: Run it and confirm the missing override causes failure.**
- [x] **Step 3: Add the narrow `[hidden] { display: none !important; }` rule and rerun the test.**

### Task 5: Expandable mobile Warehouse navigation

**Files:**
- Modify: `static/aurora.js`
- Modify: `static/aurora.css`
- Test: `tests/test_aurora_frontend.py`

**Interfaces:**
- Mobile: one `仓库` toggle replaces available `/transfer`, `/inbound`, and `/inventory` links and reveals those same links in one row above the bottom bar.
- Desktop: the original links remain in their original positions.

- [x] **Step 1: Write failing frontend tests** for mobile grouping, active state, expansion/collapse, outside-click/Escape close, permission-preserving subsets, and unchanged desktop links.
- [x] **Step 2: Run the focused tests** and confirm the warehouse toggle is missing.
- [x] **Step 3: Implement the minimal shared navigation enhancer** using only links already emitted by the server.
- [x] **Step 4: Add responsive styles** for the single bottom-bar slot and upward horizontal menu.
- [x] **Step 5: Run focused tests, the full test suite, Python compilation, and `git diff --check`.**
- [x] **Step 6: Restart the local service and verify the approved flows at 430px mobile width and desktop width.**
