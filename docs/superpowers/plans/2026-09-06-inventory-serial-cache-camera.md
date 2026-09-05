# Inventory Serial Cache and Mobile Camera Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make serial reconciliation cache-first and independent from GYJ during scanning, reject transient GYJ table states, add HTTPS mobile continuous camera scanning, and identify every count audit event by a stable entry number.

**Architecture:** Keep GYJ as an explicitly refreshed source: the service reads it only when no successful serial cache exists or when the user clicks refresh. The browser always renders the persisted reconciliation, while a small camera controller feeds decoded values into the existing serialized scan queue. Audit numbering is derived server-side from immutable count-entry IDs and returned as an allowlisted display field.

**Tech Stack:** Python 3, Flask, SQLite, Playwright, vanilla JavaScript/CSS, Node `vm` frontend tests, `@zxing/browser` 0.2.1 UMD bundle (MIT)

**Spec:** `docs/superpowers/specs/2026-09-05-inventory-serial-cache-camera-design.md`

## Global Constraints

- Do not access GYJ when a serial cache already exists unless the user explicitly clicks `重新获取`.
- Do not access GYJ while saving scans, deleting scans, or completing a product.
- A transient empty or stale GYJ table must never replace a valid cache.
- Camera controls require HTTPS or localhost and must not disable keyboard or scanner input when unavailable.
- Use the rear camera and decode both one-dimensional and two-dimensional formats continuously.
- Stop every camera track when the dialog closes, the product changes, the page hides, or the user stops scanning.
- Keep count-entry numbers stable after deletion and omit a number for legacy audit rows without an entry ID.
- Do not expose GYJ prices or raw internal error details.

---

### Task 1: Wait for a Stable GYJ Serial Report

**Files:**
- Modify: `gyj_inventory.py:225-310,506-520`
- Test: `tests/test_gyj_inventory.py`

**Interfaces:**
- Consumes: `GYJInventoryReader._read_table_page(report: str) -> dict`
- Produces: `GYJInventoryReader._wait_for_stable_query_result(report: str, expected_field: str, expected_value: str) -> dict`
- Produces: `GYJInventoryReader._collect_pages(report: str, first_snapshot: dict | None = None) -> tuple[list, list, list]`

- [ ] **Step 1: Write failing tests for transient empty and stale rows**

Add a fake page whose serial snapshots are an old barcode, a loading empty table, an unloaded empty table, and then two identical valid snapshots. Assert the final row is returned. Add a timeout case that never settles.

```python
def test_serial_query_waits_past_transient_empty_and_stale_rows(self):
    page = SettlingSerialPage([
        serial_page([["OLD", "OLD", "旧商品", "旧仓", "0", "否"]], 1),
        {**serial_page([], 0), "loading": True},
        serial_page([], 0),
        serial_page([["S1", "10000213", "雷哲", "沈桥仓", "0", "否"]], 1),
        serial_page([["S1", "10000213", "雷哲", "沈桥仓", "0", "否"]], 1),
    ])
    result = GYJInventoryReader(page).read_unshipped_serials("10000213")
    self.assertEqual([row["serial"] for row in result], ["S1"])
    self.assertGreaterEqual(len(page.waits), 4)

def test_serial_query_rejects_a_result_that_never_settles(self):
    with self.assertRaisesRegex(GYJInventoryReadError, "查询结果未稳定"):
        GYJInventoryReader(NeverSettlingSerialPage()).read_unshipped_serials("10000213")
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python3 -m unittest \
  tests.test_gyj_inventory.GYJInventoryReaderTests.test_serial_query_waits_past_transient_empty_and_stale_rows \
  tests.test_gyj_inventory.GYJInventoryReaderTests.test_serial_query_rejects_a_result_that_never_settles -v
```

Expected: FAIL because the reader immediately accepts the first snapshot.

- [ ] **Step 3: Implement condition-based settling**

Use a 15-second maximum, 100 ms samples, and two consecutive identical valid snapshots. A non-empty snapshot must contain the requested column and every result row must equal the requested value. An empty snapshot is valid only when non-loading with a parseable total of zero.

```python
def _wait_for_stable_query_result(self, report, expected_field, expected_value):
    waiter = self.page if hasattr(self.page, "wait_for_timeout") else self.browser_page
    previous_marker = None
    stable_samples = 0
    for attempt in range(151):
        snapshot = self._read_table_page(report)
        if self._query_snapshot_matches(snapshot, expected_field, expected_value):
            marker = self._page_marker(snapshot)
            stable_samples = stable_samples + 1 if marker == previous_marker else 1
            previous_marker = marker
            if stable_samples >= 2:
                return snapshot
        else:
            previous_marker = None
            stable_samples = 0
        if attempt < 150:
            waiter.wait_for_timeout(100)
    raise GYJInventoryReadError("GYJ 序列号查询结果未稳定")
```

