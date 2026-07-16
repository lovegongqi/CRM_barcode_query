# Two-Site Active/Passive Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Hong Kong as the normal site and Singapore as an automatically activated standby, with five-minute data protection, deletion-safe replication, automatic failback, and one-click CRM login across both servers.

**Architecture:** Each site owns an independent PostgreSQL database and independent Playwright profiles. Application-level outbox replication keeps business data synchronized. Cloudflare Load Balancing moves traffic, while a Worker backed by one Durable Object issues a single short-lived write lease and monotonically increasing epoch to prevent split-brain writes.

**Tech Stack:** Python 3.11, Flask 3, PostgreSQL 17, psycopg 3, requests, Cloudflare Workers/Durable Objects/Load Balancing, Docker Compose, pytest.

## Global Constraints

- Phase one plan `2026-07-16-postgresql-storage-migration.md` is complete and green before this plan starts.
- Hong Kong is preferred; Singapore is activated only when Hong Kong is unavailable or recovering.
- Normal replication interval is 60 seconds; automatic failover is blocked when data lag exceeds 300 seconds.
- Only the node holding the current coordinator lease and epoch accepts normal business writes.
- Deleted entities use tombstones retained for at least 30 days and never reappear from a recovered node.
- CRM browser sessions, cookies, live tasks, and Chromium profiles remain node-local.
- One-click login may control the standby node, but this does not grant the standby permission for normal business writes.
- Secrets are environment variables or server secret files with mode `0600`; none enter Git or logs.

---

## File Map

- `crm_ha/config.py`: validated node, peer, timing, and coordinator settings.
- `crm_ha/signing.py`: replay-resistant HMAC authentication for node-to-node APIs.
- `crm_ha/sync.py`: continuous pull, apply, acknowledge, lag tracking, and tombstone maintenance.
- `crm_ha/controller.py`: lease heartbeat, failover activation, recovery, and failback state machine.
- `crm_ha/coordinator.py`: Python client for the Cloudflare coordinator.
- `crm_ha/routes.py`: internal sync/control endpoints plus public status and readiness endpoints.
- `crm_ha/cross_site_login.py`: fans one admin login task out to both servers.
- `crm_storage/migrations/002_ha.sql`: replicated-event receipts, node state, and cross-site job records.
- `cloudflare/coordinator/`: Worker/Durable Object code and tests.
- `infra/cloudflare/configure.py`: idempotent DNS, health monitor, pool, and load balancer setup.
- `deployment/docker-compose.server.yml`: adds sync and HA controller services.
- `deployment/nginx/crm.conf`: origin HTTPS proxy and internal endpoint restrictions.
- `templates/accounts.html`: double-site status and unified batch login controls.
- `static/ha_status.css`, `static/ha_status.js`: compact shared node-status UI.
- `tests/ha/`: two-site integration stack and failover tests.

---

### Task 1: Add validated HA configuration and signed peer requests

**Files:**
- Create: `crm_ha/__init__.py`
- Create: `crm_ha/config.py`
- Create: `crm_ha/signing.py`
- Create: `tests/test_ha_config.py`
- Create: `tests/test_peer_signing.py`
- Modify: `deployment/server.env.example`

**Interfaces:**
- Produces: `HAConfig.from_env()`, `sign_request()`, and `verify_request()`.
- Later internal routes call `verify_request()` before reading any payload.

- [ ] **Step 1: Write failing configuration and signature tests**

```python
def test_ha_config_rejects_unknown_node(monkeypatch):
    monkeypatch.setenv("CRM_NODE_ID", "unknown")
    with pytest.raises(ValueError, match="CRM_NODE_ID"):
        HAConfig.from_env()


def test_signature_rejects_replay():
    headers = sign_request("POST", "/internal/sync/apply", b"{}", "hk", b"shared-secret", now=1000, nonce="n1")
    replay_cache = InMemoryReplayCache()
    verify_request("POST", "/internal/sync/apply", b"{}", headers, b"shared-secret", replay_cache, now=1001)
    with pytest.raises(SignatureError, match="replay"):
        verify_request("POST", "/internal/sync/apply", b"{}", headers, b"shared-secret", replay_cache, now=1002)
```

- [ ] **Step 2: Run the tests and confirm missing modules**

