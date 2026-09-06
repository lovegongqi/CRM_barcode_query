# Inventory Carton Entry and Compact Mobile List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add persistent carton presets, atomic whole-carton serial entry and correction, category filtering, collapsible serial groups, and compact expandable mobile product cards.

**Architecture:** Extend the SQLite inventory model with global product presets and task-scoped carton groups; retain scans in \`inventory_serial_scans\` through a nullable carton link. Validate and commit a whole carton in one \`BEGIN IMMEDIATE\` transaction using only cached expected serials. Extend the existing vanilla-JavaScript dialog, poller, and ZXing controller without adding a framework or GYJ calls during carton operations.

**Tech Stack:** Python 3, Flask, SQLite, vanilla JavaScript/CSS, Python \`unittest\`, Node \`vm\`, existing local \`@zxing/browser\` 0.2.1

**Spec:** \`docs/superpowers/specs/2026-09-06-inventory-carton-entry-mobile-list-design.md\`

## Global Constraints

- Any inventory user may save a shared carton quantity from 1 through 999; every successful change is audited.
- Preset changes affect only new cartons. Edited previews may contain a different actual count, which must be shown before confirmation.
- Generation increments only the trailing digit run and preserves its prefix and leading zeroes.
- Whole-carton writes are atomic and reject duplicate carton codes or active task serials.
- Carton operations use the successful local expected-serial cache and never call GYJ.
- Existing ungrouped scans remain valid.
- Every serial group is collapsed by default and retains its expanded state while the dialog remains open.
- Active scans show newest first; normal matched rows omit duplicate product copy; delete is inline with the serial.
- Category filtering is client-side and does not change task summary figures.
- Mobile product cards are compact until explicitly expanded; desktop cards remain complete.
- Ordinary camera scanning stays continuous; carton fields use one-shot capture and release the camera after one decode.

---

### Task 1: Persist Carton Presets and Carton Groups

**Files:**
- Modify: \`inventory_store.py:20-405\`
- Test: \`tests/test_inventory_store.py\`

**Interfaces:**
- Produces tables \`inventory_carton_presets\` and \`inventory_cartons\`.
- Adds nullable \`inventory_serial_scans.carton_id\` and task-wide active serial uniqueness.
- Produces \`get_carton_preset(owner, task_id, barcode) -> dict | None\`.
- Produces \`save_carton_preset(owner, task_id, barcode, device_id, actor, carton_quantity) -> dict\`.

- [ ] **Step 1: Write failing schema and preset tests**

\`\`\`python
def test_carton_preset_is_shared_editable_and_audited(self):
    store = InventoryStore(self.db_path)
    task = store.create_task("admin", "管理员", self.catalog())
    store.advance_count_phase_if_ready("admin", task["task_id"])
    store.save_carton_preset("admin", task["task_id"], "B2", "d1", "甲", 20)
    result = store.save_carton_preset(
        "admin", task["task_id"], "B2", "d2", "乙", 12
    )
    self.assertEqual(result["carton_quantity"], 12)
    self.assertEqual(
        store.get_carton_preset("admin", task["task_id"], "B2")["updated_by"],
        "乙",
    )
\`\`\`

- [ ] **Step 2: Verify RED**

Run: \`python3 -m unittest tests.test_inventory_store.InventoryStoreTests.test_carton_preset_is_shared_editable_and_audited -v\`

Expected: FAIL because the methods and tables do not exist.

- [ ] **Step 3: Implement the minimal schema and methods**

Create \`inventory_carton_presets(barcode, carton_quantity CHECK 1..999, created_by, created_at, updated_by, updated_at)\` and \`inventory_cartons(carton_id, task_id, barcode, carton_code, preset_quantity, confirmed_quantity, start_serial, created_by, created_device_id, created_at, active, deleted_by, deleted_device_id, deleted_at)\`. Use the existing \`PRAGMA table_info\` migration pattern for \`carton_id\`, add \`idx_inventory_serial_scans_task_active ON (task_id, serial) WHERE active=1\`, upsert the preset, bump task version, and audit \`carton_preset_changed\` with before/after quantities.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_store -v
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): persist carton presets"
\`\`\`

---

### Task 2: Add Atomic Carton Mutations and Grouped Reconciliation

**Files:**
- Modify: \`inventory_store.py:1426-1810,2240-2335\`
- Test: \`tests/test_inventory_store.py\`

**Interfaces:**
- Produces \`create_serial_carton(owner, task_id, barcode, device_id, actor, carton_code, preset_quantity, start_serial, serials) -> dict\`.
- Produces \`add_carton_serial(...)\`, \`remove_carton_serial(...)\`, and \`delete_serial_carton(...) -> dict\`.
- Extends reconciliation with \`carton_preset\`, \`cartons\`, and \`ungrouped\`.

- [ ] **Step 1: Write failing transaction tests**

\`\`\`python
def test_duplicate_serial_rolls_back_the_entire_carton(self):
    store, task = self.serial_ready_store()
    store.replace_expected_serials(
        "admin", task["task_id"], "B2", "d1", "甲", [{
            "serial": "S001", "barcode": "B2", "name": "零库存商品",
            "warehouse": "沈桥仓", "shipped": False,
        }],
    )
    store.add_serial_scan(
        "admin", task["task_id"], "B2", "d1", "甲", "S001", "matched"
    )
    with self.assertRaisesRegex(InventoryConflict, "S001"):
        store.create_serial_carton(
            "admin", task["task_id"], "B2", "d2", "乙",
            "BOX-1", 2, "S001", ["S001", "S002"],
        )
    with store.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM inventory_cartons WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()[0]
    self.assertEqual(count, 0)
\`\`\`

Also test cached matched/unknown classification, newest-first carton scans, legacy \`ungrouped\`, individual add/delete by another device, whole deletion, retained carton-code uniqueness, and audit rows.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_inventory_store.InventoryStoreTests.test_duplicate_serial_rolls_back_the_entire_carton tests.test_inventory_store.InventoryStoreTests.test_carton_mutations_group_and_order_active_scans -v`.

