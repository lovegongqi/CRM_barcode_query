# Query Mobile Selector and Batch Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mobile query-channel strip with a bounded multi-select dropdown, show persistent batch completion/time statistics on every viewport, and hide work-page subtitles only on mobile.

**Architecture:** Keep `selectedQuerySlotIds` as the single selection source for both desktop chips and the new mobile menu. Add a small persisted query-summary snapshot beside the existing `multiBatchJobs` state so completion and elapsed time survive job cleanup and refresh. Restrict layout changes to the shared Aurora stylesheet and the query template.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript, CSS media queries, Python `unittest` frontend contract tests.

## Global Constraints

- Mobile breakpoint is exactly `max-width: 720px`.
- Desktop query-channel chips remain unchanged and visible above 720px.
- Mobile channel selection defaults to all channels and never leaves zero selected channels.
- Query summary is visible on desktop and mobile in the form `完成:N/T　耗时:Hh:Mm:Ss`.
- Mobile work-page subtitles are hidden; desktop subtitles, login content, and no-permission content remain visible.
- Do not add dependencies or change backend APIs.

---

### Task 1: Lock the frontend contracts

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: template source from `templates/crm.html` and shared CSS from `static/aurora.css`.
- Produces: regression tests for mobile selector markup, persisted summary hooks, and mobile-only subtitle hiding.

- [ ] **Step 1: Write the failing contract tests**

Add these methods to `FrontendContractTest`:

```python
def test_mobile_query_channels_use_one_shared_multiselect_state(self):
    query = self.source("crm.html")
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    for token in (
        'id="querySlotMobileTrigger"',
        'id="querySlotMobileMenu"',
        'id="querySlotMobileCount"',
        "toggleQuerySlotMenu",
        "selectAllQuerySlots",
        "selectedQuerySlotIds",
    ):
        self.assertIn(token, query)
    self.assertRegex(css, r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*none")
    mobile_css = css.split("@media (max-width: 720px)", 1)[1]
    self.assertRegex(mobile_css, r"\.aurora-channel-options\s*\{[^}]*display:\s*none")
    self.assertRegex(mobile_css, r"\.aurora-channel-mobile-trigger\s*\{[^}]*display:\s*inline-flex")

def test_query_batch_summary_is_persisted_and_replaces_static_badge(self):
    query = self.source("crm.html")
    self.assertIn('id="queryBatchSummary"', query)
    self.assertIn("formatBatchElapsed", query)
    self.assertIn("crm_last_query_summary", query)
    self.assertIn("captureLastQuerySummary", query)
    self.assertNotIn("AUTO SCHEDULING</span>", query)

def test_mobile_work_page_subtitles_are_hidden_without_affecting_desktop(self):
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    mobile_css = css.split("@media (max-width: 720px)", 1)[1]
    self.assertRegex(
        mobile_css,
        r'body\[data-aurora-page\]:not\(\[data-aurora-page="login"\]\):not\(\[data-aurora-page="no-permission"\]\) \.header > div:first-child > p,[^}]*\.app-subtitle\s*\{[^}]*display:\s*none\s*!important',
    )
    desktop_css = css.split("@media (max-width: 720px)", 1)[0]
    self.assertNotRegex(desktop_css, r"\.app-subtitle\s*\{[^}]*display:\s*none")
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```bash
python -m unittest \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_query_channels_use_one_shared_multiselect_state \
  tests.test_frontend_contract.FrontendContractTest.test_query_batch_summary_is_persisted_and_replaces_static_badge \
  tests.test_frontend_contract.FrontendContractTest.test_mobile_work_page_subtitles_are_hidden_without_affecting_desktop -v
