# GYJ Five-channel Pool Design

## Goal

Allow inventory reads, serial refreshes, and purchase-inbound jobs to use five independent GYJ browser sessions for the same tool account without two tasks operating the same page at once.

## Approved behavior

- Each tool account has five independent GYJ channels. Channel 1 preserves the existing GYJ browser profile; channels 2–5 use new persistent profiles.
- GYJ login state is maintained per channel. A channel is available for business work only when that channel is logged in and not leased by another task.
- Inventory and purchase-inbound callers do not select a channel. The server automatically leases an idle logged-in channel, runs one complete GYJ operation on it, and releases it in `finally`.
- If all logged-in channels are busy, the operation waits for a released channel instead of sharing a page or failing immediately.
- A login failure or expired session affects only that channel. Other logged-in channels remain available.
- The GYJ login dialog lists all five channels and their state: not started, waiting for captcha, logged in, or busy. Login, captcha preview, captcha refresh, and captcha submission target one explicit channel.
- Because each GYJ browser session has its own captcha, captchas are entered per channel; one captcha is never broadcast to the other four channels.
- Existing remembered GYJ credentials remain scoped to the tool account and can be reused when starting login on another channel. Passwords are never returned by an API.

## Architecture

Replace the current one-worker-per-owner `GYJWorkerPool` mapping with an owner pool containing five lazily created `GYJWorker` instances. Stable slot IDs are `gyj-1` through `gyj-5`. Channel 1 continues using the existing `gyj_session/<owner>` directory so a valid deployed login survives the upgrade. Additional channels use sibling directories derived from the same safe owner plus their slot ID.

The pool owns a condition variable, a reservation count for every slot, and a round-robin cursor per owner. A business lease selects only a worker whose cached login state is valid and whose reservation count is zero. Selection and reservation happen under the same condition lock, preventing two request threads from receiving the same channel. Release always decrements the reservation and notifies waiters.

Expose two pool access paths:

1. Explicit channel access for login management. The login APIs validate `slot_id` and operate only on that worker.
2. Automatic dispatch for business methods. A small owner-bound proxy keeps the existing `worker_provider(owner)` interface used by `InventoryService`; each method call is dispatched through a lease. Purchase inbound reserves one worker before its background thread starts and holds that lease through login revalidation and the complete save operation.

No Playwright page or context is shared between slots. Workers remain lazy: defining five slots does not launch five browsers; a browser starts only when that channel is logged in or restored.

## API and UI contract

- Add `GET /api/gyj/slots` and its inbound alias. The response contains five allowlisted rows with `id`, `label`, `browser_running`, `logged_in`, `waiting_captcha`, and `busy`.
- Add an optional `slot_id` to GYJ login, captcha, preview, refresh, and login-status requests. Invalid or foreign values are rejected rather than silently falling back to channel 1.
- Login-status without `slot_id` returns aggregate availability plus the five slot rows, preserving the existing top-level `logged_in` field as “at least one channel is logged in.”
- Inventory and inbound login dialogs render the same five channel choices. Selecting a row changes only the login-management target; it never pins later business work to that slot.
- Existing clients that omit `slot_id` continue to manage `gyj-1`, while business APIs always use automatic dispatch.

## Queueing and failure rules

- A lease waits only when at least one channel is logged in but all such channels are reserved. If no channel is logged in, it fails immediately with the existing “请先登录 GYJ” class of message.
- Waiting is bounded by the existing request or job lifecycle. Cancellation and server shutdown must not leave a reservation behind.
- A worker that reports an invalid login during an operation is released and becomes unavailable until that slot is logged in again.
- Login-management calls are rejected for a busy slot so they cannot navigate a page used by a business task.
- The scheduler rotates the starting slot after each successful lease so one channel does not receive all work.
- Pool shutdown closes every created worker across every owner.

## Data and migration

- No inventory, history, or inbound database schema changes are required.
- Existing GYJ credentials storage remains unchanged.
- Existing channel-1 browser data is reused in place. New channel directories are additive and live in the existing persistent data volume, so NAS container replacement does not erase them.
- Current single-channel callers remain source compatible through the owner-bound automatic proxy and the default `gyj-1` login-management behavior.

## Verification

- Pool tests prove five stable slots exist, channel 1 uses the legacy profile path, and additional profiles are distinct.
- Concurrency tests start more than one business call and prove different idle logged-in workers run simultaneously while a sixth call waits until a lease is released.
- Race tests prove two callers cannot reserve the same worker and every exception releases its lease.
- Routing tests prove explicit login/captcha requests affect only the selected channel, invalid slot IDs are rejected, and aggregate login status remains backward compatible.
- Inventory service and purchase-inbound tests prove business calls use automatic dispatch and never a login-management default worker.
- Frontend tests prove both GYJ dialogs expose five channel states and submit the selected `slot_id`.
- The complete Python, Node-vm, and Playwright suite remains green before deployment.
