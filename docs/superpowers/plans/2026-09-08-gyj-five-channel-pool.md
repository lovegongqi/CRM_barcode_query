# GYJ Five-channel Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide five persistent, system-wide GYJ browser channels, automatically lease an idle logged-in channel for inventory and inbound work, and manage all channel logins from the administrator settings page.

**Architecture:** `GYJWorkerPool` owns five stable slot IDs, lazy workers, a condition variable, atomic reservations, and a round-robin cursor. Login endpoints address a validated slot directly; business callers use a dispatch proxy that leases and releases a channel. Purchase-inbound retains one lease for the full background job. Settings becomes the only GYJ login workspace, while inventory and inbound show aggregate availability.

**Tech Stack:** Python 3, Flask, Playwright worker threads, vanilla JavaScript/HTML, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-08-gyj-five-channel-pool-design.md`

## Global Constraints

- Preserve the existing `gyj_session/admin` profile as `gyj-1`; create additive sibling profiles for `gyj-2` through `gyj-5`.
- Never return or log a stored GYJ password.
- Keep login management explicit by slot and business dispatch automatic.
- Reserve and select under one lock; release every lease in `finally`.
- Make only request-scoped changes and retain backward compatibility for clients omitting `slot_id`.

---

## Task 1: Preserve the existing CRM captcha and iPhone keyboard fixes

**Files:**
- Modify: `app.py`
- Modify: `templates/accounts.html`
- Modify: `templates/inbound.html`
- Modify: `tests/test_frontend_contract.py`
- Create: `tests/test_accounts_frontend.py`
- Create: `tests/test_bulk_login.py`

- [ ] Run the focused tests and confirm the already-observed fixes remain green:

```bash
python3 -m unittest tests.test_bulk_login tests.test_accounts_frontend tests.test_frontend_contract tests.test_inbound_routes -v
```

- [ ] Commit only these existing fixes before starting pool work:

```bash
git add app.py templates/accounts.html templates/inbound.html tests/test_frontend_contract.py tests/test_accounts_frontend.py tests/test_bulk_login.py
git commit -m "fix: broadcast CRM captcha and allow alphanumeric GYJ codes"
```

---

## Task 2: Add stable GYJ slots and atomic leases

**Files:**
- Modify: `app.py`
- Create: `tests/test_gyj_worker_pool.py`

- [ ] Write failing tests that assert five slot IDs, channel-1 legacy path reuse, distinct sibling paths, round-robin selection, sixth-caller waiting, immediate no-login failure, 60-second timeout behavior, and release after exceptions.

```python
class GYJWorkerPoolTest(unittest.TestCase):
    def test_defines_five_stable_slots(self):
        self.assertEqual(pool.slot_ids, ("gyj-1", "gyj-2", "gyj-3", "gyj-4", "gyj-5"))

    def test_lease_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with pool.reserve(timeout=0.1) as worker:
                raise RuntimeError("boom")
        self.assertFalse(pool.slot_status("gyj-1")["busy"])
