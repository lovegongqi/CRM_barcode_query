# 入库页深色高对比样式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除 GYJ 冗余状态卡，并让入库页结果和历史组件在深色主题下清晰可读。

**Architecture:** 调整 `templates/inbound.html` 的局部样式、GYJ 状态卡标记和对应的前端状态呈现，不触碰 CRM/GYJ 接口。将浅色背景规则替换为与现有 aurora 主题一致的深蓝半透明面板和高对比文本；同时在 `static/aurora.js` 将入库图标替换为上下双向箭头。

**Tech Stack:** Flask Jinja 模板、内联 CSS、Python unittest。

## Global Constraints

- 不改变装箱单提取、GYJ 登录、保存或历史数据行为。
- 保留 GYJ 实时日志和保存结果。
- 删除“当前阶段 / 入库明细 / 保存规则”三张 GYJ 状态卡。
- 入库结果组件不再使用白色或浅灰色实底。
- 入库导航使用 `⇅`，与移库的 `⇄` 保持同一双向箭头风格。

---

### Task 1: 深色结果样式与 GYJ 卡片清理

**Files:**
- Modify: `templates/inbound.html:28-76`
- Modify: `templates/inbound.html:171-176, 500-705`
- Modify: `static/aurora.js:7`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: 现有 `.inbound-history*`、`.inbound-stat`、`.inbound-box`、`.inbound-product*` 与 `.inbound-serial*` class。
- Produces: 同名 class 的高对比深色视觉表现；GYJ 页面仅保留日志和结果区域。

- [ ] **Step 1: 写入失败的模板契约测试**

```python
def test_inbound_uses_dark_contrast_result_cards_and_omits_gyj_progress_cards(self):
    template = self._read_template("inbound.html")
    self.assertIn("background:rgba(8, 25, 48", template)
    self.assertNotIn('id="gyjStage"', template)
    self.assertNotIn('id="gyjProgress"', template)
```

- [x] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_frontend_contract -v`

Expected: 新增测试失败，因为模板仍有浅色背景与 GYJ 状态卡。

- [x] **Step 3: 实施最小模板调整**

```css
.inbound-product-head {
    background:rgba(8, 25, 48, .88);
    color:#e6f6ff;
}
.inbound-product-toggle {
    background:rgba(13, 44, 72, .9);
    color:#d8f5ff;
}
```

将历史项、进度统计、汇总、结果框、条码标签和产品卡同步改为同一深色面板体系；删除 GYJ 状态卡对应 HTML，并将状态反馈写入保存结果区域。将入库导航图标改为 `⇅`。

- [x] **Step 4: 运行测试确认通过**

Run: `python3 -m unittest tests.test_frontend_contract tests.test_frontend_routes -v`

Expected: PASS。

- [x] **Step 5: 页面验证与提交**

Run: `git diff --check && git add templates/inbound.html tests/test_frontend_contract.py && git commit -m "fix: improve inbound dark theme contrast"`

Expected: 无空白错误；产品标题、数量、按钮与条码标签均为高对比深色样式。
