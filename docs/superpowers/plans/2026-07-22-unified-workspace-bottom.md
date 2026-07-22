# Unified Workspace Bottom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the lowest main window on the query, results, transfer, product-library, and settings pages end on one shared desktop baseline without losing internal scrolling.

**Architecture:** Use one desktop-only flex workspace rule on the existing page containers. Each page's final data window becomes the flexible region, while its table or list remains the internal scroll surface; the results footer moves into the existing bottom padding so it does not shift the results window upward.

**Tech Stack:** Flask/Jinja templates, shared CSS, Python `unittest` frontend contract tests.

## Global Constraints

- Apply the common baseline only above 720px.
- Do not change login, no-permission, query, transfer, filtering, account, or matching behavior.
- Preserve the fixed mobile bottom navigation and current mobile height caps.
- Keep all overflowing records inside their existing window with vertical scrolling.

---

### Task 1: Unify the desktop workspace bottom baseline

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `static/aurora.css`

**Interfaces:**
- Consumes: Existing `data-aurora-page` values and the `.aurora-realtime`, `.aurora-results-grid`, `#editCard`, `#libraryBox`, `#accountListCard`, and `#accountsBox` elements.
- Produces: A desktop-only shared flex layout whose lowest main window fills the remaining viewport height and whose record container scrolls internally.

- [ ] **Step 1: Write the failing layout contract test**

Add this test to `FrontendContractTest`:

```python
def test_work_pages_share_one_desktop_bottom_baseline(self):
    css = (STATIC / "aurora.css").read_text(encoding="utf-8")
    self.assertIn("@media (min-width: 721px)", css)
    for page in ("query", "results", "transfer", "product-library", "settings"):
        self.assertIn(f'body[data-aurora-page="{page}"] .container', css)
    for selector in (
        'body[data-aurora-page="query"] .aurora-realtime',
        'body[data-aurora-page="transfer"] .aurora-realtime',
        'body[data-aurora-page="results"] .aurora-results-grid',
        'body[data-aurora-page="product-library"] #editCard',
        'body[data-aurora-page="settings"] #accountListCard',
    ):
        self.assertIn(selector, css)
    self.assertRegex(css, r"\.aurora-query-table-wrap\s*\{[^}]*overflow-y:\s*auto")
    self.assertRegex(css, r"#libraryBox[^}]*overflow-y:\s*auto")
    self.assertRegex(css, r"#accountsBox[^}]*overflow-y:\s*auto")
```

- [ ] **Step 2: Run the new test and confirm the old fixed-height layout fails**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_work_pages_share_one_desktop_bottom_baseline
```

Expected: `FAIL` because the shared desktop workspace selectors and settings list scroll surface do not exist yet.

- [ ] **Step 3: Add the minimal shared desktop flex layout**

Append a desktop-only block before the existing mobile media query in `static/aurora.css`:

```css
@media (min-width: 721px) {
    body[data-aurora-page="query"] .container,
    body[data-aurora-page="results"] .container,
    body[data-aurora-page="transfer"] .container,
    body[data-aurora-page="product-library"] .container,
    body[data-aurora-page="settings"] .container {
        min-height: max(760px, 100vh);
        display: flex;
        flex-direction: column;
    }

    body[data-aurora-page="query"] .aurora-realtime,
    body[data-aurora-page="transfer"] .aurora-realtime,
    body[data-aurora-page="product-library"] #editCard,
    body[data-aurora-page="settings"] #accountListCard {
        flex: 1 1 0;
        min-height: 360px;
        max-height: none;
        margin-bottom: 0 !important;
        overflow: hidden;
    }

    body[data-aurora-page="transfer"] .grid,
    body[data-aurora-page="transfer"] .grid > div {
        min-height: 0;
        display: flex;
        flex: 1 1 auto;
        flex-direction: column;
    }

    body[data-aurora-page="results"] .aurora-results-grid {
        flex: 1 1 0;
        height: auto;
        min-height: 360px;
        max-height: none;
    }

    body[data-aurora-page="results"] .footer {
        position: absolute;
        right: 24px;
        bottom: 14px;
        left: 24px;
    }

    body[data-aurora-page="product-library"] #editCard,
    body[data-aurora-page="settings"] #accountListCard {
        flex-direction: column;
    }

    body[data-aurora-page="product-library"] #libraryBox,
    body[data-aurora-page="settings"] #accountsBox {
        flex: 1 1 auto;
        min-height: 0;
        overflow-y: auto;
    }
}
```

- [ ] **Step 4: Run targeted and full verification**

Run:

```bash
python3 -m unittest tests.test_frontend_contract.FrontendContractTest.test_work_pages_share_one_desktop_bottom_baseline
python3 -m unittest discover -s tests
git diff --check
```

Expected: the targeted test passes, all frontend tests pass, and `git diff --check` prints no errors.

- [ ] **Step 5: Verify the live local site serves the shared rule**

Request `/static/aurora.css` from `http://127.0.0.1:5011` and assert that it contains the five page container selectors, `@media (min-width: 721px)`, and the three internal scroll surfaces.

- [ ] **Step 6: Commit the isolated implementation**

```bash
git add tests/test_frontend_contract.py static/aurora.css
git commit -m "style: align work page bottom panels"
```