Pass the stable snapshot into `_collect_pages`; retain existing pagination checks.

- [ ] **Step 4: Run reader tests**

```bash
python3 -m unittest tests.test_gyj_inventory -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gyj_inventory.py tests/test_gyj_inventory.py
git commit -m "fix(inventory): wait for stable serial reports"
```

---

### Task 2: Make Serial Reconciliation Cache-First

**Files:**
- Modify: `inventory_service.py:330-450`
- Test: `tests/test_inventory_service.py`

**Interfaces:**
- Consumes: `InventoryStore.serial_reconciliation(owner, task_id, barcode) -> dict`
- Produces: `open_serial_item(...) -> dict` that reads GYJ only without `serial_synced_at`
- Produces: `refresh_serial_item(..., force: bool) -> dict`, with `force=True` as the only explicit refresh
- Produces: `scan_serial(...) -> dict` that classifies only against cached expected serials
- Produces: `finish_serial_item(...) -> dict` that completes from cache

- [ ] **Step 1: Write cache-first failing tests**

Prove first open reads once, later opens and non-forced refreshes use cache even after two hours, force reads again, unmatched scans save as `unknown` without GYJ lookup, and finish does not read GYJ.

```python
def test_serial_open_reuses_successful_cache_until_manual_force(self):
    task = self.create_serial_task()
    self.worker.serials["B2"] = [expected("B-1")]
    self.service.open_serial_item("admin", task["task_id"], "B2", "d1", "甲")
    self.current[0] += timedelta(hours=2)
    result = self.service.open_serial_item("admin", task["task_id"], "B2", "d2", "乙")
    self.assertEqual(self.worker.serial_reads, ["B2"])
    self.assertTrue(result["skipped"])

def test_unmatched_scan_is_saved_without_gyj_lookup(self):
    task = self.create_serial_task()
    self.worker.serials["B2"] = []
    self.service.open_serial_item("admin", task["task_id"], "B2", "d1", "甲")
    result = self.service.scan_serial("admin", task["task_id"], "B2", "d1", "甲", "NEW")
    self.assertEqual(result["classification"], "unknown")
    self.assertEqual(self.worker.lookup_reads, [])
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest \
  tests.test_inventory_service.InventoryServiceTests.test_serial_open_reuses_successful_cache_until_manual_force \
  tests.test_inventory_service.InventoryServiceTests.test_unmatched_scan_is_saved_without_gyj_lookup -v
```

Expected: FAIL because open forces GYJ and unmatched scans call the lookup worker.

- [ ] **Step 3: Implement minimal cache semantics**

```python
if not force and last_sync_at:
    current["skipped"] = True
    return current

classification = "matched" if serial in expected else "unknown"
return self.store.add_serial_scan(
    owner, task_id, barcode, device_id, actor, serial, classification
)
```

Call the refresh helper with `force=False` from open and finish. Preserve duplicate handling and audits.

- [ ] **Step 4: Run all service tests**

```bash
python3 -m unittest tests.test_inventory_service -v
```

Expected: PASS after replacing former TTL and lookup expectations with the approved contract.

- [ ] **Step 5: Commit**

```bash
git add inventory_service.py tests/test_inventory_service.py
git commit -m "fix(inventory): use cached serial reconciliation"
```

---

### Task 3: Add Manual Refresh and Remove Automatic GYJ Refreshes

**Files:**
- Modify: `templates/inventory.html:164-196`
- Modify: `static/inventory.js:20-45,970-1340,1970-2040`
- Modify: `static/inventory.css:430-560`
- Test: `tests/test_inventory_frontend.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: POST `/api/inventory/tasks/<task>/items/<barcode>/serial/refresh` with `{device_id, force: true}`
- Produces DOM IDs: `inventorySerialRefresh`, `inventorySerialSyncedAt`
- Produces: `manualRefreshSerialItem() -> Promise<void>`

- [ ] **Step 1: Write failing DOM and behavior tests**

Assert the controls exist, no serial refresh interval is started, visibility changes do not refresh GYJ, and manual refresh sends one forced request.

```javascript
await vm.runInContext('manualRefreshSerialItem()', context);
assert.equal(requests.at(-1).url, '/api/inventory/tasks/T1/items/B2/serial/refresh');
assert.deepEqual(JSON.parse(requests.at(-1).options.body), {
    device_id: 'device-a', force: true,
});
assert.match(element('inventorySerialSyncedAt').textContent, /最近获取/);
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest \
  tests.test_inventory_frontend.InventoryFrontendTests.test_manual_serial_refresh_is_the_only_forced_refresh \
  tests.test_frontend_contract.FrontendContractTests.test_inventory_serial_dialog_has_manual_refresh_controls -v