```

- [ ] Run RED:

```bash
python3 -m unittest tests.test_gyj_worker_pool -v
```

- [ ] Implement `GYJ_SLOT_IDS`, slot validation, stable session directories, lazy slot workers, `busy`/`browser_running` state, an idempotent `GYJWorkerLease`, and `GYJWorkerPool.reserve()` using one `threading.Condition` and round-robin cursor.

- [ ] Return `所有 GYJ 通道正忙，请稍后重试` only after the configured timeout; return the existing login-required error immediately when no slot is logged in.

- [ ] Run GREEN and commit:

```bash
python3 -m unittest tests.test_gyj_worker_pool -v
git add app.py tests/test_gyj_worker_pool.py
git commit -m "feat: add five-channel GYJ worker pool"
```

---

## Task 3: Dispatch inventory and inbound business work through leases

**Files:**
- Modify: `app.py`
- Modify: `tests/test_gyj_worker_pool.py`
- Modify: `tests/test_inventory_routes.py`
- Modify: `tests/test_inbound_routes.py`

- [ ] Write failing tests proving inventory worker methods lease automatically and inbound holds one lease until the background save completes, including release when thread startup or save raises.

- [ ] Run RED:

```bash
python3 -m unittest tests.test_gyj_worker_pool tests.test_inventory_routes tests.test_inbound_routes -v
```

- [ ] Add a narrow business proxy for current inventory methods. Each method must execute inside `with pool.reserve(timeout=60)` and preserve the existing `worker_provider(owner)` call shape.

- [ ] Split access helpers into explicit login access (`gyj_login_worker(slot_id)`) and automatic business access (`gyj_business_worker_for_owner(owner)`). Wire `InventoryService` to the latter.

- [ ] In purchase inbound, reserve before launching the background thread, pass the lease to `_run_inbound_gyj_job`, and release it in that function's `finally`; release immediately if thread creation fails.

- [ ] Run GREEN and commit:

```bash
python3 -m unittest tests.test_gyj_worker_pool tests.test_inventory_routes tests.test_inbound_routes -v
git add app.py tests/test_gyj_worker_pool.py tests/test_inventory_routes.py tests/test_inbound_routes.py
git commit -m "feat: dispatch GYJ business work through idle channels"
```

---

## Task 4: Expose explicit per-channel login APIs

**Files:**
- Modify: `app.py`
- Modify: `tests/test_inbound_routes.py`

- [ ] Write failing route tests for `GET /api/gyj/slots`, aggregate status, `slot_id` targeting, default `gyj-1`, invalid slot rejection, and busy-slot login rejection.

```python
response = client.get("/api/gyj/slots")
self.assertEqual([row["id"] for row in response.get_json()["slots"]], list(GYJ_SLOT_IDS))
```

- [ ] Run RED:

```bash
python3 -m unittest tests.test_inbound_routes -v
```

- [ ] Add the slots endpoint and inbound alias. Make status responses include `total`, `logged_in_count`, `available_count`, `logged_in`, and five sanitized slot rows.

- [ ] Accept optional `slot_id` in login, captcha submit/preview/refresh, and status routes. Validate against the allowlist and route only to that slot. Keep omission mapped to `gyj-1`.

- [ ] Use administrator-stored credentials as the shared source and reject login management on a reserved slot with HTTP 409.

- [ ] Run GREEN and commit:

```bash
python3 -m unittest tests.test_inbound_routes -v
git add app.py tests/test_inbound_routes.py
git commit -m "feat: expose GYJ channel login APIs"
```

---

## Task 5: Add five-channel management to Settings

**Files:**
- Modify: `templates/accounts.html`
- Modify: `tests/test_accounts_frontend.py`
- Modify: `tests/test_frontend_contract.py`

- [ ] Write failing contract tests for `#gyjChannelCard`, five selectable channels, aggregate status, selected `slot_id` on every request, per-slot captcha preview/refresh/submit, and alphanumeric captcha input.

- [ ] Run RED:

```bash
python3 -m unittest tests.test_accounts_frontend tests.test_frontend_contract -v
```

- [ ] Add the administrator-only GYJ card with five compact channel controls and states: not started, waiting captcha, logged in, and busy. Selecting a channel changes only the login target.

- [ ] Poll aggregate slot state, send selected `slot_id` for login and captcha actions, reuse remembered administrator credentials, and render `GYJ 已登录 N/5` without exposing the password.

- [ ] Run GREEN and commit:

```bash
python3 -m unittest tests.test_accounts_frontend tests.test_frontend_contract -v
git add templates/accounts.html tests/test_accounts_frontend.py tests/test_frontend_contract.py
git commit -m "feat: manage GYJ channels from settings"
```

---

## Task 6: Remove duplicate business-page login forms

**Files:**
- Modify: `templates/inventory.html`
- Modify: `static/inventory.js`
- Modify: `templates/inbound.html`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_inventory_frontend.py`

- [ ] Write failing tests proving inventory and inbound display aggregate `GYJ 已登录 N/5`, omit password/captcha forms, and link administrators to `/accounts#gyjChannelCard`.

- [ ] Run RED:

```bash
python3 -m unittest tests.test_frontend_contract tests.test_inventory_frontend -v
```

- [ ] Replace duplicate login workspaces with aggregate availability controls. Keep non-administrator status readable and do not expose login actions to them.

- [ ] Remove obsolete credential/captcha JavaScript and poll the aggregate status endpoint only.

- [ ] Run GREEN and commit:

```bash
python3 -m unittest tests.test_frontend_contract tests.test_inventory_frontend -v
git add templates/inventory.html static/inventory.js templates/inbound.html tests/test_frontend_contract.py tests/test_inventory_frontend.py
git commit -m "refactor: centralize GYJ login management"
```

---

## Task 7: Verify, integrate, and deploy

**Files:**
- Verify all changed files

- [ ] Run focused regression tests:

```bash
python3 -m unittest tests.test_gyj_worker_pool tests.test_bulk_login tests.test_accounts_frontend tests.test_inbound_routes tests.test_inventory_routes tests.test_inventory_frontend tests.test_frontend_contract -v
```

- [ ] Run the full suite and static diff checks:

```bash
python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

- [ ] Review the complete branch diff against the approved spec and confirm no password, credential, session artifact, or unrelated file is present.

- [ ] Merge the verified branch into `main`, push `main` to GitHub, and deploy that exact commit to a new NAS release directory. Before replacement, verify the compose service name is `crm-barcode-query`, host port is `5002`, container port is `5001`, and both named volumes remain mounted.

- [ ] Verify local and external login return HTTP 200, protected inventory/transfer routes redirect when unauthenticated, container logs are healthy, and the running container uses the exact pushed commit/image.
