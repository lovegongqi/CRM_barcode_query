# GYJ Inventory Stocktake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a price-free, multi-device GYJ inventory stocktake workflow with live per-product stock reads, a second serial-number reconciliation phase, durable history, discrepancy notes, administrator-only archive/restore, and Excel export.

**Architecture:** Keep GYJ browser work inside the existing per-tool-account `GYJWorker`, add a focused read-only report reader, and put stocktake state in a dedicated SQLite repository. A service layer owns phase transitions, device locks, live GYJ reads, serial classification, 60-second completed-item synchronization, and history creation; Flask routes expose versioned JSON that a new inventory page polls every second.

**Tech Stack:** Python 3, Flask 3, SQLite, Playwright sync API, OpenPyXL, vanilla JavaScript, existing Aurora CSS shell, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-31-gyj-inventory-count-design.md`

## Global Constraints

- The feature is read-only in GYJ: do not click or call GYJ create, save, repair, delete, import, approve, or stock-correction actions.
- Default warehouse selection is empty; every stock quantity is the total across all warehouses.
- The server must discard all price and amount fields before API serialization, persistence, logging, or export.
- Quantity counting for all products must finish before serial-number reconciliation begins.
- Zero-stock products belong to the task but stay hidden unless the user searches for them.
- The first device to open a product owns the edit lock; heartbeat is 20 seconds and expiry is 2 minutes.
- Tool-page state synchronizes about every second; completed-product GYJ stock synchronizes every 60 seconds.
- Any account with `inventory` permission may count, scan, view differences, and add notes; only administrators may archive, restore, or force-unlock.
- Persist data under `CRM_DATA_DIR/config` so NAS container restarts and upgrades retain it.
- Preserve the existing `/api/inbound/gyj/*` login contract while adding shared account-level GYJ login routes for inventory.

---

### Task 1: Quantity normalization and SQLite schema

**Files:**
- Create: `inventory_store.py`
- Create: `tests/test_inventory_store.py`

**Interfaces:**
- Produces: `normalize_quantity(value) -> str`
- Produces: `quantity_difference(actual, book) -> str`
- Produces: `InventoryStore(db_path: str, now: Callable[[], datetime] | None = None)`
- Produces: `InventoryConflict`, `InventoryNotFound`, and `InventoryPermissionDenied`
- Produces: `InventoryStore.initialize() -> None`

- [ ] **Step 1: Write failing quantity and schema tests**

```python
import os
import sqlite3
import tempfile
import unittest

from inventory_store import InventoryStore, normalize_quantity, quantity_difference


class InventoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "inventory.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_quantity_helpers_do_not_use_binary_float_rounding(self):
        self.assertEqual(normalize_quantity("10.00"), "10")
        self.assertEqual(normalize_quantity("0.125"), "0.125")
        self.assertEqual(quantity_difference("9.5", "10"), "-0.5")

    def test_initialize_creates_every_inventory_table(self):
        InventoryStore(self.db_path).initialize()
        with sqlite3.connect(self.db_path) as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({
            "inventory_tasks", "inventory_items", "inventory_item_locks",
            "inventory_serial_expected", "inventory_serial_scans",
            "inventory_stock_movements", "inventory_discrepancies",
            "inventory_notes", "inventory_audit_events",
        }.issubset(names))
```

- [ ] **Step 2: Run the new tests and verify the module is missing**

Run: `python -m unittest tests.test_inventory_store.InventoryStoreTests -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'inventory_store'`.

- [ ] **Step 3: Implement decimal helpers, connection setup, and the complete schema**

```python
from datetime import datetime
from decimal import Decimal, InvalidOperation
import sqlite3


class InventoryConflict(RuntimeError):
    pass


class InventoryNotFound(RuntimeError):
    pass


class InventoryPermissionDenied(RuntimeError):
    pass


def normalize_quantity(value):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("数量格式不正确")
    if not number.is_finite() or number < 0:
        raise ValueError("数量必须是大于或等于 0 的数字")
    return _decimal_text(number)


def _decimal_text(number):
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def quantity_difference(actual, book):
    return _decimal_text(Decimal(normalize_quantity(actual)) - Decimal(normalize_quantity(book)))


class InventoryStore:
    def __init__(self, db_path, now=None):
        self.db_path = db_path
        self.now = now or datetime.now
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
```

Create the nine tables named by the test. Use `(task_id, barcode)` as the item key, `(task_id, barcode)` as the lock key, `(task_id, barcode, serial)` for expected serials, and immutable integer primary keys for discrepancies, notes, movements, and audit events. Store quantities as normalized `TEXT`, not `REAL`.

`initialize()` must be idempotent and create the database parent directory before connecting, so every later public repository method can safely assume the schema exists.

- [ ] **Step 4: Run the focused tests**

Run: `python -m unittest tests.test_inventory_store.InventoryStoreTests -v`

Expected: PASS.

- [ ] **Step 5: Commit the schema**

```bash
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): add durable stocktake schema"
```

---

### Task 2: Task and product-list persistence

**Files:**
- Modify: `inventory_store.py`
- Modify: `tests/test_inventory_store.py`

**Interfaces:**
- Consumes: `InventoryStore`, `normalize_quantity`
- Produces: `InventoryStore.create_task(owner, actor, catalog) -> dict`
- Produces: `InventoryStore.get_active_task(owner) -> dict | None`
- Produces: `InventoryStore.get_task_snapshot(owner, task_id, known_version=None) -> dict`
- Produces: `InventoryStore.list_items(task_id, query="", state="", include_zero=False) -> list[dict]`

`catalog` is a list of dictionaries with exactly these keys: `barcode`, `name`, `spec`, `model`, `category`, `unit`, `has_serial`, and `initial_stock`.

- [ ] **Step 1: Add failing lifecycle and zero-stock visibility tests**

```python
    def catalog(self):
        return [
            {"barcode": "A1", "name": "有库存商品", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "2"},
            {"barcode": "B2", "name": "零库存商品", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": True, "initial_stock": "0"},
        ]

    def test_one_active_task_per_owner_and_restart_persistence(self):
        store = InventoryStore(self.db_path)
        first = store.create_task("admin", "管理员", self.catalog())
        with self.assertRaises(InventoryConflict):
            store.create_task("admin", "管理员", self.catalog())
        reloaded = InventoryStore(self.db_path).get_active_task("admin")
        self.assertEqual(reloaded["task_id"], first["task_id"])

    def test_zero_stock_is_hidden_until_search(self):
        store = InventoryStore(self.db_path)
        task = store.create_task("admin", "管理员", self.catalog())
        self.assertEqual([row["barcode"] for row in store.list_items(task["task_id"])], ["A1"])
        self.assertEqual([row["barcode"] for row in store.list_items(task["task_id"], query="B2")], ["B2"])
```

- [ ] **Step 2: Run the focused tests and verify missing methods**

Run: `python -m unittest tests.test_inventory_store.InventoryStoreTests.test_one_active_task_per_owner_and_restart_persistence tests.test_inventory_store.InventoryStoreTests.test_zero_stock_is_hidden_until_search -v`

Expected: FAIL with `AttributeError` for `create_task`.

- [ ] **Step 3: Implement transactional task creation and versioned snapshots**

Use `BEGIN IMMEDIATE` before checking for an existing `loading`, `counting`, `serial_check`, or `sync_error` task. Generate `task_id` with `uuid.uuid4().hex`, insert every catalog row in the same transaction, set task version to `1`, and add a `task_created` audit event.

Return unchanged snapshots without item payload when `known_version` equals the current version:

```python
if known_version is not None and int(known_version) == int(task["version"]):
    return {"success": True, "unchanged": True, "version": task["version"]}
```

`list_items()` must search barcode and name case-insensitively. A non-empty search overrides zero-stock hiding; an empty search filters `initial_stock = '0'` unless `include_zero=True`.

- [ ] **Step 4: Run the complete store test file**

Run: `python -m unittest tests.test_inventory_store -v`

Expected: PASS.

- [ ] **Step 5: Commit task persistence**

```bash
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): persist stocktake tasks and products"
```

---

### Task 3: Device locks, heartbeats, versions, and audit events

**Files:**
- Modify: `inventory_store.py`
- Modify: `tests/test_inventory_store.py`

**Interfaces:**
- Produces: `InventoryStore.claim_item(task_id, barcode, device_id, actor, phase) -> dict`
- Produces: `InventoryStore.heartbeat_lock(task_id, barcode, device_id) -> dict`
- Produces: `InventoryStore.assert_item_lock(task_id, barcode, device_id, phase) -> None`
- Produces: `InventoryStore.release_expired_locks(task_id) -> int`
- Produces: `InventoryStore.admin_unlock(task_id, barcode, actor, is_admin) -> dict`

- [ ] **Step 1: Add failing first-device-wins and expiry tests**

```python
from datetime import datetime, timedelta

    def test_first_device_wins_and_only_admin_can_force_unlock(self):
        store = InventoryStore(self.db_path, now=lambda: datetime(2026, 9, 1, 10, 0, 0))
        task = store.create_task("admin", "管理员", self.catalog())
        lock = store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        self.assertEqual(lock["device_id"], "device-a")
        with self.assertRaises(InventoryConflict):
            store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")
        with self.assertRaises(InventoryPermissionDenied):
            store.admin_unlock(task["task_id"], "A1", "乙", False)

    def test_lock_expires_after_two_minutes_without_heartbeat(self):
        current = [datetime(2026, 9, 1, 10, 0, 0)]
        store = InventoryStore(self.db_path, now=lambda: current[0])
        task = store.create_task("admin", "管理员", self.catalog())
        store.claim_item(task["task_id"], "A1", "device-a", "甲", "counting")
        current[0] += timedelta(seconds=121)
        self.assertEqual(store.release_expired_locks(task["task_id"]), 1)
        self.assertEqual(store.claim_item(task["task_id"], "A1", "device-b", "乙", "counting")["device_id"], "device-b")
```

- [ ] **Step 2: Run the lock tests and verify they fail**

Run: `python -m unittest tests.test_inventory_store.InventoryStoreTests.test_first_device_wins_and_only_admin_can_force_unlock tests.test_inventory_store.InventoryStoreTests.test_lock_expires_after_two_minutes_without_heartbeat -v`

Expected: FAIL with `AttributeError` for `claim_item`.

- [ ] **Step 3: Implement atomic locking**

Use `BEGIN IMMEDIATE`, delete only locks whose `expires_at <= now`, then insert the lock with `heartbeat_at=now` and `expires_at=now+120 seconds`. Re-claim by the same device refreshes the existing lock. Every claim, heartbeat-created extension, expiry, manual unlock, and conflict writes a named audit event; conflicts do not increment task version, while lock ownership changes do.

- [ ] **Step 4: Run all store tests**

Run: `python -m unittest tests.test_inventory_store -v`

Expected: PASS.

- [ ] **Step 5: Commit lock semantics**

```bash
git add inventory_store.py tests/test_inventory_store.py
git commit -m "feat(inventory): add device locks and audit versions"
```

---

### Task 4: Read-only GYJ inventory report adapter

**Files:**
- Create: `gyj_inventory.py`
- Create: `tests/test_gyj_inventory.py`

**Interfaces:**
- Produces: `GYJInventoryReadError`
- Produces: `parse_stock_rows(headers, rows) -> list[dict]`
- Produces: `parse_material_rows(rows) -> dict[str, dict]`
- Produces: `parse_serial_rows(headers, rows) -> list[dict]`
- Produces: `GYJInventoryReader(page, log=None)`
- Produces: `GYJInventoryReader.load_catalog() -> list[dict]`
- Produces: `GYJInventoryReader.read_total_stock(barcode) -> str`
- Produces: `GYJInventoryReader.read_stock_totals() -> dict[str, str]`
- Produces: `GYJInventoryReader.read_unshipped_serials(barcode) -> list[dict]`
- Produces: `GYJInventoryReader.lookup_serial(serial) -> dict | None`

- [ ] **Step 1: Write failing parser tests using the observed GYJ columns**

```python
import unittest

from gyj_inventory import parse_material_rows, parse_serial_rows, parse_stock_rows


class GYJInventoryParserTests(unittest.TestCase):
    def test_stock_parser_allowlists_fields_and_drops_prices(self):
        headers = ["条码", "名称", "规格", "型号", "类别", "单位", "成本价", "库存", "库存金额"]
        rows = [["996041496", "ERH-X10 售后机箱", "", "", "配件", "个", "100", "2", "200"]]
        result = parse_stock_rows(headers, rows)
        self.assertEqual(result, [{
            "barcode": "996041496", "name": "ERH-X10 售后机箱", "spec": "", "model": "",
            "category": "配件", "unit": "个", "stock": "2",
        }])
        self.assertNotIn("成本价", repr(result))
        self.assertNotIn("200", repr(result))

    def test_material_parser_uses_explicit_serial_badge(self):
        result = parse_material_rows([
            {"barcode": "996041496", "name": "ERH-X10 售后机箱", "serial_badge": True},
            {"barcode": "406005140", "name": "电源24VDC4A", "serial_badge": False},
        ])
        self.assertTrue(result["996041496"]["has_serial"])
        self.assertFalse(result["406005140"]["has_serial"])

    def test_serial_parser_keeps_only_unshipped_rows_without_price(self):
        headers = ["序列号", "条码", "名称", "仓库", "入库单价", "已出库"]
        rows = [
            ["SN-1", "10000398", "雷哲V200", "沈桥仓", "3110", "否"],
            ["SN-2", "10000398", "雷哲V200", "沈桥仓", "3110", "是"],
        ]
        self.assertEqual(parse_serial_rows(headers, rows), [{
            "serial": "SN-1", "barcode": "10000398", "name": "雷哲V200", "warehouse": "沈桥仓", "shipped": False,
        }])
```

- [ ] **Step 2: Run parser tests and verify the new module is missing**

Run: `python -m unittest tests.test_gyj_inventory.GYJInventoryParserTests -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement allowlisted parsers and paginated UI reads**

Use the confirmed pages and controls:

```python
GYJ_STOCK_URL = "https://cloud.gyjerp.com/report/material_stock"
GYJ_MATERIAL_URL = "https://cloud.gyjerp.com/material/material"
GYJ_SERIAL_URL = "https://cloud.gyjerp.com/system/plugins/serialNumberStatistics/serialNumberStatistics.html"
```

For stock, leave warehouse empty, fill `请输入条码、名称、助记码、规格、型号等信息` only for focused reads, click `查 询`, iterate `.ant-pagination-next` until disabled, and parse table rows by normalized header names. For material metadata, detect the visible `序` badge in the name cell. For serials, expand filters, set product or serial, set `已出库` to `否`, leave warehouse empty, and iterate every result page. Ignore `合计` rows and reject a result if pagination count and collected rows disagree.

`load_catalog()` builds its roster from every enabled product in 商品信息, then left-joins stock rows by barcode and assigns `stock="0"` when the stock report has no row. This guarantees that zero-stock products remain searchable. A stock row missing product metadata receives `data_error="无法确认商品序列号设置"` instead of `has_serial=False`.

- [ ] **Step 4: Add fake-page reader tests for pagination and all-warehouse behavior**

Create a small fake page adapter that records visited URLs, filled values, selected labels, and returned pages. Assert that the warehouse control is never assigned a warehouse, two pages are combined, `read_total_stock()` returns a normalized total, and `read_unshipped_serials()` explicitly selects `否`.

Run: `python -m unittest tests.test_gyj_inventory -v`

Expected: PASS.

- [ ] **Step 5: Commit the read-only reader**

```bash
git add gyj_inventory.py tests/test_gyj_inventory.py
git commit -m "feat(inventory): read GYJ stock and serial reports"
```

---

### Task 5: Extend the existing GYJ worker and share login routes

**Files:**
- Modify: `app.py:34-40`
- Modify: `app.py:3860-4013`
- Modify: `app.py:8876-8899`
- Modify: `app.py:10075-10137`
- Modify: `tests/test_inbound_routes.py`
- Create: `tests/test_inventory_worker.py`

**Interfaces:**
- Consumes: `GYJInventoryReader`
- Produces on `GYJSession`: `load_inventory_catalog()`, `read_inventory_stock(barcode)`, `read_inventory_stock_totals()`, `read_inventory_serials(barcode)`, `lookup_inventory_serial(serial)`
- Produces matching methods on `GYJWorker`
- Produces shared routes: `/api/gyj/credentials`, `/api/gyj/login`, `/api/gyj/login/captcha`, `/api/gyj/captcha-preview`, `/api/gyj/login-status`
- Preserves all existing `/api/inbound/gyj/*` login routes and response fields

- [ ] **Step 1: Add failing worker delegation and route-alias tests**

```python
class InventoryWorkerTests(unittest.TestCase):
    def test_worker_delegates_inventory_reads_to_its_single_thread(self):
        worker = object.__new__(app_module.GYJWorker)
        worker._call = mock.Mock(return_value=(True, "2"))
        self.assertEqual(worker.read_inventory_stock("996041496"), (True, "2"))
        worker._call.assert_called_once_with("read_inventory_stock", "996041496")

    def test_shared_login_status_matches_inbound_alias(self):
        with mock.patch.object(app_module.gyj_worker, "get", return_value=FakeLoggedInWorker()):
            shared = self.client.get("/api/gyj/login-status")
            inbound = self.client.get("/api/inbound/gyj/login-status")
        self.assertEqual(shared.get_json(), inbound.get_json())

    def test_shared_gyj_routes_require_tool_login(self):
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get("/api/gyj/login-status").status_code, 401)
```

- [ ] **Step 2: Run focused tests and verify missing worker methods/routes**

Run: `python -m unittest tests.test_inventory_worker tests.test_inbound_routes -v`

Expected: FAIL for `read_inventory_stock` or a 404 on `/api/gyj/login-status`.

- [ ] **Step 3: Add session and worker methods without creating a second browser**

Each session method must hold the existing `GYJSession.lock`, call `check_login_status()`, instantiate `GYJInventoryReader(GYJPlaywrightPage(self.page))`, and return `(False, message)` on `GYJInventoryReadError`. Worker methods only call `_call()` with the exact method name and arguments.

- [ ] **Step 4: Extract shared login handlers and attach both route families**

Use multiple Flask decorators on the same handler so credentials, captcha, and login status remain byte-for-byte compatible:

```python
@app.route("/api/gyj/login-status", methods=["GET"])
@app.route("/api/inbound/gyj/login-status", methods=["GET"])
def api_gyj_login_status():
    worker = gyj_worker.get(gyj_credentials_owner_key())
    ok, message = worker.check_login_status()
    return jsonify({
        "success": bool(ok), "logged_in": bool(ok),
        "waiting_captcha": bool(getattr(worker, "waiting_captcha", False)),
        "message": str(message or ""),
    })
```

Add `if path.startswith("/api/gyj"): return "account-self"` to `required_permission_for_path()` before exposing the aliases, so no shared GYJ route bypasses tool-account authentication.

- [ ] **Step 5: Run worker and inbound regression tests**

Run: `python -m unittest tests.test_inventory_worker tests.test_inbound_routes -v`

Expected: PASS.

- [ ] **Step 6: Commit shared GYJ access**

```bash
git add app.py tests/test_inventory_worker.py tests/test_inbound_routes.py
git commit -m "feat(inventory): share GYJ session for report reads"
```

---

### Task 6: Counting service and 60-second completed-stock synchronization

**Files:**
- Create: `inventory_service.py`
- Create: `tests/test_inventory_service.py`
- Modify: `inventory_store.py`
- Modify: `tests/test_inventory_store.py`

**Interfaces:**
- Produces: `InventoryService(store, worker_provider, now=None)`
- Produces: `InventoryService.create_task(owner, actor) -> dict`
- Produces: `InventoryService.open_count_item(owner, task_id, barcode, device_id, actor) -> dict`
- Produces: `InventoryService.submit_count(owner, task_id, barcode, device_id, actor, actual_qty) -> dict`
- Produces: `InventoryService.sync_completed_items(owner, task_id, force=False) -> dict`
- Produces store methods: `set_open_book_quantity`, `record_count`, `update_completed_stock`, `advance_count_phase_if_ready`

- [ ] **Step 1: Write failing service tests with a fake GYJ worker**

```python
class FakeInventoryWorker:
    def __init__(self):
        self.stock = {"A1": "2", "B2": "0", "C3": "5"}
        self.catalog = [
            {"barcode": "A1", "name": "普通商品", "spec": "", "model": "", "category": "配件", "unit": "个", "has_serial": False, "initial_stock": "2"},
            {"barcode": "B2", "name": "序列商品", "spec": "", "model": "", "category": "整机", "unit": "台", "has_serial": True, "initial_stock": "0"},
        ]

    def load_inventory_catalog(self):
        return True, self.catalog

    def read_inventory_stock(self, barcode):
        return True, self.stock[barcode]

    def read_inventory_stock_totals(self):
        return True, dict(self.stock)

    def test_submit_uses_a_second_live_read_and_routes_variances(self):
        worker = FakeInventoryWorker()
        service = InventoryService(self.store, lambda owner: worker, now=self.now)
        task = service.create_task("admin", "管理员")
        service.open_count_item("admin", task["task_id"], "A1", "device-a", "甲")
        worker.stock["A1"] = "1"
        row = service.submit_count("admin", task["task_id"], "A1", "device-a", "甲", "1")
        self.assertEqual(row["completed_book_qty"], "1")
        self.assertEqual(row["state"], "matched")
```

Also add one assertion that a non-serial mismatch becomes `variance`, a serial mismatch becomes `serial_pending`, and the task does not enter `serial_check` until every item has a submitted quantity.

- [ ] **Step 2: Run service tests and verify the module is missing**

Run: `python -m unittest tests.test_inventory_service -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement counting transitions and exact quantity formulas**

`open_count_item()` claims the lock before calling GYJ and saves the returned book quantity. `submit_count()` asserts the same device lock, re-reads GYJ, then records:

```python
diff_qty = quantity_difference(actual_qty, latest_book_qty)
state = "matched" if diff_qty == "0" else ("serial_pending" if item["has_serial"] else "variance")
```

Store `completed_book_qty`, `completed_actual_qty`, `latest_book_qty`, `expected_current_qty`, and `diff_qty`. Release the item lock after a successful submit.

`sync_completed_items()` returns `skipped=True` when the last successful completed-item sync is less than 60 seconds old unless `force=True`. Otherwise read all totals once and apply:

```python
expected_current = completed_actual + latest_book - completed_book
```

Record one movement row only when `latest_book_qty` changes.

- [ ] **Step 4: Run store and service tests**

Run: `python -m unittest tests.test_inventory_store tests.test_inventory_service -v`

Expected: PASS.

- [ ] **Step 5: Commit counting behavior**

```bash
git add inventory_store.py inventory_service.py tests/test_inventory_store.py tests/test_inventory_service.py
git commit -m "feat(inventory): add live quantity counting workflow"
```

---

### Task 7: Serial-number reconciliation phase

**Files:**
- Modify: `inventory_store.py`
- Modify: `inventory_service.py`
- Modify: `tests/test_inventory_store.py`
- Modify: `tests/test_inventory_service.py`

**Interfaces:**
- Produces: `InventoryService.open_serial_item(owner, task_id, barcode, device_id, actor) -> dict`
- Produces: `InventoryService.scan_serial(owner, task_id, barcode, device_id, actor, serial) -> dict`
- Produces: `InventoryService.delete_serial_scan(owner, task_id, barcode, device_id, actor, serial) -> dict`
- Produces: `InventoryService.finish_serial_item(owner, task_id, barcode, device_id, actor) -> dict`
- Produces: `InventoryService.refresh_serial_item(owner, task_id, barcode, device_id, actor) -> dict`
- Produces store methods: `replace_expected_serials`, `add_serial_scan`, `remove_serial_scan`, `serial_reconciliation`, `complete_serial_item`

- [ ] **Step 1: Add failing serial classification tests**

```python
    def test_serial_phase_classifies_missing_extra_other_and_duplicate(self):
        worker = FakeInventoryWorker()
        worker.serials = {"B2": [
            {"serial": "B-1", "barcode": "B2", "name": "序列商品", "warehouse": "沈桥仓", "shipped": False},
            {"serial": "B-2", "barcode": "B2", "name": "序列商品", "warehouse": "其他仓", "shipped": False},
        ]}
        worker.lookup = {
            "OTHER-1": {"serial": "OTHER-1", "barcode": "C3", "name": "其他商品", "warehouse": "沈桥仓", "shipped": False},
        }
        detail = self.service.open_serial_item("admin", self.task_id, "B2", "device-a", "甲")
        self.service.scan_serial("admin", self.task_id, "B2", "device-a", "甲", "B-1")
        self.assertEqual(self.service.scan_serial("admin", self.task_id, "B2", "device-a", "甲", "OTHER-1")["classification"], "other_product")
        duplicate = self.service.scan_serial("admin", self.task_id, "B2", "device-a", "甲", "B-1")
        self.assertEqual(duplicate["classification"], "duplicate")
        result = self.service.finish_serial_item("admin", self.task_id, "B2", "device-a", "甲")
        self.assertIn("B-2", [row["serial"] for row in result["system_only"]])
```

- [ ] **Step 2: Run the focused serial test and verify missing methods**

Run: `python -m unittest tests.test_inventory_service.InventoryServiceTests.test_serial_phase_classifies_missing_extra_other_and_duplicate -v`

Expected: FAIL with `AttributeError` for `open_serial_item`.

- [ ] **Step 3: Implement expected-set refresh, scanning, and classification**

Opening a serial item requires task state `serial_check`, item state `serial_pending`, and an acquired `serial_check` lock. Refresh GYJ expected serials across all warehouses before showing the scanner.

For each scan:

- if active scan already exists for the same serial, return `duplicate` without inserting a second active row;
- if serial is in the expected set for the current product, classify `matched`;
- otherwise call `lookup_inventory_serial(serial)` once;
- classify `other_product`, `already_shipped`, or `unknown` from that lookup.

While the serial workspace is open, `refresh_serial_item()` refreshes the expected set at most once every 60 seconds and recomputes the visible classifications. Before finishing, force the same refresh again. Compute `system_only` as active expected minus active matched scans, `physical_only` as active unknown/already-shipped scans, and retain `other_product` rows separately. Save the immutable final classification rows and release the lock.

- [ ] **Step 4: Add and run transition guards**

Assert that serial opening fails before all quantity items are complete, finishing fails when GYJ refresh fails, and the task cannot complete while any `serial_pending` item remains.

Run: `python -m unittest tests.test_inventory_store tests.test_inventory_service -v`

Expected: PASS.

- [ ] **Step 5: Commit serial reconciliation**

```bash
git add inventory_store.py inventory_service.py tests/test_inventory_store.py tests/test_inventory_service.py
git commit -m "feat(inventory): reconcile physical serial numbers"
```

---

### Task 8: Completed history, discrepancy notes, archive, and restore

**Files:**
- Modify: `inventory_store.py`
- Modify: `inventory_service.py`
- Modify: `tests/test_inventory_store.py`
- Modify: `tests/test_inventory_service.py`

**Interfaces:**
- Produces: `InventoryService.complete_task(owner, task_id, actor) -> dict`
- Produces: `InventoryStore.list_task_history(owner) -> list[dict]`
- Produces: `InventoryStore.list_discrepancies(owner, state, query="") -> list[dict]`
- Produces: `InventoryStore.add_discrepancy_note(owner, discrepancy_id, actor, note, serial=None) -> dict`
- Produces: `InventoryStore.archive_discrepancy(owner, discrepancy_id, actor, is_admin) -> dict`
- Produces: `InventoryStore.restore_discrepancy(owner, discrepancy_id, actor, is_admin) -> dict`

- [ ] **Step 1: Add failing history and role tests**

```python
    def test_completion_creates_immutable_product_and_serial_differences(self):
        result = self.service.complete_task("admin", self.task_id, "管理员")
        rows = self.store.list_discrepancies("admin", "open")
        self.assertTrue(result["completed"])
        self.assertIn("product_quantity", {row["kind"] for row in rows})
        self.assertIn("system_only_serial", {row["kind"] for row in rows})

    def test_every_inventory_user_can_note_but_only_admin_can_archive(self):
        row = self.store.list_discrepancies("admin", "open")[0]
        note = self.store.add_discrepancy_note("admin", row["id"], "仓管员", "已找到，放错仓位")
        self.assertEqual(note["note"], "已找到，放错仓位")
        with self.assertRaises(InventoryPermissionDenied):
            self.store.archive_discrepancy("admin", row["id"], "仓管员", False)
        archived = self.store.archive_discrepancy("admin", row["id"], "管理员", True)
        self.assertEqual(archived["state"], "archived")
        self.assertEqual(self.store.restore_discrepancy("admin", row["id"], "管理员", True)["state"], "open")
```

- [ ] **Step 2: Run history tests and verify missing APIs**

Run: `python -m unittest tests.test_inventory_service tests.test_inventory_store -v`

Expected: FAIL with `AttributeError` for discrepancy methods.

- [ ] **Step 3: Materialize differences once at task completion**

Use one transaction to verify all quantity items and serial items are complete, set task state `completed`, and insert:

- one `product_quantity` row for every non-zero product difference;
- one row per final `system_only_serial`, `physical_only_serial`, `other_product_serial`, `already_shipped_serial`, or `unknown_serial` result.

Never update these original quantities or classifications when notes change. Notes are append-only rows. Archive/restore updates only discrepancy state and archive metadata, then appends an audit event.

- [ ] **Step 4: Run store and service tests**

Run: `python -m unittest tests.test_inventory_store tests.test_inventory_service -v`

Expected: PASS.

- [ ] **Step 5: Commit history and archive rules**

```bash
git add inventory_store.py inventory_service.py tests/test_inventory_store.py tests/test_inventory_service.py
git commit -m "feat(inventory): preserve and archive stocktake differences"
```

---

### Task 9: Price-free Excel reports

**Files:**
- Create: `inventory_export.py`
- Create: `tests/test_inventory_export.py`

**Interfaces:**
- Produces: `build_inventory_workbook(task, items, discrepancies) -> io.BytesIO`
- Produces: `build_discrepancy_workbook(rows, state_label) -> io.BytesIO`

- [ ] **Step 1: Write failing workbook structure and price-leak tests**

```python
from openpyxl import load_workbook

from inventory_export import build_inventory_workbook


class InventoryExportTests(unittest.TestCase):
    def test_workbook_has_two_sheets_and_no_price_columns(self):
        stream = build_inventory_workbook(self.task, self.items, self.discrepancies)
        workbook = load_workbook(stream, data_only=True)
        self.assertEqual(workbook.sheetnames, ["商品差异汇总", "序列号差异明细"])
        all_values = [
            str(cell.value or "")
            for sheet in workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
        ]
        joined = "|".join(all_values)
        for forbidden in ("成本价", "采购价", "零售价", "销售价", "库存金额", "入库单价"):
            self.assertNotIn(forbidden, joined)
        self.assertIn("已找到，放错仓位", joined)
```

- [ ] **Step 2: Run export tests and verify the module is missing**

Run: `python -m unittest tests.test_inventory_export -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Build the exact sheets from allowlisted fields**

The summary sheet columns are `商品条码`, `商品名称`, `规格`, `型号`, `类别`, `单位`, `序列号管理`, `完成时账面数量`, `实盘数量`, `盘盈/盘亏`, `处理状态`, `商品备注`. The serial sheet columns are `商品条码`, `商品名称`, `序列号`, `差异分类`, `盘点任务`, `扫描账号`, `扫描设备`, `扫描时间`, `处理状态`, `序列号备注`.

Apply the existing workbook style conventions: bold filled header, frozen first row, autofilter, sensible column widths, and text formatting for barcodes and serial numbers.

- [ ] **Step 4: Run export tests**

Run: `python -m unittest tests.test_inventory_export -v`

Expected: PASS.

- [ ] **Step 5: Commit report generation**

```bash
git add inventory_export.py tests/test_inventory_export.py
git commit -m "feat(inventory): export stocktake differences"
```

---

### Task 10: Inventory routes, permissions, navigation, and sync scheduling

**Files:**
- Modify: `app.py:6181-6230`
- Modify: `app.py:8662-8900`
- Modify: `app.py:9875-10320`
- Modify: `templates/accounts.html:168-175`
- Modify: `static/aurora.js:2-10`
- Create: `templates/inventory.html`
- Create: `tests/test_inventory_routes.py`
- Modify: `tests/test_frontend_routes.py`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `InventoryStore`, `InventoryService`, workbook builders
- Produces page: `GET /inventory`
- Produces task APIs under `/api/inventory/tasks`
- Produces discrepancy APIs under `/api/inventory/discrepancies`
- Produces permission name: `inventory`

- [ ] **Step 1: Write failing route and permission tests**

```python
    def test_inventory_permission_controls_page_and_api(self):
        viewer = self.login_account("viewer", ["results"])
        self.assertEqual(viewer.get("/inventory").status_code, 403)
        self.assertEqual(viewer.get("/api/inventory/tasks/active").status_code, 403)

        counter = self.login_account("counter", ["inventory"])
        self.assertEqual(counter.get("/inventory").status_code, 200)
        self.assertEqual(counter.get("/api/inventory/tasks/active").status_code, 200)

    def test_admin_navigation_places_inventory_after_inbound(self):
        page = self.admin.get("/inventory").get_data(as_text=True)
        self.assertLess(page.index('href="/inbound"'), page.index('href="/inventory"'))
        self.assertLess(page.index('href="/inventory"'), page.index('href="/product-library"'))
```

- [ ] **Step 2: Run route tests and verify 404 or missing permission**

Run: `python -m unittest tests.test_inventory_routes tests.test_frontend_routes tests.test_frontend_contract -v`

Expected: FAIL because `/inventory` and `inventory.html` do not exist.

- [ ] **Step 3: Wire repository, service, permission, and shell page**

Add `INVENTORY_DB_FILE = os.path.join(CONFIG_DIR, "inventory_stocktake.sqlite3")`, create one `InventoryStore`, and one `InventoryService` using `gyj_worker.get`. Add `inventory` to the administrator defaults, account-save allowlist, settings checkbox, `PAGE_LINKS` immediately after inbound, `required_permission_for_path`, and Aurora nav metadata.

Create `inventory.html` with the Aurora shell, `data-aurora-page="inventory"`, navigation links, login button, and empty root containers used by the next tasks.

The page route passes `account=current_account_public()` and the template defines `const CURRENT_ACCOUNT = {{ account|tojson }};`; later UI code uses only `CURRENT_ACCOUNT.is_admin` to decide whether to render administrative controls.

- [ ] **Step 4: Add exact JSON and export routes**

Implement:

```text
GET    /api/inventory/tasks/active?version=<int>&query=<text>&state=<text>
POST   /api/inventory/tasks
GET    /api/inventory/tasks/history
GET    /api/inventory/tasks/<task_id>
POST   /api/inventory/tasks/<task_id>/items/<barcode>/claim
POST   /api/inventory/tasks/<task_id>/items/<barcode>/heartbeat
POST   /api/inventory/tasks/<task_id>/items/<barcode>/count
POST   /api/inventory/tasks/<task_id>/items/<barcode>/serial/open
POST   /api/inventory/tasks/<task_id>/items/<barcode>/serial/refresh
POST   /api/inventory/tasks/<task_id>/items/<barcode>/serials
DELETE /api/inventory/tasks/<task_id>/items/<barcode>/serials/<serial>
POST   /api/inventory/tasks/<task_id>/items/<barcode>/serial/finish
POST   /api/inventory/tasks/<task_id>/complete
POST   /api/inventory/tasks/<task_id>/items/<barcode>/unlock
GET    /api/inventory/tasks/<task_id>/export
GET    /api/inventory/discrepancies?state=open|archived&query=<text>
POST   /api/inventory/discrepancies/<id>/notes
POST   /api/inventory/discrepancies/<id>/archive
POST   /api/inventory/discrepancies/<id>/restore
GET    /api/inventory/discrepancies/export?state=open|archived&query=<text>
```

Derive `owner` and `actor` from the Flask session, never from request JSON. Require `is_admin_account()` for unlock, archive, and restore. Convert `InventoryConflict` to HTTP 409, `InventoryNotFound` to 404, `InventoryPermissionDenied` to 403, and invalid quantities to 400.

On active-task polling, call a non-blocking `_ensure_inventory_sync(owner, task_id)` that starts at most one daemon thread per task when the last completed-item sync is at least 60 seconds old. Reopening a stale page triggers the same sync immediately.

- [ ] **Step 5: Run route and existing permission tests**

Run: `python -m unittest tests.test_inventory_routes tests.test_frontend_routes tests.test_frontend_contract -v`

Expected: PASS.

- [ ] **Step 6: Commit server integration**

```bash
git add app.py templates/accounts.html templates/inventory.html static/aurora.js tests/test_inventory_routes.py tests/test_frontend_routes.py tests/test_frontend_contract.py
git commit -m "feat(inventory): expose protected stocktake APIs"
```

---

### Task 11: Current-count page and multi-device interaction

**Files:**
- Create: `static/inventory.css`
- Create: `static/inventory.js`
- Modify: `templates/inventory.html`
- Modify: `app.py:8829-8848`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: active-task, claim, heartbeat, count, and shared GYJ login APIs
- Produces browser functions: `pollInventoryTask`, `renderInventoryItems`, `openCountItem`, `submitCount`, `sendInventoryHeartbeat`, `openGyjLogin`
- Produces stable DOM ids: `inventoryTaskSummary`, `inventorySearch`, `inventoryFilters`, `inventoryItems`, `inventoryCountDialog`, `inventoryGyjLoginDialog`

- [ ] **Step 1: Add failing frontend contract tests**

```python
    def test_inventory_page_has_live_count_contract_and_no_price_copy(self):
        source = self.source("inventory.html")
        script = (STATIC / "inventory.js").read_text(encoding="utf-8")
        for token in (
            'id="inventoryTaskSummary"', 'id="inventorySearch"',
            'id="inventoryItems"', 'id="inventoryCountDialog"',
            "pollInventoryTask", "sendInventoryHeartbeat", "submitCount",
        ):
            self.assertIn(token, source + script)
        for forbidden in ("成本价", "采购价", "零售价", "销售价", "库存金额"):
            self.assertNotIn(forbidden, source + script)
```

- [ ] **Step 2: Run the frontend contract and verify missing assets**

Run: `python -m unittest tests.test_frontend_contract.FrontendContractTest.test_inventory_page_has_live_count_contract_and_no_price_copy -v`

Expected: FAIL because `static/inventory.js` is missing.

- [ ] **Step 3: Build the responsive current-count interface**

Render summary cards for total, completed, pending, matched, surplus, deficit, and serial-pending counts. Render product rows with barcode, name, spec, model, category, unit, explicit serial badge, GYJ stock, actual quantity, difference, state, lock owner, and update time.

The default request uses an empty search and therefore receives no zero-stock rows. Debounce non-empty barcode/name search by 250 ms; searched zero-stock rows render normally. A scanner Enter key in the global search opens the exact matching barcode.

- [ ] **Step 4: Implement version polling, lock heartbeat, and count submission**

Use `localStorage.inventory_device_id`, generated once with `crypto.randomUUID()`. Poll active task every 1000 ms with the last version. While a count dialog is editable, send heartbeat every 20000 ms. If a 409 response reports another lock owner, switch the dialog to read-only and show that owner.

The count dialog always shows current GYJ book quantity and computes the visible difference from the numeric input. Disable submit during the live GYJ re-read and replace the row with the server response after success.

- [ ] **Step 5: Reuse the shared GYJ modal behavior**

Call `/api/gyj/credentials`, `/api/gyj/login`, `/api/gyj/login/captcha`, `/api/gyj/captcha-preview`, and `/api/gyj/login-status`. Keep only one button in the top-right status row; do not add query-channel buttons.

- [ ] **Step 6: Add cache-busting keys and run frontend tests**

Expose `inventory_css_v` and `inventory_js_v` from `_aurora_asset_versions()` and use them in the template.

Run: `python -m unittest tests.test_frontend_contract tests.test_frontend_routes -v`

Expected: PASS.

- [ ] **Step 7: Commit the count UI**

```bash
git add static/inventory.css static/inventory.js templates/inventory.html app.py tests/test_frontend_contract.py
git commit -m "feat(inventory): add live multi-device count page"
```

---

### Task 12: Serial reconciliation, history, differences, and admin archive UI

**Files:**
- Modify: `static/inventory.css`
- Modify: `static/inventory.js`
- Modify: `templates/inventory.html`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_inventory_routes.py`

**Interfaces:**
- Consumes: serial, history, discrepancy, note, archive, restore, and export APIs
- Produces browser functions: `openSerialItem`, `scanSerial`, `finishSerialItem`, `loadInventoryHistory`, `loadDiscrepancies`, `saveDiscrepancyNote`, `archiveDiscrepancy`, `restoreDiscrepancy`
- Produces DOM ids: `inventorySerialWorkspace`, `inventorySerialInput`, `inventoryHistory`, `inventoryDifferencesOpen`, `inventoryDifferencesArchived`

- [ ] **Step 1: Add failing contract and administrator-rendering tests**

```python
    def test_inventory_serial_and_history_contract(self):
        source = self.source("inventory.html") + (STATIC / "inventory.js").read_text(encoding="utf-8")
        for token in (
            'id="inventorySerialWorkspace"', 'id="inventorySerialInput"',
            'id="inventoryHistory"', 'id="inventoryDifferencesOpen"',
            'id="inventoryDifferencesArchived"', "finishSerialItem",
            "saveDiscrepancyNote", "archiveDiscrepancy", "restoreDiscrepancy",
        ):
            self.assertIn(token, source)

    def test_non_admin_archive_and_restore_apis_are_forbidden(self):
        response = self.counter.post(f"/api/inventory/discrepancies/{self.discrepancy_id}/archive")
        self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Run focused tests and verify missing controls**

Run: `python -m unittest tests.test_frontend_contract tests.test_inventory_routes -v`

Expected: FAIL because serial/history DOM and functions are absent.

- [ ] **Step 3: Build the serial workspace**

When task state becomes `serial_check`, show one queue of `serial_pending` products. Opening a product acquires its lock and auto-focuses `inventorySerialInput`. After every Enter scan, clear and refocus the input, update matched/system-only/physical-only/other-product/duplicate counters, and keep detailed lists visible. While the workspace remains open, call the serial refresh route every 60 seconds. Removing a mistaken scan calls the DELETE route and appends an audit event server-side.

- [ ] **Step 4: Build history and discrepancy workspaces**

Add tabs `当前盘点`, `历史任务`, and `历史差异`. History rows show task number, start/completion time, participant count, product totals, quantity differences, serial differences, and Excel download.

Historical differences have `待处理` and `已归档` sub-tabs, barcode/name/serial search, product-level notes, serial-level notes, actor/time history, and filtered Excel download. Render archive and restore buttons only when `CURRENT_ACCOUNT.is_admin` is true; keep server enforcement regardless of rendering.

- [ ] **Step 5: Run all route and frontend tests**

Run: `python -m unittest tests.test_inventory_routes tests.test_frontend_contract tests.test_frontend_routes -v`

Expected: PASS.

- [ ] **Step 6: Commit serial and history UI**

```bash
git add static/inventory.css static/inventory.js templates/inventory.html tests/test_frontend_contract.py tests/test_inventory_routes.py
git commit -m "feat(inventory): add serial and discrepancy history UI"
```

---

### Task 13: Full verification and operator documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: every interface in Tasks 1-12
- Produces: documented local/NAS data location and operator workflow

- [ ] **Step 1: Document the delivered workflow and persistent data file**

Add a concise README section covering navigation order, shared GYJ login, two-phase count flow, zero-stock search, 60-second completed-stock synchronization, device-lock expiry, historical notes, administrator-only archive/restore, Excel reports, and `CRM_DATA_DIR/config/inventory_stocktake.sqlite3`.

- [ ] **Step 2: Run syntax and focused inventory tests**

Run: `python -m py_compile app.py inventory_store.py inventory_service.py inventory_export.py gyj_inventory.py`

Expected: exit code 0.

Run: `python -m unittest tests.test_inventory_store tests.test_gyj_inventory tests.test_inventory_worker tests.test_inventory_service tests.test_inventory_export tests.test_inventory_routes -v`

Expected: PASS.

- [ ] **Step 3: Run the complete regression suite**

Run: `python -m unittest discover -s tests -v`

Expected: PASS with no failures or errors.

- [ ] **Step 4: Start the local app and verify the browser flow against read-only GYJ pages**

Use the existing local launcher, sign in as administrator, and verify:

1. `盘点` appears immediately after `入库`.
2. GYJ login status is shared with inbound.
3. Starting a task reads the all-warehouse catalog without showing prices.
4. A positive-stock product can be counted and a searched zero-stock product can be opened.
5. A second browser/device sees the first device lock and live progress.
6. A serial mismatch enters the second phase only after every quantity is complete.
7. Serial scanning classifies matched, missing, extra, other-product, and duplicate values.
8. Completed history survives an app restart.
9. A normal inventory user can add a note but cannot archive; an administrator can archive and restore.
10. Both Excel exports contain the expected two sheets and no price labels or values.

Do not click any GYJ write action during verification.

- [ ] **Step 5: Review the final diff for scope and secrets**

Run: `git diff --check`

Expected: no output.

Run: `git status --short`

Expected: only the intended inventory files and README are modified before the final commit.

Run: `rg -n "password|captcha|成本价|采购价|零售价|销售价|库存金额|入库单价" inventory_store.py inventory_service.py inventory_export.py static/inventory.js templates/inventory.html`

Expected: credential matches are limited to the approved shared GYJ login modal and are never present in API output/log rendering; price matches are absent from persistence, service, export, and page rendering.

- [ ] **Step 6: Commit documentation and verified final adjustments**

```bash
git add README.md app.py inventory_store.py inventory_service.py inventory_export.py gyj_inventory.py static/inventory.css static/inventory.js templates/inventory.html tests
git commit -m "docs: document GYJ inventory stocktake"
```