```

Expected: FAIL because the controls and function do not exist.

- [ ] **Step 3: Implement manual refresh**

```javascript
async function manualRefreshSerialItem() {
    const button = inventoryElement('inventorySerialRefresh');
    button.disabled = true;
    setSerialMessage('正在重新获取 GYJ 账面序列号…');
    try {
        await refreshSerialItem(true);
        setSerialMessage('账面序列号已重新获取。', 'success');
    } catch (error) {
        setSerialMessage(`重新获取失败：${error.message}。已保留上次数据。`, 'error');
    } finally {
        button.disabled = false;
        inventoryElement('inventorySerialInput').focus();
    }
}
```

Remove `serialRefreshTimer`, all timer calls, visibility-triggered refresh, and “强制刷新” completion copy. Keep the scan result rendering through a non-forced cache response.

- [ ] **Step 4: Run frontend tests**

```bash
python3 -m unittest tests.test_inventory_frontend tests.test_frontend_contract -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/inventory.html static/inventory.js static/inventory.css \
  tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): add manual serial refresh"
```

---

### Task 4: Show Stable Count-Entry Numbers in Audit History

**Files:**
- Modify: `inventory_store.py:2253-2325`
- Modify: `static/inventory.js:1360-1395`
- Test: `tests/test_inventory_store.py`
- Test: `tests/test_inventory_frontend.py`

**Interfaces:**
- Produces audit field: `entry_number: int | None`
- Consumes frontend fields: `event_type`, `event_label`, `entry_number`

- [ ] **Step 1: Write failing store and rendering tests**

Create two entries, update/delete the first, and update the second. Assert stable event numbers. Render an update with number 2 and assert the visible title.

```python
self.assertEqual([row["entry_number"] for row in rows], [1, 2, 1, 1, 2])
```

```javascript
renderInventoryAudit([{
    event_type: 'count_entry_updated', event_label: '修改分次数量',
    entry_number: 2, actor: '乙', created_at: '2026-09-06T10:00:00',
    before_quantity: '12', after_quantity: '13',
}]);
assert.equal(auditRoot.children[0].children[0].textContent, '修改第 2 笔数量');
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest \
  tests.test_inventory_store.InventoryStoreTests.test_audit_view_numbers_count_entries_stably \
  tests.test_inventory_frontend.InventoryFrontendTests.test_audit_renders_count_entry_number -v
```

Expected: FAIL because `entry_number` is absent.

- [ ] **Step 3: Derive and render stable numbers**

Parse `entry_id` from count-entry details. Maintain one insertion-ordered mapping per barcode while iterating rows by `event_id`. Assign a number on first valid appearance and reuse it. Map the three frontend titles to `新增/修改/删除第 N 笔数量`; use the old label for legacy rows.

- [ ] **Step 4: Run store and frontend tests**

```bash
python3 -m unittest tests.test_inventory_store tests.test_inventory_frontend -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add inventory_store.py static/inventory.js \
  tests/test_inventory_store.py tests/test_inventory_frontend.py
git commit -m "feat(inventory): identify count audit entries"
```

---

### Task 5: Add HTTPS Mobile Continuous Camera Scanning

**Files:**
- Create: `static/vendor/zxing-browser-0.2.1.min.js`
- Create: `static/vendor/zxing-browser-LICENSE.txt`
- Modify: `templates/inventory.html:5-12,164-196`
- Modify: `static/inventory.js:20-45,970-1360,1970-2045`
- Modify: `static/inventory.css:430-560`
- Test: `tests/test_inventory_frontend.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `ZXingBrowser.BrowserMultiFormatReader`
- Produces DOM IDs: `inventoryCameraStart`, `inventoryCameraStop`, `inventoryCameraPanel`, `inventoryCameraVideo`, `inventoryCameraMessage`
- Produces: `startInventoryCamera() -> Promise<void>`, `stopInventoryCamera() -> void`, `submitDecodedSerial(serial: string) -> Promise<void>`

- [ ] **Step 1: Vendor pinned ZXing and its license**

