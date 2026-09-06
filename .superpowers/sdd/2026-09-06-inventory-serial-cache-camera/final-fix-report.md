# Final Review Fix Report — Inventory Serial Cache and Mobile Camera

Date: 2026-09-06
Branch: `codex/inventory-serial-cache-camera`
Review addressed: `final-review.md`
Implementation commit: `2ab1501 fix(inventory): resolve final serial and camera review`

## Outcome

All four High findings and all four requested surgical Minor/test-coverage findings are fixed. The affected reader, store, service, frontend, and contract modules pass 280 tests; the complete suite passes 499 tests. Python compilation, JavaScript syntax validation, and `git diff --check` also pass.

The fixes preserve the intended invariants:

- Only an explicit manual forced refresh reaches GYJ after a successful serial cache exists.
- A successfully cached zero-row result remains a valid reusable cache.
- Count-entry IDs, per-barcode audit numbering, and prior audit events are retained.
- Query settling still requires consecutive identical accepted snapshots and retains the prior pagination and collection checks.
- Reopening a completed reconciliation retires only completion-generated active `system_only` rows; genuine scans and historical rows remain.

## Finding-by-finding mapping

1. **Count-entry add/update/delete invalidated a successful cache.**

   - Code: `inventory_store.py:1075-1122` no longer clears `serial_synced_at` while recalculating an item from count entries. All count-entry mutations and the existing reopen path therefore preserve the last successful cache marker.
   - Regression: `tests/test_inventory_service.py:350` (`test_count_entry_mutations_preserve_successful_serial_cache`) caches `B-1`, changes the worker result, performs add, add/update, and delete mutations, then proves non-forced open/refresh calls are skipped, the timestamp and cached expected row remain unchanged, and the worker is called exactly once.
   - Contract preservation: manual force behavior remains covered by `test_serial_open_reuses_successful_cache_until_manual_force`; cached finish/no-cache behavior remains covered by the existing service suite.

2. **Reopening prior completion could turn a synthetic serial into a false match or duplicate.**

   - Code: `inventory_store.py:1100-1107` detects a transition away from `serial_complete` during count recalculation and deactivates only active rows whose `source_classification` is `system_only`. Rows are not deleted, genuine scans are not altered, and audit records are untouched.
   - Regression: `tests/test_inventory_service.py:405` (`test_reopened_serial_completion_retires_synthetic_rows_before_rescan`) covers initial scan and finish, count-driven reopen, cached non-forced open, a real scan of the formerly synthetic serial, and a second finish.
   - The test verifies the real scan is `matched` rather than `duplicate`; the second finish contains two genuine matches and no synthetic row; GYJ is called only once; the database retains the old inactive synthetic row alongside both active genuine scans; and both completion audit events remain.

3. **Stable non-empty query results did not require a parseable pagination total.**

   - Code: `gyj_inventory.py:326-341` parses the total before the empty/non-empty branch. A missing/blank total returns `None`, and an unparseable total raises internally; both now reject the sample as transient. The existing row/header/filter checks and page-collection total/count checks remain in place.
   - Regression: `tests/test_gyj_inventory.py:534` (`test_serial_query_waits_for_a_present_parseable_nonempty_total`) table-drives missing, blank, and unparseable totals, followed by two valid stable snapshots, and proves none of the invalid non-empty snapshots can settle.
   - Existing settling, changing-metadata, delayed-pagination, timeout, and stable-zero tests all remain green in the full reader module.

4. **A stale ZXing `controls.stop()` could clear the newer preview's shared `video.srcObject`.**

   - Code: `static/inventory.js:40` tracks the in-flight start promise; `static/inventory.js:1021-1092` chains each start behind the preceding attempt. A newer decoder cannot attach its stream until stale cleanup has completed, while the existing generation checks still prevent stale ownership or status writes.
   - Regression: `tests/test_inventory_frontend.py:1789` (`test_stale_camera_start_cannot_clear_newer_session`) uses a ZXing-faithful stale control whose `stop()` clears the shared video element. It requests a second start while the first is unresolved, releases the stale first control, and proves the second stream remains attached, its track is not stopped, and only the second controls own the active session.