Expected: FAIL because carton mutation methods and grouped reconciliation are absent.

- [ ] **Step 3: Implement one-transaction writes**

Within \`BEGIN IMMEDIATE\`, call \`_serial_mutation_context\`, require \`serial_synced_at\`, reject task-wide active duplicates, insert the carton and linked scans, classify with active \`inventory_serial_expected\`, audit once, and commit. Any exception rolls back all rows. Correction methods verify active carton ownership; whole deletion deactivates the carton and every linked active scan in one transaction.

- [ ] **Step 4: Extend reconciliation and audit allowlists**

Return active cartons with nested scans ordered \`scan_id DESC\`, ungrouped scans newest-first, and preserve existing classification arrays for compatibility. Add labels for preset change, carton create, carton serial add/remove, and carton delete. Return only \`carton_id\`, \`carton_code\`, preset before/after, confirmed quantity, affected count, and serial list from audit details.

- [ ] **Step 5: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_store -v
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): store atomic serial cartons"
\`\`\`

---

### Task 3: Validate Carton Operations in the Service

**Files:**
- Modify: \`inventory_service.py:275-410\`
- Test: \`tests/test_inventory_service.py\`

**Interfaces:**
- Produces \`_carton_quantity(value) -> int\` and \`_carton_text(value, label) -> str\`.
- Produces service methods matching Task 2 plus \`save_carton_preset(...)\`.

- [ ] **Step 1: Write failing no-GYJ and validation tests**

\`\`\`python
def test_carton_create_uses_cache_without_worker_calls(self):
    task = self.create_serial_task()
    self.worker.serials["B2"] = [
        {"serial": serial, "barcode": "B2", "name": "序列商品",
         "warehouse": "沈桥仓", "shipped": False}
        for serial in ("S001", "S002")
    ]
    self.service.open_serial_item("admin", task["task_id"], "B2", "d1", "甲")
    reads = list(self.worker.serial_reads)
    result = self.service.create_serial_carton(
        "admin", task["task_id"], "B2", "d2", "乙",
        "BOX-1", 2, "S001", ["S001", "S002"],
    )
    self.assertEqual(result["counts"]["matched"], 2)
    self.assertEqual(self.worker.serial_reads, reads)
    self.assertEqual(self.worker.lookup_reads, [])
\`\`\`

Add cases for \`0\`, \`1000\`, \`1.5\`, \`True\`, empty/control/over-512 text, empty or over-999 arrays, and duplicate serials.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_inventory_service.InventoryServiceTests.test_carton_create_uses_cache_without_worker_calls tests.test_inventory_service.InventoryServiceTests.test_carton_validation_rejects_invalid_payloads -v`.

