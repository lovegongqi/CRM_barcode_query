"""Diagnose whether GYJ products visible in the product library are also visible
in the purchase-inbound 'select product' modal.

Saves evidence to gyj_e2e_evidence/diagnose_*.{png,json}.
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402
import gyj_inbound as gi  # noqa: E402

PRODUCT_CODES = [
    "926023628",  # ETF2300 PF12 滤料罐备件
    "926023602",  # ETF2100PF10 滤料总成
    "746037009",  # 加热体组件组成
    "406005128",  # 电源24VDC3A GVE J10_J12
    "406005140",  # 电源24VDC4A GVE 90度弯插 ERO220
]

OUT = os.path.abspath("gyj_e2e_evidence")


def search_library(page, code):
    """GYJ 商品库 (/material/material) — 列表搜索. Returns row text or None."""
    ok = page.evaluate("""(code) => {
      const isVis = el => !!(el && (el.offsetWidth || el.offsetHeight) && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
      const inputs = Array.from(document.querySelectorAll('input')).filter(isVis);
      const search = inputs.find(i => (i.getAttribute('placeholder') || '').includes('条码') || (i.getAttribute('placeholder') || '').includes('编码') || (i.getAttribute('placeholder') || '').includes('名称'));
      if (!search) return {err: 'no search input'};
      search.value = code;
      search.dispatchEvent(new Event('input', { bubbles: true }));
      const buttons = Array.from(document.querySelectorAll('button')).filter(isVis);
      const query = buttons.find(b => (b.innerText || '').replace(/\\s+/g, '') === '查询' || (b.innerText || '').replace(/\\s+/g, '') === '搜索');
      if (query) query.click();
      return {typed: true};
    }""", code)
    time.sleep(1.0)
    rows = page.evaluate("""() => Array.from(document.querySelectorAll('tr')).slice(0, 4).map(r => (r.innerText || '').replace(/\\s+/g, ' ').slice(0, 200))""")
    return rows


def search_purchase_picker(page, code):
    """GYJ 采购入库 → 选择商品 模态 — 列表搜索."""
    ok = page.evaluate("""(code) => {
      const isVis = el => !!(el && (el.offsetWidth || el.offsetHeight) && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
      const modals = Array.from(document.querySelectorAll('.ant-modal'));
      const picker = modals.find(m => ((m.querySelector('.ant-modal-title') || {}).innerText || '').includes('选择商品'));
      if (!picker) return {err: 'no picker'};
      const input = picker.querySelector("input[placeholder*='条码、名称']");
      if (!input) return {err: 'no search input'};
      input.value = code;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      const buttons = Array.from(picker.querySelectorAll('button'));
      const query = buttons.find(b => (b.innerText || '').replace(/\\s+/g, '') === '查询' || (b.innerText || '').replace(/\\s+/g, '') === '搜索');
      if (query) query.click();
      return {typed: true};
    }""", code)
    time.sleep(1.5)
    rows = page.evaluate("""() => {
      const isVis = el => !!(el && (el.offsetWidth || el.offsetHeight) && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
      const modals = Array.from(document.querySelectorAll('.ant-modal'));
      const picker = modals.find(m => ((m.querySelector('.ant-modal-title') || {}).innerText || '').includes('选择商品'));
      if (!picker) return {err: 'no picker'};
      return Array.from(picker.querySelectorAll('tr')).slice(0, 4).map(r => (r.innerText || '').replace(/\\s+/g, ' ').slice(0, 200));
    }""")
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    sess = app.GYJSession(app._gyj_session_dir("admin"))
    sess._ensure_browser()
    report = {"codes": {}, "errors": []}
    try:
        # 1) Check login + clean form.
        sess.page.goto("https://cloud.gyjerp.com/bill/purchase_in", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        ok, msg = sess.check_login_status()
        if not ok:
            report["errors"].append(f"check_login_status at start: {msg}")
            print("not logged in — abort")
            raise SystemExit(0)
        # 2) 商品库 — each product.
        sess.page.goto("https://cloud.gyjerp.com/material/material", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        for code in PRODUCT_CODES:
            rows = search_library(sess.page, code)
            in_library = any(code in r for r in rows)
            report["codes"].setdefault(code, {})["library"] = {"present": in_library, "rows": rows}
            print(f"LIBRARY {code}: present={in_library}", flush=True)
        # Screenshot library.
        sess.page.screenshot(path=os.path.join(OUT, "diagnose_1_library.png"), full_page=True)
        # 3) 采购入库 — open new form.
        sess.page.goto("https://cloud.gyjerp.com/bill/purchase_in", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        # Cancel any leftover form.
        form = sess.page.locator(".ant-modal.j-modal-box.fullscreen:visible")
        if form.count() == 1:
            cancel = form.get_by_role("button", name="取 消", exact=True)
            if cancel.count() == 1:
                cancel.click()
                time.sleep(1.5)
        # Click 新增.
        nb = sess.page.locator(".table-operator button.ant-btn-primary")
        nb.wait_for(state="visible", timeout=15000)
        nb.click()
        time.sleep(2)
        # Pick supplier 昆山怡口净水 to populate headers.
        ok2 = sess.page.evaluate("""() => {
          const isVis = el => !!(el && (el.offsetWidth || el.offsetHeight) && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none');
          const triggers = Array.from(document.querySelectorAll('.ant-select-selection,.ant-select-selector')).filter(isVis);
          const supplier = triggers.find(t => {
            const ph = t.getAttribute('data-placeholder') || '';
            return ph.includes('供应商') || (t.innerText || '').includes('请选择供应商');
          });
          if (!supplier) return {err: 'no supplier trigger'};
          supplier.click();
          return {clicked: true};
        }""")
        time.sleep(0.6)
        # Click the option.
        ok3 = sess.page.evaluate("""() => {
          const items = Array.from(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) li, .ant-select-item-option'));
          const opt = items.find(li => {
            const t = (li.innerText || '').trim();
            return t === '昆山怡口净水系统有限公司' || t === '昆山怡口净水';
          });
          if (!opt) return {err: 'no option'};
          opt.click();
          return {picked: opt.innerText};
        }""")
        time.sleep(0.6)
        # Click row 1's icon-only magnifier.
        ok4 = sess.page.evaluate("""() => {
          const rows = Array.from(document.querySelectorAll('.tr'));
          const r = rows[1];
          const icon = r.querySelector('button.ant-btn.ant-btn-icon-only');
          if (!icon) return {err: 'no icon'};
          icon.click();
          return {clicked: true};
        }""")
        time.sleep(1.5)
        # Screenshot the picker empty.
        sess.page.screenshot(path=os.path.join(OUT, "diagnose_2_picker_empty.png"), full_page=True)
        # 4) 采购入库 — search each code.
        for code in PRODUCT_CODES:
            rows = search_purchase_picker(sess.page, code)
            in_purchase = any(code in r for r in rows)
            report["codes"].setdefault(code, {})["purchase"] = {"present": in_purchase, "rows": rows}
            print(f"PURCHASE {code}: present={in_purchase}", flush=True)
            sess.page.screenshot(path=os.path.join(OUT, f"diagnose_3_purchase_{code}.png"), full_page=True)
        # 5) 采购入库 — also try the 选单关联 order import path? Skip.
        # 6) Close form so GYJ 端 stays clean.
        form2 = sess.page.locator(".ant-modal.j-modal-box.fullscreen:visible")
        if form2.count() == 1:
            cancel = form2.get_by_role("button", name="取 消", exact=True)
            if cancel.count() == 1:
                cancel.click()
                time.sleep(1.5)
        # Also close any product picker.
        ok5 = sess.page.evaluate("""() => {
          const modals = Array.from(document.querySelectorAll('.ant-modal'));
          for (const m of modals) {
            const close = m.querySelector('button.ant-modal-close, button[aria-label="Close"]');
            if (close) close.click();
          }
          return {closed: modals.length};
        }""")
        print("closed:", ok5, flush=True)
    finally:
        # Save report.
        with open(os.path.join(OUT, "diagnose_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("REPORT:", os.path.join(OUT, "diagnose_report.json"), flush=True)


if __name__ == "__main__":
    main()