Run: `python -m pytest tests/test_ha_config.py tests/test_peer_signing.py -q`

Expected: collection fails because `crm_ha` does not exist.

- [ ] **Step 3: Implement strict configuration**

```python
@dataclass(frozen=True)
class HAConfig:
    node_id: Literal["hk", "sg"]
    peer_base_url: str
    peer_hmac_secret: bytes
    coordinator_url: str
    coordinator_token: str
    sync_interval_seconds: int = 60
    max_failover_lag_seconds: int = 300
    lease_ttl_seconds: int = 30
    tombstone_days: int = 30
```

Require `CRM_NODE_ID`, `CRM_PEER_BASE_URL`, `CRM_PEER_HMAC_SECRET`, `CRM_COORDINATOR_URL`, and `CRM_COORDINATOR_TOKEN` whenever `CRM_HA_ENABLED=1`.

- [ ] **Step 4: Implement canonical HMAC signing**

Canonical bytes are:

```text
METHOD\nPATH\nUNIX_TIMESTAMP\nNONCE\nSHA256_BODY_HEX\nNODE_ID
```

Use `hmac.compare_digest`; reject timestamps more than 60 seconds old, unknown node IDs, and a nonce already seen in the last 120 seconds.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_ha_config.py tests/test_peer_signing.py -q`

Expected: all tests pass, including body tampering, stale timestamp, and replay cases.

- [ ] **Step 6: Commit peer security**

```bash
git add crm_ha tests/test_ha_config.py tests/test_peer_signing.py deployment/server.env.example
git commit -m "feat: authenticate two-site node traffic"
```

---

### Task 2: Replicate outbox events and tombstones idempotently

**Files:**
- Create: `crm_storage/migrations/002_ha.sql`
- Create: `crm_ha/sync.py`
- Create: `crm_ha/routes.py`
- Create: `tests/test_sync_apply.py`
- Create: `tests/test_sync_loop.py`
- Modify: `crm_storage/postgres_store.py`
- Modify: `app.py`

**Interfaces:**
- Produces: `PostgresStore.apply_remote_event(event)`, `SyncService.run_once()`, `/internal/sync/events`, `/internal/sync/ack`, and `/internal/sync/status`.
- `apply_remote_event()` is idempotent by `event_id` and never emits a second local event.

- [ ] **Step 1: Add failing idempotency and delete-resurrection tests**

```python
def test_remote_event_is_applied_once(sg_store, hk_upsert_event):
    assert sg_store.apply_remote_event(hk_upsert_event) is True
    assert sg_store.apply_remote_event(hk_upsert_event) is False
    assert len(sg_store.list_reports()) == 1


def test_old_upsert_cannot_revive_deleted_entity(sg_store, delete_event, older_upsert_event):
    sg_store.apply_remote_event(delete_event)
    sg_store.apply_remote_event(older_upsert_event)
    assert sg_store.get_entity("distributor", "旧分销商").deleted is True
```

- [ ] **Step 2: Add replicated-event and node-state tables**

```sql
CREATE TABLE sync_applied_events (
    event_id uuid PRIMARY KEY,
    origin_node text NOT NULL,
    local_sequence bigint NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (origin_node, local_sequence)
);