```

Expected: three failures because the new IDs, functions, and CSS rules do not exist yet.

- [ ] **Step 3: Commit the failing contracts**

```bash
git add tests/test_frontend_contract.py
git commit -m "test: define mobile query selector and summary contracts"
```

---

### Task 2: Implement the mobile query-channel dropdown

**Files:**
- Modify: `templates/crm.html`
- Modify: `static/aurora.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `querySlots: Array`, `selectedQuerySlotIds: Array<string>`, `toggleQuerySlot(slotId, checked)`.
- Produces: `toggleQuerySlotMenu(event)`, `closeQuerySlotMenu()`, `selectAllQuerySlots()`, and synchronized desktop/mobile rendering.

- [ ] **Step 1: Add the mobile trigger and menu markup**

Replace the channel picker heading with this structure while retaining `#querySlotSelector`:

```html
<div class="aurora-channel-picker" data-default-selection="all">
    <div class="aurora-channel-heading">
        <div><strong>查询通道</strong><small>默认全选，系统自动调度空闲通道</small></div>
        <button class="aurora-channel-mobile-trigger" id="querySlotMobileTrigger" type="button" aria-expanded="false" aria-controls="querySlotMobileMenu" onclick="toggleQuerySlotMenu(event)">
            <span id="querySlotMobileCount">已选 0/0</span><span aria-hidden="true">⌄</span>
        </button>
    </div>
    <div class="aurora-channel-options" id="querySlotSelector"></div>
    <div class="aurora-channel-mobile-menu" id="querySlotMobileMenu" hidden></div>
</div>
```

- [ ] **Step 2: Render both views from the existing selection state**

Extend `renderQuerySlotSelector()` so it updates the desktop chips, `#querySlotMobileCount`, and `#querySlotMobileMenu`. The mobile menu starts with a select-all action and then renders checkbox rows with the same channel label/status used by desktop:

```javascript
const selectedCount = selectedQuerySlotIds.length;
const count = document.getElementById('querySlotMobileCount');
if (count) count.textContent = `已选 ${selectedCount}/${querySlots.length}`;
const mobileMenu = document.getElementById('querySlotMobileMenu');
if (mobileMenu) {
    mobileMenu.innerHTML = `
        <button class="aurora-channel-select-all" type="button" onclick="selectAllQuerySlots()">✓ 全选通道</button>
        ${querySlots.map(slot => {
            const selected = selectedQuerySlotIds.includes(slot.id);
            const state = slot.logged_in ? '在线' : (slot.remembered_logged_in ? '待验证' : '未登录');
            return `<label class="aurora-channel-mobile-option">
                <input type="checkbox" value="${escapeHtml(slot.id)}" ${selected ? 'checked' : ''} onchange="toggleQuerySlot('${escapeHtml(slot.id)}', this.checked)">
                <span>${escapeHtml(slot.label || slot.id)}</span><small>${escapeHtml(state)}</small>
            </label>`;
        }).join('')}
    `;
}
```

Add menu controls that use the same selection array:

```javascript
function selectAllQuerySlots() {
    selectedQuerySlotIds = querySlots.map(slot => slot.id);
    localStorage.setItem('crm_query_selected_slots', JSON.stringify(selectedQuerySlotIds));
    renderQuerySlotSelector();
}

function toggleQuerySlotMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('querySlotMobileMenu');
    const trigger = document.getElementById('querySlotMobileTrigger');
    const opening = menu.hidden;
    menu.hidden = !opening;
    trigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
}

function closeQuerySlotMenu() {
    const menu = document.getElementById('querySlotMobileMenu');
    const trigger = document.getElementById('querySlotMobileTrigger');
    if (menu) menu.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

document.addEventListener('click', event => {
    if (!event.target.closest('.aurora-channel-picker')) closeQuerySlotMenu();
});
document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeQuerySlotMenu();
});
```

- [ ] **Step 3: Add bounded desktop/mobile styles**

Add base styles with the mobile control hidden by default, then replace the mobile horizontal-strip rule with the dropdown presentation:

```css
.aurora-channel-heading { min-width: 0; }
.aurora-channel-mobile-trigger,
.aurora-channel-mobile-menu { display: none; }

@media (max-width: 720px) {
    .aurora-channel-picker { position: relative; grid-template-columns: 1fr; }
    .aurora-channel-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .aurora-channel-options { display: none; }
    .aurora-channel-mobile-trigger {
        min-height: 34px;
        padding: 7px 10px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        flex: 0 0 auto;
        border: 1px solid rgba(87, 227, 255, .28);
        border-radius: 10px;
        color: #dff8fd;
        background: rgba(73, 203, 233, .10);
    }
    .aurora-channel-mobile-menu {
        position: absolute;
        z-index: 40;
        top: calc(100% - 4px);
        right: 10px;
        width: min(290px, calc(100% - 20px));
        max-height: 320px;
        padding: 8px;
        display: grid;
        gap: 5px;
        overflow-y: auto;
        border: 1px solid rgba(87, 227, 255, .24);
        border-radius: 14px;
        background: rgba(6, 18, 32, .98);
        box-shadow: 0 18px 40px rgba(0, 0, 0, .38);
    }
    .aurora-channel-mobile-menu[hidden] { display: none; }
}
```

Style `.aurora-channel-select-all` and `.aurora-channel-mobile-option` as full-width dark rows with the state aligned to the right.

- [ ] **Step 4: Run the selector contract test**

Run:

```bash
python -m unittest tests.test_frontend_contract.FrontendContractTest.test_mobile_query_channels_use_one_shared_multiselect_state -v
```

Expected: PASS.

- [ ] **Step 5: Commit the selector**

```bash
git add templates/crm.html static/aurora.css tests/test_frontend_contract.py
git commit -m "feat: add mobile query channel multiselect"
```

---

### Task 3: Add persistent completion and elapsed-time summary

**Files:**
- Modify: `templates/crm.html`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `multiBatchJobs.total`, `multiBatchJobs.completed`, `multiBatchJobs.items`, `multiBatchJobs.startedAtMs`.
- Produces: `formatBatchElapsed(seconds)`, `renderQueryBatchSummary()`, `captureLastQuerySummary()`, and session key `crm_last_query_summary`.

- [ ] **Step 1: Replace the static badge target**

Use a stable target in the realtime header:

```html
<span class="aurora-live-badge" id="queryBatchSummary"><span class="aurora-live-dot"></span>完成:0/0　耗时:0h:0m:0s</span>
```

- [ ] **Step 2: Add summary state and formatting**

Initialize the last snapshot from session storage and format elapsed time without padding:

```javascript
let lastQuerySummary = JSON.parse(sessionStorage.getItem('crm_last_query_summary') || 'null') || {
    completed: 0,
    total: 0,
    elapsedSeconds: 0
};

function formatBatchElapsed(seconds) {
    const totalSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    return `${hours}h:${minutes}m:${totalSeconds % 60}s`;
}

function terminalQueryItemCount(items) {
    return (Array.isArray(items) ? items : []).filter(item => ['success', 'error', 'stopped'].includes(item.state)).length;
}

function queryBatchSummarySnapshot() {
    if (multiBatchJobs && multiBatchJobs.total) {
        const startedAtMs = Number(multiBatchJobs.startedAtMs || 0) || Date.parse(multiBatchJobs.started_at || '');
        const endedAtMs = Number(multiBatchJobs.finishedAtMs || 0) || Date.now();
        return {
            completed: Math.max(Number(multiBatchJobs.completed || 0), terminalQueryItemCount(multiBatchJobs.items)),
            total: Number(multiBatchJobs.total || 0),
            elapsedSeconds: Number.isFinite(startedAtMs) && startedAtMs > 0 ? Math.max(0, Math.floor((endedAtMs - startedAtMs) / 1000)) : 0
        };
    }
    return lastQuerySummary;
}

function renderQueryBatchSummary() {
    const target = document.getElementById('queryBatchSummary');
    if (!target) return;
    const summary = queryBatchSummarySnapshot();
    target.innerHTML = `<span class="aurora-live-dot"></span>完成:${summary.completed}/${summary.total}　耗时:${formatBatchElapsed(summary.elapsedSeconds)}`;
}
```