5. **A superseded manual refresh could write into a newer dialog/request.**

   - Code: `static/inventory.js:1307-1328` captures the originating `serialWorkspaceRequestId` and guards success, failure, button, and focus writes with request/session/editable/finish state checks. A stale `null` result is not announced as success.
   - Regression: `tests/test_inventory_frontend.py:1541` (`test_superseded_manual_refresh_does_not_update_reopened_dialog_ui`) closes an in-flight forced refresh, opens a different product, resolves the old request first, and proves the new dialog's loading message, disabled controls, and focus state are unchanged until its own open finishes.

6. **Serial delete/finish rendered raw backend error strings.**

   - Code: `static/inventory.js:1425-1431` uses the fixed delete copy `删除扫描记录失败，请重试。`; `static/inventory.js:1475-1480` uses the fixed finish copy `未能完成核对，请稍后重试。当前扫描界面已保留。`.
   - Regressions: `tests/test_inventory_frontend.py:2205` and `tests/test_inventory_frontend.py:2514` inject `SENTINEL_PRIVATE_GYJ_ERROR`, assert it is absent from the DOM, assert the action-specific safe copy, and retain the existing retry/blocked-finish behavior.

7. **Explicit camera Stop left an active-state message visible.**

   - Code: `static/inventory.js:993-1019` gives `stopInventoryCamera` a `showStatus` argument and reports `相机已停止。` for the explicit action. Internal pre-start, failure, dialog, visibility, and unload cleanup calls use `stopInventoryCamera(false)` (`static/inventory.js:1022`, `1075`, `1223`, `1238`, `2174`, `2180`) so they do not overwrite the next relevant state.
   - Regression: `tests/test_inventory_frontend.py:1739` (`test_explicit_camera_stop_reports_stopped_and_restart_reports_active`) proves start → explicit stop → restart reports active, stopped, then active truthfully. The continuous-camera test still verifies hidden/close cleanup.

8. **Debounce coverage omitted two branches.**

   - Production condition remains the intended conjunction at `static/inventory.js:1052-1060`: suppress only when the new value equals the immediately previous value **and** elapsed time is below 1500 ms.
   - Regression extension: `tests/test_inventory_frontend.py:1631` now proves same value within 1500 ms is suppressed, a different value within 1500 ms is accepted, and the same value after 1500 ms is accepted.
   - Because production behavior was already correct, the added assertions passed initially. A temporary local mutation from `&&` to `||` made the focused test fail with only `['SN-2']`; restoring `&&` returned it to green. The mutation was not committed.

## RED/GREEN evidence

### Cache preservation and reopened synthetic rows

RED command:

```text
python3 -m unittest \
  tests.test_inventory_service.InventoryServiceTests.test_count_entry_mutations_preserve_successful_serial_cache \
  tests.test_inventory_service.InventoryServiceTests.test_reopened_serial_completion_retires_synthetic_rows_before_rescan -v
```

Initial output: `Ran 2 tests` with 2 failures; both observed the cache timestamp become `None` instead of the saved timestamp. After the minimal marker-preservation change, the first test passed and the second failed because the later real scan was `duplicate` rather than `matched`, independently exposing the active synthetic-row defect.

GREEN output after both fixes:

```text
Ran 2 tests

OK
```

Broader store/service GREEN:

```text
python3 -m unittest tests.test_inventory_store tests.test_inventory_service -v
Ran 92 tests

OK
```

### Required pagination total

RED command:

```text
python3 -m unittest \
  tests.test_gyj_inventory.GYJInventoryReaderTests.test_serial_query_waits_for_a_present_parseable_nonempty_total -v
```

RED output: missing and blank totals failed because the reader settled after fewer than the required waits; the unparseable case errored immediately with `GYJInventoryReadError: 分页总数无法解析` instead of treating the snapshot as transient.

GREEN output:

```text
Ran 1 test

OK
```

Broader reader GREEN:

```text
python3 -m unittest tests.test_gyj_inventory -v
Ran 30 tests

OK
```

### Frontend races, safe copy, and explicit stopped state

RED command:

```text
python3 -m unittest \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_superseded_manual_refresh_does_not_update_reopened_dialog_ui \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_stale_camera_start_cannot_clear_newer_session \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_explicit_camera_stop_reports_stopped_and_restart_reports_active \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_finish_uses_backend_forced_finish_once_and_keeps_workspace_on_failure \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_failed_accepted_delete_blocks_finish_until_delete_is_retried \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_camera_continuously_submits_and_stops -v
```

RED output: `Ran 6 tests` with 5 failures. The stale manual refresh overwrote the newer loading message; stale camera cleanup left `srcObject` null; explicit stop left the active message; and finish/delete rendered the injected sentinel. The extended debounce assertions passed because that behavior was already correct.

GREEN output after the frontend fixes:

```text
Ran 6 tests

OK
```

Debounce mutation check:

```text
# Temporary local mutation: && -> || in the duplicate-suppression condition
python3 -m unittest \
  tests.test_inventory_frontend.InventoryFrontendBehaviorTests.test_camera_continuously_submits_and_stops -v
```

Mutation RED: submitted `['SN-2']` instead of `['SN-1', 'SN-2', 'SN-2']`. After restoring `&&`, the focused test returned `Ran 1 test ... OK`.

## Final verification

Affected modules:

```text
python3 -m unittest \
  tests.test_gyj_inventory \
  tests.test_inventory_store \
  tests.test_inventory_service \
  tests.test_inventory_frontend \
  tests.test_frontend_contract

Ran 280 tests in 3.758s

OK
```

Complete suite:

```text
python3 -m unittest discover -s tests

Ran 499 tests in 7.522s

OK
```

The full run emitted six existing `ResourceWarning: unclosed database` messages from `unittest.mock`; the 494-test baseline emitted the same warnings before this fix wave. They do not represent new failures.

Static checks:

```text
python3 -m py_compile app.py gyj_inventory.py inventory_service.py inventory_store.py
node --check static/inventory.js
git diff --check
```

All three commands exited 0 with no error output.

## Files changed

- `inventory_store.py`
- `gyj_inventory.py`
- `static/inventory.js`
- `tests/test_inventory_service.py`
- `tests/test_gyj_inventory.py`
- `tests/test_inventory_frontend.py`
- `.superpowers/sdd/2026-09-06-inventory-serial-cache-camera/final-fix-report.md` (this report)

No template, CSS, API route, schema, or vendored decoder file was changed.

## Commits

- `2ab1501 fix(inventory): resolve final serial and camera review` — all production and regression-test changes.
- This report is added by the immediately following documentation commit.

## Self-review

- Reviewed the complete implementation diff against all eight findings and the authoritative design. Each production change is limited to the reported lifecycle/validation boundary.
- Confirmed the count recalculation no longer touches `serial_synced_at`; explicit force remains the only post-cache worker path.
- Confirmed synthetic-row retirement is conditioned on prior `serial_complete` state and `source_classification = 'system_only'`, updates only `active`, and preserves genuine scans and audit history.
- Confirmed total parsing happens before both empty and non-empty acceptance without weakening consecutive-snapshot equality or page collection.
- Confirmed camera serialization retains generation cancellation and prevents the second shared preview from existing during stale ZXing cleanup.
- Confirmed internal camera resets suppress the stopped message while the explicit Stop control reports it.
- Confirmed error assertions test the actual DOM copy and reject a raw sentinel.
- Confirmed no temporary debounce mutation remains and `git diff --check` is clean.

## Remaining concerns / deployment checks

There are no unresolved code or test blockers in this fix wave.

The following previously documented deployment validation remains intentionally deferred until the branch is merged locally because the launch agent runs the main checkout:

- Restart the launch agent, verify the listener, load the inventory page, and verify the local ZXing asset over the deployed route.
- Exercise one physical phone session through the NAS HTTPS reverse proxy: start the rear camera, scan different values, stop/close/hide, and confirm the camera indicator extinguishes. Loopback HTTP remains only a desktop secure-context exception.

The six pre-existing SQLite `ResourceWarning` messages remain a test-harness hygiene concern, not a regression introduced here.
