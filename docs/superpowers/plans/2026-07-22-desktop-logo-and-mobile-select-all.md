# Desktop Logo and Mobile Select-All Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 放大所有页面的电脑端 Logo 并与工作页副标题底边对齐，同时修复手机端“全选通道”文字竖排问题。

**Architecture:** 只修改共享 `static/aurora.css`，由现有全页面 `aurora-logo` 和手机端通道菜单类统一生效。使用前端契约测试锁定电脑端与手机端的不同尺寸，以及全选按钮的单行弹性布局。

**Tech Stack:** Flask/Jinja 模板、CSS、Python `unittest`

## Global Constraints

- 电脑端所有页面 Logo 为 `68px × 68px`。
- 工作页面 Logo 底边与副标题底边齐平。
- `720px` 及以下 Logo 仍为 `38px × 38px`。
- 手机端“全选通道”必须单行显示，下方通道复选项布局不变。
- 不修改业务逻辑和页面文案。

---

### Task 1: Add failing visual contract tests

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `static/aurora.css` 的共享选择器。
- Produces: `test_desktop_logo_is_enlarged_and_aligned_with_header_copy` 与 `test_mobile_query_select_all_stays_on_one_line`。

- [ ] **Step 1: Write the failing tests**

```python
def test_desktop_logo_is_enlarged_and_aligned_with_header_copy(self):
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    desktop_css, mobile_css = css.split("@media (max-width: 720px)", 1)
    logo_rule = re.search(r"\.aurora-logo\s*\{([^}]*)\}", desktop_css, re.S)
    self.assertRegex(logo_rule.group(1), r"top:\s*22px")
    self.assertRegex(logo_rule.group(1), r"width:\s*68px")
    self.assertRegex(logo_rule.group(1), r"height:\s*68px")
    self.assertRegex(mobile_css, r"\.aurora-logo\s*\{[^}]*width:\s*38px;[^}]*height:\s*38px")

def test_mobile_query_select_all_stays_on_one_line(self):
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    mobile_css = css.split("@media (max-width: 720px)", 1)[1]
    self.assertRegex(
        mobile_css,
        r"\.aurora-channel-select-all\s*\{[^}]*display:\s*flex;[^}]*white-space:\s*nowrap",
    )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_desktop_logo_is_enlarged_and_aligned_with_header_copy tests.test_frontend_contract.FrontendContractTest.test_mobile_query_select_all_stays_on_one_line -v`

Expected: both tests fail because the desktop Logo is still `58px` at `top: 27px`, and the select-all button still uses grid layout.

### Task 2: Implement the shared CSS corrections

**Files:**
- Modify: `static/aurora.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: existing `.aurora-logo`, `.aurora-channel-select-all`, and mobile media query.
- Produces: shared desktop Logo size/alignment and one-line mobile select-all presentation.

- [ ] **Step 1: Apply the minimal CSS change**

```css
.aurora-logo {
    top: 22px;
    width: 68px;
    height: 68px;
}

@media (max-width: 720px) {
    .aurora-channel-select-all {
        display: flex;
        align-items: center;
        gap: 8px;
        white-space: nowrap;
    }
}
```

Keep the existing mobile `.aurora-logo` rule unchanged so it continues to override the desktop dimensions.

- [ ] **Step 2: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_desktop_logo_is_enlarged_and_aligned_with_header_copy tests.test_frontend_contract.FrontendContractTest.test_mobile_query_select_all_stays_on_one_line -v`

Expected: 2 tests pass.

- [ ] **Step 3: Run the full suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass with zero failures.

- [ ] **Step 4: Verify the rendered pages**

At `1590 × 1263`, open `/crm`, `/`, `/transfer`, `/product-library`, `/accounts`, `/login`, and `/no-permission`; confirm each Logo is enlarged and the five work-page Logo bottoms align with the subtitle bottoms. At `430 × 932`, confirm Logo remains `38px`, “全选通道” is horizontal, and `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

- [ ] **Step 5: Commit**

```bash
git add static/aurora.css tests/test_frontend_contract.py
git commit -m "fix: align desktop logos and mobile channel menu"
```