- [ ] **Step 3: Capture and restore the finished batch**

Before `clearMultiBatchJobs()` empties the active state, freeze and persist the summary:

```javascript
function captureLastQuerySummary() {
    if (!multiBatchJobs || !multiBatchJobs.total) return;
    if (!multiBatchJobs.finishedAtMs) multiBatchJobs.finishedAtMs = Date.now();
    lastQuerySummary = queryBatchSummarySnapshot();
    sessionStorage.setItem('crm_last_query_summary', JSON.stringify(lastQuerySummary));
    renderQueryBatchSummary();
}
```

Call `captureLastQuerySummary()` at the start of `clearMultiBatchJobs()`. Call `renderQueryBatchSummary()` from `renderQuerySummary()`, after restoring state on page load, and immediately after creating a new `multiBatchJobs` object so the previous batch is replaced by `0/newTotal`.

- [ ] **Step 4: Run the summary contract test**

Run:

```bash
python -m unittest tests.test_frontend_contract.FrontendContractTest.test_query_batch_summary_is_persisted_and_replaces_static_badge -v
```

Expected: PASS.

- [ ] **Step 5: Commit the summary**

```bash
git add templates/crm.html tests/test_frontend_contract.py
git commit -m "feat: show persistent query batch progress summary"
```

---

### Task 4: Hide work-page subtitles on mobile only

**Files:**
- Modify: `static/aurora.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: shared `data-aurora-page` body attributes and `.app-subtitle` on the results page.
- Produces: one mobile-only visibility rule with login/access exclusions.

- [ ] **Step 1: Add the mobile-only subtitle rule**

Inside `@media (max-width: 720px)`, add:

```css
body[data-aurora-page]:not([data-aurora-page="login"]):not([data-aurora-page="no-permission"]) .header > div:first-child > p,
body[data-aurora-page="results"] .app-subtitle {
    display: none !important;
}
```

- [ ] **Step 2: Run the subtitle contract test**

Run:

```bash
python -m unittest tests.test_frontend_contract.FrontendContractTest.test_mobile_work_page_subtitles_are_hidden_without_affecting_desktop -v
```

Expected: PASS.

- [ ] **Step 3: Commit the mobile subtitle change**

```bash
git add static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: hide work page subtitles on mobile"
```

---

### Task 5: Full regression and visual verification

**Files:**
- Verify: `templates/crm.html`
- Verify: `static/aurora.css`
- Verify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: completed implementation from Tasks 2-4.
- Produces: automated and visual evidence that desktop/mobile behavior matches the approved design.

- [ ] **Step 1: Run the complete automated suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 2: Verify mobile query layout at 430 × 932**

Open `/crm` at 430 × 932 and verify:

- the page header subtitle is absent while the English eyebrow and `CRM 在线查询` remain;
- `查询通道` has an `已选 10/10` trigger on its right;
- the menu opens inside the query card, lists every channel, and does not create horizontal page scrolling;
- the bottom navigation remains fixed and unobscured;
- the realtime header shows `完成:0/0　耗时:0h:0m:0s` before a batch.

- [ ] **Step 3: Verify desktop query layout at 1590 × 1263**

Open `/crm` at 1590 × 1263 and verify:

- the page subtitle remains visible;
- the original desktop channel chips remain visible and the mobile trigger is hidden;
- the realtime header shows completion and elapsed time.

- [ ] **Step 4: Verify a completed batch snapshot**

Run a small query batch, wait for completion or stop it, reload `/crm`, and verify the final completion count and frozen elapsed time remain visible. Start a new batch and verify the summary resets to `0/newTotal`.

- [ ] **Step 5: Review the final diff**

Run:

```bash
git diff HEAD~3 -- templates/crm.html static/aurora.css tests/test_frontend_contract.py
git status --short
```

Expected: only the approved query selector, summary, subtitle, tests, and documentation changes are present; existing unrelated untracked QA images remain untouched.
