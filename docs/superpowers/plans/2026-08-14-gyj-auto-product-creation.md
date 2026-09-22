# GYJ Missing Product Auto-Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a CRM packing-slip product code cannot be selected in GYJ purchase inbound, create the missing GYJ product through the visible UI, verify it can then be selected, and continue the same inbound order; abort before plain save after two failed create-or-lookup attempts.

**Architecture:** Keep policy in `GYJPurchaseInboundWriter`, where the complete CRM line determines whether the product needs serial-number management. Keep visible-page actions in `GYJPlaywrightPage`, so the writer can be tested with `FakeGYJPage` without browser state. Reuse the existing job log callback to report each new product and retry, and preserve the current plain-save-only guard.

**Tech Stack:** Python 3, unittest, Playwright page adapter, Flask background job.

## Global Constraints

- Existing GYJ products must be selected directly; do not create a duplicate.
- New product fields: code = CRM `product_code`; name = CRM `description`; unit = `个`; serial setting = `有` if the CRM line has serials, otherwise `无`.
- Interact only with visible GYJ UI controls. Do not call undocumented GYJ write APIs or save/audit any record other than the intended plain-save purchase inbound order.
- Retry create/lookup at most twice after the initial missing lookup. If both retries fail, raise `GYJInboundError`; the writer must not call plain save.
- Preserve the current serial-first line ordering and existing quantity/serial entry behavior.

---

## Task 1: Add writer policy and fake-page contracts

**Files:**
- Modify: `tests/test_gyj_inbound.py`
- Modify: `gyj_inbound.py`

- [ ] Add focused writer tests before implementation.
  - Extend `FakeGYJPage` with a configurable sequence of product-selection outcomes and a `create_product(product_code, description, has_serials)` recorder.
  - Add `test_writer_creates_missing_product_then_retries_selection` asserting: first selection raises the existing missing-product error; `create_product("926019528", "滤芯", True)` is called once; selection runs again; normal line completion and plain save occur.
  - Add `test_writer_marks_unbarcoded_accessory_as_no_serial_product` asserting the same creation receives `False` for a no-serial line.
  - Add `test_writer_retries_missing_product_creation_twice_then_stops_before_save` asserting two create attempts, a raised error containing the code, and no `保存` click.
  - Run:
    ```bash
    python3 -m unittest \
      tests.test_gyj_inbound.GYJInboundWriterTest.test_writer_creates_missing_product_then_retries_selection \
      tests.test_gyj_inbound.GYJInboundWriterTest.test_writer_marks_unbarcoded_accessory_as_no_serial_product \
      tests.test_gyj_inbound.GYJInboundWriterTest.test_writer_retries_missing_product_creation_twice_then_stops_before_save -v
    ```
    Expected before code: failures because the writer does not create missing products.

- [ ] Implement the smallest writer-level recovery loop.
  - Extract the existing single line entry into a method that first calls the page’s normal `add_product_line(line)`.
  - On the established missing-product `GYJInboundError`, call `page.create_product(...)`, log its successful creation, and retry the normal line entry. Limit to two create-and-retry attempts.
  - Treat all other errors as terminal; never mask serial/quantity failures as product creation failures.
  - Continue to invoke `progress` only after the full line is entered successfully.

- [ ] Re-run the focused tests; commit this behavior with:
  ```bash
  git add gyj_inbound.py tests/test_gyj_inbound.py
  git commit -m "feat: create missing GYJ inbound products"
  ```

## Task 2: Implement the visible GYJ new-product UI adapter

**Files:**
- Modify: `tests/test_gyj_inbound.py`
- Modify: `gyj_inbound.py`

- [ ] Add adapter contract tests for `GYJPlaywrightPage.create_product` using small fake locators.
  - Assert it opens the visible product chooser’s exact `新增` button only after an empty search result.
  - Assert it fills `input#name`, `input#unit`, and the visible line barcode input selected by `[id^="barCode_jet-"]`.
  - Assert it selects `有` when `has_serials=True` and `无` otherwise through the serial-number control rooted at `#enableSerialNumber`.
  - Assert it uses the ordinary visible `保存` control and not an audit/save-and-review control, then waits for the new-product page to close before retrying product search.
  - Run the targeted adapter test class. Expected before code: missing method/fake contract failure.

- [ ] Implement `GYJPlaywrightPage.create_product(product_code, description, has_serials)`.
  - Locate only visible controls; use stable selectors from the observed GYJ page (`#name`, `#unit`, `[id^="barCode_jet-"]`, `#enableSerialNumber`).
  - Fill `description` as name, `个` as unit, and `product_code` as barcode. Reject an empty description or code with a concise `GYJInboundError`.
  - Select serial option `有`/`无`, click the plain visible `保存`, and verify the new-product editor is closed before returning.
  - On any create failure, raise a code-specific `GYJInboundError` so Task 1 performs the second attempt and emits a useful final error.

- [ ] Re-run the focused adapter and writer tests, then commit the UI adapter change separately:
  ```bash
  git add gyj_inbound.py tests/test_gyj_inbound.py
  git commit -m "feat: automate GYJ missing product form"
  ```

## Task 3: Stream auto-create activity to the inbound page

**Files:**
- Modify: `tests/test_gyj_inbound.py`
- Modify: `tests/test_inbound_gyj_api.py` (or the existing GYJ job API test module if that is the repository’s current location)
- Modify: `app.py` only if the existing writer callback is insufficient

- [ ] Add a job-level regression test that runs a fake worker through `_run_inbound_gyj_job` and asserts the status payload includes the auto-created product log before the corresponding `正在录入`/completed-line update.
  - Assert a second create attempt is logged as retry rather than a false success.
  - Assert terminal retry exhaustion returns a failed job and does not report `GYJ 采购入库单已保存`.

- [ ] Use the existing `log` callback from `_run_inbound_gyj_job`; do not add a new API or expose credentials.
  - Success message includes product code, name, and `序列号：有/无`.
  - Retry message identifies the attempt number and product code.
  - Keep completed-product streaming unchanged: only a successfully entered line appears.

- [ ] Run targeted API/job tests and the full regression suite:
  ```bash
  python3 -m unittest tests.test_gyj_inbound -v
  python3 -m unittest discover -s tests -v
  git diff --check
  ```
  Commit the logging integration once all pass.

## Task 4: Controlled live verification and handoff

**Files:**
- No source changes expected.

- [ ] Start/restart the local app only after the test suite is green.
- [ ] Use the existing visible, authenticated GYJ browser only to exercise a product the user specifically chooses. Do not create a test product or purchase order as part of automated verification unless the user explicitly asks to do so at that time.
- [ ] Verify the live operation reports either direct matching or the exact auto-create/retry log, and that it does not audit the purchase inbound document.
- [ ] Report the commits, test evidence, and any GYJ UI selector mismatch clearly.
