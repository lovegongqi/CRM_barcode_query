# GYJ Session Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a valid server-side GYJ browser session after the project process restarts, without asking for credentials or a new captcha when GYJ still accepts the existing session.

**Architecture:** `GYJSession.check_login_status()` becomes the single restoration boundary. If its page is absent, it starts the existing persistent profile, navigates to the purchase-inbound page, and treats a non-login GYJ URL as a restored session. The browser remains bound to the tool account's server-side profile; expired sessions still return the normal login/captcha status.

**Tech Stack:** Python, Playwright sync API, unittest, Flask.

## Global Constraints

- Reuse only the existing persistent GYJ browser profile for the current tool account.
- Do not read tokens, cookies, local storage, passwords, or captcha values.
- Never submit a captcha or save a purchase inbound order as part of session restoration.
- If GYJ returns to `/user/login`, preserve the existing manual captcha flow.

---

### Task 1: Restore login state from the persistent GYJ profile

**Files:**
- Modify: `app.py:3845-3863`
- Modify: `tests/test_inbound_routes.py:118-175`

**Interfaces:**
- Consumes: `GYJSession.check_login_status() -> tuple[bool, str]`.
- Produces: a browser restoration attempt before reporting that GYJ is not logged in.

- [ ] **Step 1: Write the failing test**

```python
def test_login_status_reopens_persistent_session_before_reporting_login_required(self):
    session = app_module.GYJSession(TEST_DATA_DIR.name)
    page = _RestorableGYJPage('https://cloud.gyjerp.com/bill/purchase_in')
    session._ensure_browser = mock.Mock(side_effect=lambda: setattr(session, 'page', page) or True)

    ok, message = session.check_login_status()

    self.assertTrue(ok)
    self.assertEqual(message, 'GYJ 已登录')
    self.assertEqual(page.gotos, [('https://cloud.gyjerp.com/bill/purchase_in', 'domcontentloaded', 60000)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_inbound_routes.GYJLoginFlowTest.test_login_status_reopens_persistent_session_before_reporting_login_required -v`

Expected: FAIL because the existing method returns “GYJ 浏览器未启动”.

- [ ] **Step 3: Write the minimal implementation**

```python
if not self.is_alive():
    if not self._ensure_browser():
        return False, 'GYJ 浏览器未启动'
    self.page.goto(GYJ_PURCHASE_IN_URL, wait_until='domcontentloaded', timeout=60000)
```

Then retain the existing URL-based checks: `/user/login` requires the normal login/captcha flow; any valid `cloud.gyjerp.com` page sets `logged_in=True`.

- [ ] **Step 4: Run focused verification**

Run: `python3 -m unittest tests.test_inbound_routes.GYJLoginFlowTest tests.test_gyj_inbound -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_inbound_routes.py docs/superpowers/plans/2026-08-13-gyj-session-restore.md
git commit -m 'fix: restore GYJ session after restart'
```

### Task 2: Restart and verify the project path

**Files:**
- Modify: none

**Interfaces:**
- Consumes: `GET /api/inbound/gyj/login-status`.
- Produces: runtime evidence that the server detects the existing GYJ session after a project restart.

- [ ] **Step 1: Run the full automated suite**

Run: `python3 -m unittest discover -s tests -q`

Expected: all tests pass.

- [ ] **Step 2: Restart the local project service**

Restart the Flask process on `127.0.0.1:5002` after the committed code is loaded.

- [ ] **Step 3: Verify through the project UI**

Open the GYJ workspace and observe `GET /api/inbound/gyj/login-status`. A valid existing session renders `GYJ 已登录，待创建`; an expired session renders the normal captcha prompt. Do not submit captcha or create/save an order in this step.

- [ ] **Step 4: Verify source state**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and no uncommitted source changes.