Extract `package/umd/zxing-browser.min.js` and `package/LICENSE` from `@zxing/browser@0.2.1`. Do not use `@latest` or a runtime CDN. Load it before `inventory.js`.

```html
<script defer src="/static/vendor/zxing-browser-0.2.1.min.js"></script>
<script defer src="/static/inventory.js{{ inventory_js_v }}"></script>
```

- [ ] **Step 2: Write failing camera lifecycle tests**

Assert local assets and DOM. With a fake decoder prove an insecure non-local origin reports `需要 HTTPS`, start requests a rear camera, two values submit in order, the same value within 1500 ms is ignored, and close/hidden stops controls and every media track.

```javascript
await vm.runInContext('startInventoryCamera()', context);
decoderCallback(fakeResult('SN-1'), null);
decoderCallback(fakeResult('SN-1'), null);
now += 1600;
decoderCallback(fakeResult('SN-2'), null);
await vm.runInContext('serialOperationQueue', context);
assert.deepEqual(submitted, ['SN-1', 'SN-2']);
vm.runInContext('closeSerialWorkspace()', context);
assert.equal(stopCount, 1);
```

- [ ] **Step 3: Run focused tests and verify RED**

```bash
python3 -m unittest \
  tests.test_inventory_frontend.InventoryFrontendTests.test_camera_continuously_submits_and_stops \
  tests.test_inventory_frontend.InventoryFrontendTests.test_camera_requires_secure_context \
  tests.test_frontend_contract.FrontendContractTests.test_inventory_camera_uses_local_pinned_decoder -v
```

Expected: FAIL because the controller and DOM do not exist.

- [ ] **Step 4: Implement the controller**

```javascript
const controls = await reader.decodeFromConstraints(
    {video: {facingMode: {ideal: 'environment'}}},
    inventoryElement('inventoryCameraVideo'),
    (result) => {
        if (!result) return;
        const serial = String(result.getText ? result.getText() : result.text || '').trim();
        if (serial) submitDecodedSerial(serial);
    },
);
inventoryCameraControls = controls;
```

Refactor Enter input to call `submitDecodedSerial`. Suppress only the same value in the preceding 1500 ms. Stop decoder controls and every `video.srcObject` track before clearing it. Use `window.isSecureContext` plus localhost/loopback detection for the HTTPS message.

- [ ] **Step 5: Add responsive styles**

Use `aspect-ratio: 4 / 3`, `object-fit: cover`, full mobile width, and keep controls above the long result lists.

- [ ] **Step 6: Run frontend tests**

```bash
python3 -m unittest tests.test_inventory_frontend tests.test_frontend_contract -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add static/vendor templates/inventory.html static/inventory.js static/inventory.css \
  tests/test_inventory_frontend.py tests/test_frontend_contract.py
git commit -m "feat(inventory): add mobile continuous scanning"
```

---

### Task 6: Full Verification and Local Restart

**Files:**
- Verify only; modify only files whose failure traces directly to Tasks 1-5

**Interfaces:**
- Consumes all interfaces produced by Tasks 1-5
- Produces a clean committed tree and a running local service on `127.0.0.1:5002`

- [ ] **Step 1: Run the complete test suite**

```bash
python3 -m unittest discover -s tests
```

Expected: exit 0 with all tests passing.

- [ ] **Step 2: Run syntax and diff checks**

```bash
python3 -m py_compile app.py gyj_inventory.py inventory_service.py inventory_store.py
git diff --check
git status --short
```

Expected: compile and diff checks exit 0; only intentional changes remain before final commit.

- [ ] **Step 3: Restart and inspect the service**

```bash
launchctl kickstart -k gui/$(id -u)/com.crmbarcodequery.local
lsof -nP -iTCP:5002 -sTCP:LISTEN
```

Expected: one Python process listens on `127.0.0.1:5002`.

- [ ] **Step 4: Smoke-test page and vendor asset**

```bash
curl -fsS -I http://127.0.0.1:5002/inventory
curl -fsS -I http://127.0.0.1:5002/static/vendor/zxing-browser-0.2.1.min.js
```

Expected: normal page response or login redirect, and HTTP 200 for the decoder.

- [ ] **Step 5: Verify commits and clean tree**

```bash
git log --oneline dfe8e25..HEAD
git status --short
```

Expected: focused commits for Tasks 1-5 and no uncommitted files.

- [ ] **Step 6: State the deployment prerequisite**

The final handoff must say that loopback camera testing is allowed, but phones need an HTTPS NAS reverse proxy. Do not claim that NAS HTTPS is configured unless separately verified.