Expected: FAIL because carton service methods are absent.

- [ ] **Step 3: Implement validation and delegation**

Use \`_serial_guard\` and \`_serial_item\`; reject invalid values before store calls; normalize every serial; cap the array at 999; never call \`_call_worker\`, \`refresh_serial_item\`, or \`scan_serial\`.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_service -v
git add inventory_service.py tests/test_inventory_service.py
git commit -m "feat(inventory): validate carton serial entry"
\`\`\`

---

### Task 4: Expose Carton APIs

**Files:**
- Modify: \`app.py:10756-11015\`
- Test: \`tests/test_inventory_routes.py\`

**Interfaces:**
- GET/POST \`/api/inventory/tasks/<task_id>/items/<barcode>/carton-preset\`.
- POST \`/api/inventory/tasks/<task_id>/items/<barcode>/cartons\`.
- POST \`/api/inventory/tasks/<task_id>/items/<barcode>/cartons/<int:carton_id>/serials\`.
- DELETE \`/api/inventory/tasks/<task_id>/items/<barcode>/cartons/<int:carton_id>/serials/<serial>\`.
- DELETE \`/api/inventory/tasks/<task_id>/items/<barcode>/cartons/<int:carton_id>\`.

- [ ] **Step 1: Write failing route tests**

Assert encoded slash handling, authenticated actor/owner, ordinary inventory-user access, task-version responses, malformed IDs/JSON, wrong methods, 409 conflicts, and sanitized errors. Carton create accepts exactly \`{device_id, carton_code, preset_quantity, start_serial, serials}\`.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_inventory_routes.InventoryRouteTest.test_carton_routes_use_authenticated_identity tests.test_inventory_routes.InventoryRouteTest.test_carton_routes_validate_methods_and_errors -v`.

Expected: FAIL because the carton routes return 404.

- [ ] **Step 3: Implement routes with existing helpers**

Use \`_inventory_identity\`, \`_inventory_json_body\`, \`_inventory_path_value\`, \`_inventory_device_id\`, and \`_inventory_mutation_response\`. Never accept actor, owner, or permissions from JSON.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_routes -v
git add app.py tests/test_inventory_routes.py
git commit -m "feat(inventory): expose carton entry APIs"
\`\`\`

---

### Task 5: Add Product Category Filtering

**Files:**
- Modify: \`app.py:10756-10782\`
- Modify: \`templates/inventory.html:54-76\`
- Modify: \`static/inventory.js:20-540,2109-2175\`
- Modify: \`static/inventory.css:145-180,760-820\`
- Test: \`tests/test_inventory_routes.py\`
- Test: \`tests/test_inventory_frontend.py\`
- Test: \`tests/test_frontend_contract.py\`

**Interfaces:**
- Produces task field \`categories: string[]\` from the unfiltered item set.
- Produces DOM select \`inventoryCategoryFilter\`.
- Produces \`renderInventoryCategories(categories)\` and \`inventoryCategoryMatches(item)\`.

- [ ] **Step 1: Write failing backend/frontend tests**

\`\`\`javascript
category.value = '滤芯';
state.value = 'variance';
const visible = inventoryVisibleItems([
    {category: '滤芯', diff_qty: '-1'},
    {category: '整机', diff_qty: '-1'},
    {category: '滤芯', diff_qty: '0'},
]);
assert.equal(visible.length, 1);
\`\`\`

Assert the API returns sorted, unique, non-empty categories before server query/state filtering replaces \`items\`.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_inventory_routes.InventoryRouteTest.test_active_task_returns_unfiltered_categories tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_category_filter_combines_with_state tests.test_frontend_contract.FrontendContractTest.test_inventory_has_category_filter -v`.