CREATE TABLE ha_node_state (
    node_id text PRIMARY KEY,
    last_peer_contact_at timestamptz,
    last_sync_at timestamptz,
    last_sync_error text NOT NULL DEFAULT '',
    lag_seconds integer,
    last_event_id uuid,
    updated_at timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Step 3: Implement deterministic event application**

Within one transaction: lock the entity row, compare `site_epoch`, then `(origin_node, local_sequence)`, apply the winning upsert/delete, insert the original event into local `sync_events` with `ON CONFLICT DO NOTHING`, record `sync_applied_events`, and update the cursor. Delete events always win over older upserts.

- [ ] **Step 4: Implement pull and acknowledgement routes**

`GET /internal/sync/events?origin=hk&after=123&limit=500` returns ordered JSON events with blob bytes base64 encoded. `POST /internal/sync/ack` records peer cursor and tombstone acknowledgements. Both routes require HMAC and are excluded from normal active-site write fencing.

- [ ] **Step 5: Implement `SyncService.run_once()`**

Pull up to 500 events repeatedly for both origins until caught up, apply them, acknowledge the highest sequence, then write measured lag and errors into `ha_node_state`. The loop sleeps 60 seconds after success and uses capped retry delays `5, 15, 30, 60` seconds after errors.

- [ ] **Step 6: Add 30-day tombstone maintenance**

Only null large report blobs when both `hk_ack_at` and `sg_ack_at` are set and `deleted_at < now() - interval '30 days'`. Never delete the tombstone row or entity key. A node whose cursor predates retained event history reports `full_resync_required` and cannot become ready.

- [ ] **Step 7: Run sync tests**

Run: `python -m pytest tests/test_sync_apply.py tests/test_sync_loop.py -q`

Expected: all tests pass for duplicate delivery, out-of-order upsert/delete, peer outage, retry, and tombstone acknowledgement.

- [ ] **Step 8: Commit replication**

```bash
git add crm_storage crm_ha app.py tests/test_sync_apply.py tests/test_sync_loop.py
git commit -m "feat: replicate PostgreSQL business events"
```

---

### Task 3: Implement the external single-writer coordinator

**Files:**
- Create: `cloudflare/coordinator/package.json`
- Create: `cloudflare/coordinator/wrangler.toml.example`
- Create: `cloudflare/coordinator/src/index.js`
- Create: `cloudflare/coordinator/test/coordinator.test.js`
- Create: `crm_ha/coordinator.py`
- Create: `tests/test_coordinator_client.py`

**Interfaces:**
- Worker endpoints: `GET /state`, `POST /heartbeat`, `POST /acquire`, `POST /begin-failback`, `POST /complete-failback`.
- Python produces `CoordinatorClient.state()`, `heartbeat()`, `acquire()`, `begin_failback()`, and `complete_failback()`.

- [ ] **Step 1: Write failing Durable Object state-machine tests**

```javascript
it("does not grant a second live writer", async () => {
  const hk = await call("/acquire", {node_id: "hk", requested_epoch: 1});
  expect(hk.ok).toBe(true);
  const sg = await call("/acquire", {node_id: "sg", requested_epoch: 2});
  expect(sg.ok).toBe(false);
});

it("requires recovery proof before failback", async () => {
  await expireAndAcquireSingapore();
  const result = await call("/complete-failback", {node_id: "hk", caught_up_event_id: "wrong"});
  expect(result.ok).toBe(false);
});
```

- [ ] **Step 2: Implement one Durable Object keyed as `crm-primary`**

Persist only:

```javascript
{
  active_site: "hk" | "sg" | null,
  epoch: 1,
  lease_expires_at: 0,
  state: "hk_active" | "sg_active" | "hk_recovering",
  last_event_id: null,
  updated_at: 0
}
```

All mutating calls require `Authorization: Bearer <COORDINATOR_TOKEN>`. Lease duration is 30 seconds. Epoch increments whenever ownership changes.

- [ ] **Step 3: Encode failover/failback rules**

- `hk` renews while healthy.
- `sg` may acquire only after the lease expires and its reported lag is `<=300`.
- `hk` recovering from `sg_active` calls `begin-failback`, remains non-serving, then calls `complete-failback` only with the coordinator's expected latest event ID.
- Coordinator switches epoch before returning success; the previous holder immediately loses write permission on its next heartbeat.

- [ ] **Step 4: Implement the Python client with short timeouts**

Use connect timeout 2 seconds and read timeout 3 seconds. Return typed `CoordinatorState`; never silently assume a node is active when the coordinator is unreachable.

- [ ] **Step 5: Run Worker and Python tests**

Run: `cd cloudflare/coordinator && npm test`

Expected: state-machine tests pass.

Run: `python -m pytest tests/test_coordinator_client.py -q`

Expected: all timeout, authorization, and response-validation tests pass.

- [ ] **Step 6: Commit the coordinator**

```bash
git add cloudflare/coordinator crm_ha/coordinator.py tests/test_coordinator_client.py
git commit -m "feat: coordinate a single active CRM site"
```

---

### Task 4: Fence writes and implement automatic activation and failback

**Files:**
- Create: `crm_ha/controller.py`
- Create: `tests/test_ha_controller.py`
- Create: `tests/test_write_fencing.py`
- Modify: `crm_ha/routes.py`
- Modify: `app.py`
- Modify: `deployment/docker-compose.server.yml`

**Interfaces:**
- Produces: `HAController.tick()`, `LeaseState`, `/healthz`, `/readyz`, and Flask write-fencing middleware.
- Compose adds one `ha-controller` process per site.

- [ ] **Step 1: Add failing controller transition tests**

```python
def test_singapore_activates_after_hk_lease_expires(controller_sg, coordinator, synced_store):
    coordinator.expire_current_lease()
    controller_sg.tick()
    assert controller_sg.state.active_site == "sg"
    assert controller_sg.state.can_write is True


def test_hong_kong_does_not_serve_before_catch_up(controller_hk, store_with_lag):
    controller_hk.tick()
    assert controller_hk.state.mode == "hk_recovering"
    assert controller_hk.state.can_write is False
```

- [ ] **Step 2: Implement controller state persistence**

Store the last coordinator response locally in `ha_node_state`, but treat it only as a cache. `can_write` requires an unexpired in-memory lease, matching epoch, healthy database, and successful coordinator heartbeat.

- [ ] **Step 3: Add write-fencing middleware**

Protect `POST`, `PUT`, `PATCH`, and `DELETE` requests except `/internal/*`, `/api/app-auth/login`, and health endpoints. In HA server mode, a non-holder returns HTTP 503 JSON:

```json
{"success": false, "error": "当前节点为备用或正在恢复，请稍后重试", "active_site": "sg"}
```

Internal endpoints still require peer HMAC or coordinator authentication.

- [ ] **Step 4: Implement health semantics**

- `/healthz`: 200 when Flask and local PostgreSQL respond.
- Hong Kong `/readyz`: 200 only when it holds the active lease.
- Singapore `/readyz`: 200 when it holds the lease or is a healthy synchronized standby capable of activation; response JSON exposes `mode`.
- During `hk_recovering`, Hong Kong `/readyz` remains 503 until event cursor and verification checks match Singapore.

- [ ] **Step 5: Implement automatic failback verification**

Before `complete_failback`, compare per-table live/deleted counts, highest event sequence for both origins, latest event UUID, and a deterministic SHA-256 sample of 100 entity keys. Any mismatch keeps Singapore active and records the exact check failure.

- [ ] **Step 6: Run controller and fencing tests**

Run: `python -m pytest tests/test_ha_controller.py tests/test_write_fencing.py -q`

Expected: all lease-expiry, coordinator-loss, lag, recovery, split-brain, and failback tests pass.

- [ ] **Step 7: Commit activation logic**

```bash
git add crm_ha app.py deployment/docker-compose.server.yml tests/test_ha_controller.py tests/test_write_fencing.py
git commit -m "feat: automate fenced site failover and failback"
```

---

### Task 5: Expose compact double-site status in the existing UI

**Files:**
- Create: `static/ha_status.css`
- Create: `static/ha_status.js`
- Modify: `templates/accounts.html`
- Modify: `templates/crm.html`
- Modify: `templates/transfer.html`
- Modify: `templates/index.html`
- Modify: `templates/product_library.html`
- Modify: `app.py`
- Create: `tests/test_ha_status_api.py`

**Interfaces:**
- Produces: `GET /api/ha/status` and reusable `window.CrmHaStatus.mount()`.

- [ ] **Step 1: Add a failing status API test**

```python
def test_status_contains_both_nodes(client, fake_ha_status):
    data = client.get("/api/ha/status").get_json()
    assert data["active_site"] == "hk"
    assert {node["id"] for node in data["nodes"]} == {"hk", "sg"}
    assert data["nodes"][1]["sync_lag_seconds"] == 42
```

- [ ] **Step 2: Implement the status aggregator**

Return current node, active site, epoch, each node's app/database state, sync lag, pending events, last backup, CRM query/transfer login counts, and the latest error. Fetch peer status with a two-second timeout; show it as unreachable rather than failing the page.

- [ ] **Step 3: Add compact responsive status components**

Settings shows a two-row node table and backup/sync details. Other pages show only a small `香港主站` or `新加坡备用` badge near the existing “查看日志” action; clicking opens details. On mobile, labels wrap within the viewport and controls remain one row only where existing layout permits.

- [ ] **Step 4: Run API and template smoke tests**

Run: `python -m pytest tests/test_ha_status_api.py -q`

Expected: status works when peer is healthy, unreachable, stale, and recovering.

Use Playwright screenshots at desktop `1440x900` and mobile `390x844`; verify no horizontal overflow and the five main navigation buttons retain equal sizing.

- [ ] **Step 5: Commit status UI**

```bash
git add static/ha_status.css static/ha_status.js templates app.py tests/test_ha_status_api.py
git commit -m "feat: show two-site health and sync status"
```

---

### Task 6: Add one-click CRM login across both servers

**Files:**
- Create: `crm_ha/cross_site_login.py`
- Create: `crm_storage/migrations/003_cross_site_jobs.sql`
- Create: `tests/test_cross_site_login.py`
- Modify: `crm_ha/routes.py`
- Modify: `app.py:3725-3996, 7365-7466`
- Modify: `templates/accounts.html:180-430`

**Interfaces:**
- Produces: `CrossSiteLoginCoordinator`, `/api/crm/cross-site-login/start`, `/status`, `/captcha`, `/retry`, and signed `/internal/crm-login/*` routes.
- Reuses each node's existing local `_run_bulk_login_job()` and `crm_pool`.

- [ ] **Step 1: Add failing fan-out and captcha tests**

```python
def test_start_skips_logged_in_slots_and_starts_both_nodes(login_coordinator):
    job = login_coordinator.start("gongqi", "password", remember=True)
    assert {row.node_id for row in job.nodes} == {"hk", "sg"}
    assert job.slot("hk", "query-1").status == "skipped_logged_in"
    assert job.slot("sg", "query-1").status == "starting"


def test_one_captcha_reaches_all_waiting_channels(login_coordinator):
    job = login_coordinator.fixture_waiting_on_both_nodes()
    login_coordinator.submit_captcha(job.id, "8269")
    assert login_coordinator.sent_captchas == [("hk", "8269"), ("sg", "8269")]
```

- [ ] **Step 2: Create persistent job-status tables**

```sql
CREATE TABLE cross_site_login_jobs (
    job_id uuid PRIMARY KEY,
    requested_by text NOT NULL,
    status text NOT NULL,
    remember boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE cross_site_login_slots (
    job_id uuid NOT NULL REFERENCES cross_site_login_jobs(job_id) ON DELETE CASCADE,
    node_id text NOT NULL,
    slot_id text NOT NULL,
    status text NOT NULL,
    message text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, node_id, slot_id)
);
```

Do not store username, password, or captcha in these tables. Credentials come from encrypted `crm_credentials`; captcha remains in memory and is cleared after five minutes.

- [ ] **Step 3: Add signed local-login control routes**

The active node calls both itself and its peer. Internal routes start the existing local bulk job, report each local slot, accept captcha, and retry only selected failed slots. Standby write fencing explicitly allows these HMAC-authenticated control routes.

- [ ] **Step 4: Show the验证码 input immediately**

The orchestrator returns `waiting_captcha=true` as soon as the first channel reaches that state. Submitting once broadcasts the code to all currently waiting channels; channels that enter within five minutes receive the in-memory code automatically. A rejected channel remains failed and can be retried without logging out successful channels.

- [ ] **Step 5: Update the settings page**

Replace the single-site button text with `批量登录两台服务器`. Render grouped status pills:

```text
香港：查询1 已登录  查询2 等待验证码  移库1 登录中
新加坡：查询1 已登录  查询2 失败      移库1 已登录
```

Persist only `job_id` in `sessionStorage`; reload status from the backend after navigation or refresh.

- [ ] **Step 6: Run cross-site login tests**

Run: `python -m pytest tests/test_cross_site_login.py -q`

Expected: both-node fan-out, early captcha display, late waiter reuse, page refresh, timeout cleanup, partial failure, and per-slot retry tests pass.

- [ ] **Step 7: Commit cross-site login**

```bash
git add crm_ha/cross_site_login.py crm_storage/migrations/003_cross_site_jobs.sql app.py templates/accounts.html tests/test_cross_site_login.py
git commit -m "feat: log into CRM channels across both sites"
```

---

### Task 7: Make Cloudflare and origin deployment reproducible

**Files:**
- Create: `infra/cloudflare/configure.py`
- Create: `infra/cloudflare/README.md`
- Create: `deployment/nginx/crm.conf`
- Create: `scripts/deploy_node.sh`
- Create: `scripts/install_backup_timer.sh`
- Create: `tests/test_cloudflare_config.py`
- Create: `tests/test_deploy_scripts.py`
- Modify: `deployment/docker-compose.server.yml`

**Interfaces:**
- Produces idempotent setup for `crm.mlmll.cn`, Hong Kong primary pool, Singapore fallback pool, and `/readyz` monitors.
- Deployment script accepts exactly `hk` or `sg` and never deletes data volumes.

- [ ] **Step 1: Add failing idempotency tests for Cloudflare payloads**

```python
def test_load_balancer_prefers_hk_and_falls_back_to_sg(api):
    configure(api, zone="mlmll.cn")
    configure(api, zone="mlmll.cn")
    assert api.count("load_balancer", "crm.mlmll.cn") == 1
    lb = api.get_load_balancer("crm.mlmll.cn")
    assert lb.default_pools == ["hk-pool", "sg-pool"]
    assert lb.fallback_pool == "sg-pool"
```

- [ ] **Step 2: Implement exact Cloudflare resources**

Create/update origin DNS names `crm-hk-origin.mlmll.cn` and `crm-sg-origin.mlmll.cn`, one HTTPS monitor per pool, two one-origin pools, and load balancer `crm.mlmll.cn`. Keep pool order `[hk, sg]`, steering off, and session affinity off. The script reads `CLOUDFLARE_API_TOKEN` from the environment and never prints it.

- [ ] **Step 3: Configure origin HTTPS and access rules**

Nginx proxies public app traffic to `127.0.0.1:5001`, forwards original scheme/IP headers, limits `/internal/` to the peer public IP, and uses a Cloudflare Origin Certificate. PostgreSQL remains unbound to public interfaces.

- [ ] **Step 4: Implement safe node deployment**

`scripts/deploy_node.sh hk` and `scripts/deploy_node.sh sg` validate required secret files, run `docker compose pull/build`, apply database migrations, start services, wait for `/healthz`, and print status. The script must not run `docker compose down -v`, `docker volume rm`, or prune commands.

- [ ] **Step 5: Install daily backup and weekly restore verification timers**

Create systemd timer/service units from the existing backup scripts. Hong Kong and Singapore copy encrypted dumps to each other using SSH keys. Keep 30 daily backups and record verification result in `operation_logs`.

- [ ] **Step 6: Validate infrastructure scripts**

Run: `python -m pytest tests/test_cloudflare_config.py tests/test_deploy_scripts.py -q`

Expected: idempotency, secret redaction, node validation, and no-destructive-command tests pass.

Run: `bash -n scripts/deploy_node.sh scripts/install_backup_timer.sh`

Expected: exit 0.

- [ ] **Step 7: Commit reproducible infrastructure**

```bash
git add infra deployment/nginx scripts tests/test_cloudflare_config.py tests/test_deploy_scripts.py
git commit -m "ops: provision Hong Kong primary and Singapore standby"
```

---

### Task 8: Exercise the complete two-site failure matrix locally

**Files:**
- Create: `tests/ha/docker-compose.yml`
- Create: `tests/ha/fake_crm.py`
- Create: `tests/ha/test_failover_e2e.py`
- Create: `tests/ha/test_cross_site_login_e2e.py`
- Create: `docs/operations/two-site-runbook.md`

**Interfaces:**
- Produces: a repeatable two-app/two-database test environment and exact recovery runbook.

- [ ] **Step 1: Build the two-site integration stack**

Run Hong Kong and Singapore app/database/sync/controller services on isolated networks. Use a local coordinator test service with the same API contract and a fake CRM login service so tests do not send real SMS.

- [ ] **Step 2: Add the active-site and replication scenario**

The test creates a report, product rule, distributor, and account through Hong Kong, waits at most 120 seconds, and verifies identical content in Singapore. It then deletes all four and verifies Singapore contains tombstones and no visible records.

- [ ] **Step 3: Add automatic failover and failback scenarios**

Stop Hong Kong app/controller, verify Singapore acquires a higher epoch and accepts writes, restart Hong Kong, verify it remains unready while catching up, then verify automatic switchback only after hashes and event positions match.

- [ ] **Step 4: Add split-brain and stale-standby scenarios**

Partition each node from the coordinator in turn and assert at most one site accepts a POST. Pause sync for more than 300 seconds and assert Singapore does not auto-activate until manually acknowledged.

- [ ] **Step 5: Add deletion non-resurrection scenario**

Keep Singapore offline, delete a report and distributor in Hong Kong, bring Singapore back with stale rows, and verify incoming tombstones hide them. Simulate offline longer than 30 days and verify Singapore reports `full_resync_required` instead of serving.

- [ ] **Step 6: Add cross-site CRM login scenario**

Start with mixed logged-in states, click one global login, expose the captcha after the first waiter, submit once, and assert all remaining HK/SG channels complete. Refresh status mid-job and verify it resumes.

- [ ] **Step 7: Run the complete HA suite**

Run: `docker compose -f tests/ha/docker-compose.yml up -d --build`

Run: `python -m pytest tests/ha -q`

Expected: active/passive, failover, failback, split-brain, stale standby, delete resurrection, and cross-site login scenarios all pass.

- [ ] **Step 8: Write the operator runbook**

Document status meanings, how to verify active site and epoch, manual pause, stale-standby override, full resync, backup restore, CRM relogin, rollback to Hong Kong, and the commands that must never be used on data volumes.

- [ ] **Step 9: Commit the failure matrix and runbook**

```bash
git add tests/ha docs/operations/two-site-runbook.md
git commit -m "test: cover two-site failover and recovery"
```

---

### Task 9: Deploy Hong Kong and Singapore with rollback gates

**Files:**
- Modify only deployment documentation or scripts for issues discovered during the controlled rollout.

**Interfaces:**
- Produces the live `https://crm.mlmll.cn` active/passive service.

- [ ] **Step 1: Take and verify pre-deployment backups**

Export the current Hong Kong Docker data volume and database, calculate SHA-256, copy one encrypted backup to Singapore, and restore it into a disposable database. Do not proceed unless verification passes.

- [ ] **Step 2: Deploy Singapore first**

Use SSH keys and run:

```bash
ssh root@sg.mlmll.cn 'cd /opt/CRM_barcode_query && ./scripts/deploy_node.sh sg'
```

Expected: `/healthz` is 200, node is standby/read-only, database migration is current, and CRM channels can be logged in independently.

- [ ] **Step 3: Migrate and deploy Hong Kong**

Pause user writes, import the current file volume with `--verify`, then run:

```bash
ssh root@hk.mlmll.cn 'cd /opt/CRM_barcode_query && ./scripts/deploy_node.sh hk'
```

Expected: Hong Kong holds the active lease; source/destination counts and report hashes match.

- [ ] **Step 4: Establish first full sync**

Wait until Singapore reports zero pending events and lag below 60 seconds. Compare report, matching-rule, distributor, account, and tombstone counts on both databases.

- [ ] **Step 5: Deploy coordinator and Cloudflare load balancer**

Publish the Worker using Wrangler secrets, run `infra/cloudflare/configure.py`, then verify `crm.mlmll.cn` reaches Hong Kong and both origin health statuses are visible.

- [ ] **Step 6: Test one-click two-server CRM login**

From the settings page, start one global login, submit one real SMS code, and verify every configured Hong Kong and Singapore query/transfer channel reports its final state. Retry only failures.

- [ ] **Step 7: Perform controlled live failover and failback**

Stop Hong Kong app/controller, verify Cloudflare switches to Singapore, create and delete a disposable test record, restart Hong Kong, verify the record and tombstone arrive, then verify automatic return to Hong Kong.

- [ ] **Step 8: Observe before cleanup**

Monitor sync lag, coordinator epoch, Cloudflare health, application errors, backups, and CRM channel states for 24 hours. Keep the old Hong Kong data volume and pre-migration backup untouched for at least 30 days.

- [ ] **Step 9: Run final verification and publish**

Run unit, integration, and HA suites; use `superpowers:verification-before-completion`; then push reviewed commits to `origin/main`. Do not delete old volumes as part of publishing.
