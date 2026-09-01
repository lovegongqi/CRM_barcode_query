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
- Lock timestamps intentionally serialize to seconds, so sub-second clock values are rounded down for comparisons and expiry boundaries.
