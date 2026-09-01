# Task 3 report: device locks, heartbeats, versions, and audit events

## Implementation

- Added atomic `BEGIN IMMEDIATE` lock claiming with first-device ownership.
- Same-device claims renew the 120-second lock; heartbeats renew it without changing task version.
- Lock assertions validate device owner and phase.
- Expiration uses stable second-precision ISO timestamps and treats `expires_at <= now` as expired.
- Expiration and manual unlock remove ownership and increment task version; conflicts do not.
- Added named audit events for claims, heartbeat extensions/reclaims, conflicts, expiry, and administrator unlocks.
- Administrator unlock requires `is_admin is True`.

## Verification

- Focused RED run: failed with the expected `AttributeError` before implementation.
- `python3 -m unittest tests.test_inventory_store -v`: 17 tests passed.
- `python3 -m unittest discover -s tests -p 'test*.py' -v`: 277 tests passed.
- `git diff --check`: passed.

## Commit

`feat(inventory): add device locks and audit versions`

## Risks

- Audit event names are implementation-level strings (`lock_claimed`, `lock_heartbeat`, `lock_conflict`, `lock_expired`, `lock_admin_unlocked`); callers should use these names consistently.
- Lock timestamps serialize with microseconds when present, while whole-second values retain compact ISO formatting.

## Review fixes (round 1)

- Lease timestamps are now derived only after `BEGIN IMMEDIATE` succeeds in claim and heartbeat paths.
- Timestamp serialization preserves microseconds when present, guaranteeing at least 120 elapsed seconds.
- Successful lock assertions now commit expiry cleanup; expired locks on other items are removed with their audit/version changes preserved.
- Added deterministic sub-second, write-lock contention, and two-item assertion cleanup regressions.
- Focused store suite: `python3 -m unittest tests.test_inventory_store -v` — 20 passed.
- Full suite: `python3 -m unittest discover -s tests -p 'test*.py' -v` — 280 passed.

## Review fixes (round 2)

- `assert_item_lock` now derives its effective time only after `BEGIN IMMEDIATE` succeeds, preventing stale-time acceptance when a lock expires during writer-lock wait.
- Added a deterministic contention regression that advances the clock past expiry while the assertion is blocked; it verifies `InventoryConflict` plus persisted expiry audit/version behavior.
- Focused store suite: `python3 -m unittest tests.test_inventory_store -v` — 21 passed.
- Full suite: `python3 -m unittest discover -s tests -p 'test*.py' -v` — 281 passed.