Expected: FAIL because the task field and category control are absent.

- [ ] **Step 3: Implement client-only composition**

Preserve the selected category if still available, otherwise reset it. Category changes rerender product and serial queues without requesting the server or changing \`lastInventoryVersion\`. Keep summary values from the server. Use three desktop toolbar columns and one mobile column.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_routes tests.test_inventory_frontend tests.test_frontend_contract -v
git add app.py templates/inventory.html static/inventory.js static/inventory.css \
  tests/test_inventory_routes.py tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): filter products by category"
\`\`\`

---

### Task 6: Make Mobile Product Cards Compact and Expandable

**Files:**
- Modify: \`static/inventory.js:26-45,334-434\`
- Modify: \`static/inventory.css:181-305,775-825\`
- Test: \`tests/test_inventory_frontend.py\`
- Test: \`tests/test_frontend_contract.py\`

**Interfaces:**
- Produces \`inventoryExpandedBarcodes: Set<string>\`.
- Produces \`toggleInventoryItemDetails(barcode)\` and \`.inventory-item-toggle\`.

- [ ] **Step 1: Write failing expansion-state tests**

Render two products, expand one, rerender after a simulated poll, and assert only that card stays expanded. Filter it out and assert its key is removed. Assert a serial-pending card keeps “核对序列号” as its compact primary action.

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_product_card_expansion_survives_poll_render tests.test_frontend_contract.FrontendContractTest.test_inventory_mobile_product_cards_are_collapsible -v`.

Expected: FAIL because expansion state and controls are absent.

- [ ] **Step 3: Implement markup and responsive CSS**

