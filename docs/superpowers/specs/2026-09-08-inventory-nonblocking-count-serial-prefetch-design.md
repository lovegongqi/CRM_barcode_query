# Non-blocking Inventory Count and Serial Prefetch Design

## Goal

Let staff record physical quantities and serials immediately while GYJ book data refreshes in the background.

## Approved behavior

- Opening a quantity dialog never waits for GYJ; the cached book quantity is shown and editing is enabled immediately.
- Adding, updating, or deleting a count entry persists against the cached book quantity and never performs a synchronous GYJ request.
- When a serialized item first becomes `serial_pending`, the server starts one deduplicated background expected-serial prefetch for that task/product.
- Opening serial reconciliation returns cached reconciliation immediately. Scanning and camera controls are enabled immediately, while a forced refresh starts in the background.
- Slow expected-serial reads occur outside the per-product serial mutation lock. Scans made during a refresh are stored immediately as `unknown` when necessary and reclassified atomically when refreshed expected serials are committed.
- Refresh failure preserves all physical scans, shows a retry message, and leaves the existing “重新获取” control enabled.
- Automatic prefetch/refresh events remain excluded from the user-facing modification log.

## Architecture

Use the existing cached quantities stored on `inventory_items` for count mutations. Reuse the existing 60-second task stock synchronization for later book-quantity correction.

Add an app-level, process-local deduplicated serial-prefetch registry keyed by `(owner, task_id, barcode)`. Its daemon worker calls a service refresh method as actor `system` and removes itself from the registry on completion. A failed prefetch is not sticky: opening the dialog always starts a new forced refresh, and the manual retry uses the same route.

Split serial refresh into three phases: validate and snapshot briefly, read GYJ without `_serial_guard`, then reacquire `_serial_guard` to revalidate item state and atomically replace expected serials. This keeps scan writes responsive during the external request.

The serial open route becomes cache-only. The browser renders the cached payload, enables input/camera, and then starts a forced refresh without placing it in the serial mutation queue, so scan requests can proceed concurrently.

## Failure and concurrency rules

- If the item leaves `serial_pending` while GYJ is being read, the commit is rejected and no stale expected data is written.
- Concurrent background/open/manual refresh requests are serialized per product without holding the scan mutation lock, so results commit in request order while scans remain responsive; server prefetch itself is also deduplicated.
- Closing or switching the dialog invalidates the request generation so late responses cannot overwrite another product.
- Count-save success is never turned into failure by a later task poll or background refresh.

## Verification

- Service concurrency test proves a scan returns before a blocked GYJ serial read is released and is reclassified after refresh completes.
- Route test proves serialized count saves start one deduplicated background prefetch.
- Frontend tests prove count and serial inputs are usable before background requests resolve, refresh failure preserves editability, and retry remains available.
- Full Python/Node-vm/Playwright test suite remains green.
