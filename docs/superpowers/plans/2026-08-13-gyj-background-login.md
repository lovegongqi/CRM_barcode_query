# GYJ 后台登录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GYJ 采购入库使用工具内凭据提交、后台登录和会话复用。

**Architecture:** `app.py` 增加按工具账号隔离的 GYJ 凭据存储与后台登录状态机，并让 GYJ Worker 使用专用持久化会话。`templates/inbound.html` 增加账号、密码、记住选项和验证码入口；现有入库保存只复用已验证会话。

**Tech Stack:** Python 3.14、Flask、Playwright sync API、原生 JavaScript、unittest。

## Global Constraints

- 仅在用户勾选 `记住账号密码` 时持久化 GYJ 凭据，且按工具账号 ID 隔离。
- 账号、密码、验证码、Cookie 和会话数据不得出现在 API 响应、日志或前端存储。
- 自动入库仍只点击普通 `保存`，绝不审核、提交或转采购退货。

---

### Task 1: GYJ 凭据存储与后台登录

**Files:** `app.py`, `tests/test_inbound_routes.py`

**Interfaces:** `get_remembered_gyj_credentials() -> dict`, `save_remembered_gyj_credentials(remember, username='', password='') -> bool`, `GYJWorker.login_step1(username, password) -> tuple[bool, str]`, `GYJWorker.login_step2(captcha) -> tuple[bool, str]`.

- [ ] **Step 1: Write failing route tests**

```python
def test_gyj_credentials_never_return_password(self):
    self.client.post('/api/inbound/gyj/credentials', json={'remember': True, 'username': 'gyj', 'password': 'secret'})
    self.assertNotIn('password', self.client.get('/api/inbound/gyj/credentials').get_json())

def test_gyj_login_forwards_credentials_to_worker(self):
    response = self.client.post('/api/inbound/gyj/login', json={'username': 'gyj', 'password': 'secret'})
    self.assertTrue(response.get_json()['success'])
```

- [ ] **Step 2: Run RED**

Run `python3 -m unittest tests.test_inbound_routes.InboundRouteTest.test_gyj_login_forwards_credentials_to_worker -v`; expect failure because GYJ login does not accept credentials.

- [ ] **Step 3: Implement minimum secure state**

Add a GYJ-specific credential file and owner key, only return `{remember, username}`, and remove stored credentials if `remember` is false. Add worker `login_step1` and `login_step2`; first fills the visible GYJ login page in the background and reports `logged_in` or `waiting_captcha`, second submits the captcha in the existing page. Use a per-owner persistent Playwright directory and never put transient credentials into jobs.

- [ ] **Step 4: Run GREEN and commit**

Run `python3 -m unittest tests.test_inbound_routes -v`, then commit `feat: add GYJ background login`.

### Task 2: GYJ 登录页面

**Files:** `templates/inbound.html`, `tests/test_frontend_contract.py`

**Interfaces:** consumes `/api/inbound/gyj/credentials`, `/api/inbound/gyj/login`, `/api/inbound/gyj/login/captcha`, and `/api/inbound/gyj/login-status`; produces `loadGYJCredentials()`, `startGYJBackgroundLogin()`, and `submitGYJCaptcha()`.

- [ ] **Step 1: Write failing frontend contract**

```python
def test_inbound_gyj_login_uses_backend_credentials_contract(self):
    inbound = self.source('inbound.html')
    for element_id in ('gyjUsername', 'gyjPassword', 'gyjRememberLogin', 'gyjCaptcha'):
        self.assertIn(f'id="{element_id}"', inbound)
    self.assertIn("fetch('/api/inbound/gyj/credentials'", inbound)
    self.assertIn("fetch('/api/inbound/gyj/login/captcha'", inbound)
```

- [ ] **Step 2: Run RED**

Run `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_inbound_gyj_login_uses_backend_credentials_contract -v`; expect failure because the old button opens a visible browser login.

- [ ] **Step 3: Implement minimum UI**

Add credentials form and submit login through `POST /api/inbound/gyj/login`; clear the password field immediately; only show captcha when status reports `waiting_captcha`; keep creation disabled until `logged_in`.

- [ ] **Step 4: Run GREEN and commit**

Run `python3 -m unittest tests.test_frontend_contract tests.test_inbound_routes -v`, then commit `feat: add GYJ background login controls`.

### Task 3: Regression verification and restart

- [ ] **Step 1: Verify syntax and all tests**

Run `python3 -m py_compile app.py gyj_inbound.py && python3 -m unittest discover -s tests -p 'test_*.py' -v`; expect exit 0.

- [ ] **Step 2: Verify protected route, restart and push**

Verify `GET /inbound` redirects before tool-account login, restart the local server, and push `fix/inbound-element-ui-input`.