Add an explicit \`aria-expanded\` button and mark full-only fields/actions. At 720px or less keep product/status, three compact metrics, and one primary action visible; hide details, footer, and secondary actions until \`.is-expanded\`. Target about 100px compact height. Hide the toggle on desktop and leave all details visible.

- [ ] **Step 4: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_frontend tests.test_frontend_contract -v
git add static/inventory.js static/inventory.css tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): compact mobile product cards"
\`\`\`

---

### Task 7: Build Carton Entry, Collapsible Serial Groups, and One-Shot Camera

**Files:**
- Modify: \`templates/inventory.html:164-196\`
- Modify: \`static/inventory.js:26-45,974-1485,2109-2175\`
- Modify: \`static/inventory.css:305-620,775-835\`
- Test: \`tests/test_inventory_frontend.py\`
- Test: \`tests/test_frontend_contract.py\`

**Interfaces:**
- Produces \`generateCartonSerials(startSerial, quantity) -> string[]\`.
- Produces state \`cartonPreview\`, \`expandedSerialGroups\`, and camera modes \`continuous|carton-code|carton-start\`.
- Produces \`renderCartonEntry()\`, \`renderCartonPreview()\`, \`submitSerialCarton()\`, and \`toggleSerialGroup(key)\`.

- [ ] **Step 1: Write failing generation and preview tests**

\`\`\`javascript
assert.deepEqual(Array.from(generateCartonSerials('REG202609160521', 3)), [
    'REG202609160521', 'REG202609160522', 'REG202609160523',
]);
assert.deepEqual(Array.from(generateCartonSerials('SN0099', 3)), [
    'SN0099', 'SN0100', 'SN0101',
]);
assert.throws(() => generateCartonSerials('NO-SUFFIX', 20), /末尾数字/);
\`\`\`

Prove scan capture only fills an editable field; preview edits/adds/deletes form the submitted array; one confirmation makes one atomic request; failure retains preview.

- [ ] **Step 2: Write failing grouped-list tests**

Assert all classification/carton/ungrouped sections start collapsed, one click expands one group, rerender preserves state, and close clears state. Assert newest scan is first, matched rows omit product metadata, exception rows retain necessary reason, and delete is inline right of the serial.

- [ ] **Step 3: Verify RED**

Run: `python3 -m unittest tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_carton_generation_preserves_suffix_width tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_carton_preview_submits_edited_serials_once tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_serial_groups_collapse_and_keep_latest_scan_first tests.test_frontend_contract.FrontendContractTest.test_inventory_serial_dialog_has_carton_controls -v`.

Expected: FAIL because carton controls, generation, and collapsible group rendering are absent.

- [ ] **Step 4: Add carton form and editable preview**

Add preset, carton-code, and start-serial inputs; two one-shot camera buttons; generate; editable preview rows; add/delete row controls; \`箱规 N，实际录入 M\`; and atomic confirm. Disable only during submission.

- [ ] **Step 5: Render saved cartons and all serial groups collapsed**

Use stable keys such as \`carton:17\`, \`ungrouped\`, and \`classification:system_only\`. Carton headers show carton code, active count, range, actor/time, expand, and confirmed whole-delete. Expanded groups support single correction. Replace the old permanently open grids.

- [ ] **Step 6: Add one-shot camera routing**

\`\`\`javascript
if (inventoryCameraMode === 'carton-code' || inventoryCameraMode === 'carton-start') {
    const targetId = inventoryCameraMode === 'carton-code'
        ? 'inventoryCartonCode' : 'inventoryCartonStartSerial';
    inventoryElement(targetId).value = serial;
    stopInventoryCamera(false);
    inventoryElement(targetId).focus();
    return;
}
submitDecodedSerial(serial);
\`\`\`

Reuse current HTTPS and rear-camera checks. One-shot capture fills only the selected field, stops controls and tracks, and never generates or saves. Ordinary scanning stays continuous.

- [ ] **Step 7: Verify and commit**

\`\`\`bash
python3 -m unittest tests.test_inventory_frontend tests.test_frontend_contract -v
git add templates/inventory.html static/inventory.js static/inventory.css \
  tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): add carton serial workspace"
\`\`\`

---

### Task 8: Audit Integration and Full Verification

**Files:**
- Modify: \`static/inventory.js:1490-1568\`
- Test: \`tests/test_inventory_store.py\`
- Test: \`tests/test_inventory_frontend.py\`
- Test: \`tests/test_frontend_contract.py\`

**Interfaces:**
- Consumes the allowlisted carton audit fields from Task 2.

- [ ] **Step 1: Write failing audit-render tests**

Render preset change, carton create, single add/delete, and carton delete. Assert titles name the carton and show affected count or serial, without exposing raw details JSON.

- [ ] **Step 2: Implement compact audit copy**

Render \`整箱录入 · BOX-1\`, \`箱内补录 · BOX-1\`, and \`删除整箱 · BOX-1\`, followed by the affected count or serial. Preserve existing count and ordinary serial audit rendering.

- [ ] **Step 3: Run focused inventory verification**

\`\`\`bash
python3 -m unittest \
  tests.test_inventory_store tests.test_inventory_service tests.test_inventory_routes \
  tests.test_inventory_frontend tests.test_frontend_contract -v
\`\`\`

Expected: PASS.

- [ ] **Step 4: Run complete verification**

\`\`\`bash
python3 -m unittest discover -s tests
python3 -m py_compile app.py inventory_store.py inventory_service.py
node --check static/inventory.js
git diff --check
\`\`\`

Expected: every command exits 0.

- [ ] **Step 5: Preflight live data and restart locally**

\`\`\`bash
sqlite3 'file:config/inventory_stocktake.sqlite3?immutable=1' \
  "SELECT task_id, serial, COUNT(*) FROM inventory_serial_scans WHERE active=1 GROUP BY task_id, serial HAVING COUNT(*)>1;"
launchctl kickstart -k gui/$(id -u)/com.crmbarcodequery.local
lsof -nP -iTCP:5002 -sTCP:LISTEN
curl -fsS -I http://127.0.0.1:5002/inventory
\`\`\`

Expected: duplicate query returns no rows, one Python process listens on port 5002, and the inventory page returns a normal response or login redirect.

- [ ] **Step 6: Commit final audit integration**

\`\`\`bash
git add static/inventory.js tests/test_inventory_store.py \
  tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): show carton audit history"
git status --short
\`\`\`

Expected: clean working tree.
