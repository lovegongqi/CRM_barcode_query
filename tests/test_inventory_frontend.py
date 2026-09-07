import pathlib
import subprocess
import textwrap
import unittest

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "inventory.js"
STYLE = ROOT / "static" / "inventory.css"


class InventoryFrontendBehaviorTests(unittest.TestCase):
    def test_mobile_account_and_workspace_controls_share_one_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <style>
                    *{{box-sizing:border-box}} body{{margin:0}}
                    .status-bar{{width:414px}}
                    .aurora-account-session{{display:flex;align-items:center}}
                    </style>
                    <div class="status-bar wrap aurora-account-status">
                      <div class="inventory-workspace-tabs">
                        <button class="inventory-workspace-tab">当前盘点</button>
                        <button class="inventory-workspace-tab">历史任务</button>
                        <button class="inventory-workspace-tab">历史差异</button>
                      </div>
                      <div class="aurora-account-session">
                        <span class="aurora-account-name">管理员</span>
                        <a class="aurora-account-logout">退出工具账号</a>
                      </div>
                      <button class="btn" id="inventoryGyjLoginButton">GYJ 已登录</button>
                    </div>
                    """
                )
                page.evaluate("document.body.dataset.auroraPage = 'inventory'")
                controls = page.locator(
                    ".inventory-workspace-tab, .aurora-account-name, "
                    ".aurora-account-logout, #inventoryGyjLoginButton"
                )
                centers = controls.evaluate_all(
                    "nodes => nodes.map(node => { const box = node.getBoundingClientRect(); return box.top + box.height / 2; })"
                )
                status = page.locator(".status-bar").bounding_box()
                self.assertEqual(len(centers), 6)
                self.assertLess(max(centers) - min(centers), 1)
                self.assertLessEqual(status["height"], 44)
                self.assertLessEqual(
                    controls.nth(5).bounding_box()["x"] + controls.nth(5).bounding_box()["width"],
                    status["x"] + status["width"] + 1,
                )
            finally:
                browser.close()

    def test_start_and_complete_actions_use_the_same_mobile_slot(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <style>*{{box-sizing:border-box}} .inventory-section-head{{width:390px}}</style>
                    <div class="inventory-section-head">
                      <div><h2>当前盘点</h2></div>
                      <div class="inventory-task-actions">
                        <button class="btn" id="inventoryCompleteTask" hidden>完成盘点</button>
                        <button class="btn" id="inventoryCreateTask">开始盘点</button>
                      </div>
                    </div>
                    """
                )
                page.evaluate("document.body.dataset.auroraPage = 'inventory'")
                start_box = page.locator("#inventoryCreateTask").bounding_box()
                page.evaluate("""() => {
                    document.querySelector('#inventoryCreateTask').hidden = true;
                    document.querySelector('#inventoryCompleteTask').hidden = false;
                }""")
                complete_box = page.locator("#inventoryCompleteTask").bounding_box()
                self.assertAlmostEqual(start_box["x"], complete_box["x"], delta=1)
                self.assertAlmostEqual(start_box["width"], complete_box["width"], delta=1)
            finally:
                browser.close()

    def test_mobile_difference_state_tabs_are_visible_and_equal_width(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <style>*{{box-sizing:border-box}} body{{margin:0}} .inventory-difference-tabs{{width:390px}}</style>
                    <div class="inventory-difference-tabs">
                      <button class="inventory-workspace-tab is-active">待处理</button>
                      <button class="inventory-workspace-tab">已归档</button>
                    </div>
                    """
                )
                buttons = page.locator(".inventory-difference-tabs button")
                first = buttons.nth(0).bounding_box()
                second = buttons.nth(1).bounding_box()
                self.assertAlmostEqual(first["y"], second["y"], delta=1)
                self.assertAlmostEqual(first["width"], second["width"], delta=1)
                self.assertGreater(first["width"], 150)
                self.assertGreaterEqual(
                    float(buttons.nth(0).evaluate(
                        "node => parseFloat(getComputedStyle(node).fontSize)"
                    )),
                    11,
                )
            finally:
                browser.close()

    def test_collapsed_difference_cards_keep_their_summary_height_in_scroll_list(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                cards = "".join(
                    """<details class="inventory-discrepancy-card">
                    <summary class="inventory-discrepancy-summary">
                      <div class="inventory-history-title">
                        <strong>10000398 · 雷哲V200MAX-N2嵌入式管线机</strong>
                        <code>REG202608050141</code>
                      </div>
                    </summary>
                    </details>"""
                    for _ in range(28)
                )
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <style>*{{box-sizing:border-box}} .inventory-differences{{height:544px}}</style>
                    <div class="inventory-differences">{cards}</div>
                    """
                )
                first = page.locator(".inventory-discrepancy-card").nth(0)
                summary = first.locator("summary")
                self.assertGreaterEqual(first.bounding_box()["height"], 60)
                self.assertGreaterEqual(summary.bounding_box()["height"], 60)
            finally:
                browser.close()

    def test_workspace_tabs_live_inside_account_status_bar(self):
        template = (ROOT / "templates" / "inventory.html").read_text(encoding="utf-8")
        status_start = template.index('<div class="status-bar wrap aurora-account-status">')
        status_end = template.index("</div>\n\n    <main", status_start)
        status_markup = template[status_start:status_end]
        self.assertIn('class="inventory-workspace-tabs"', status_markup)
        self.assertNotIn('class="inventory-workspace-tabs"', template[status_end:])
        self.assertLess(
            status_markup.index('id="inventoryTabCurrent"'),
            status_markup.index('class="aurora-account-session"'),
        )
        self.assertLess(
            status_markup.index('class="aurora-account-session"'),
            status_markup.index('id="inventoryGyjLoginButton"'),
        )
        self.assertIn('<span class="inventory-label-mobile">历史</span>', status_markup)

    def test_mobile_current_panel_keeps_controls_fixed_and_only_items_scroll(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                cards = "".join(
                    f'<article class="inventory-item">商品 {index}</article>'
                    for index in range(30)
                )
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <style>
                    :root{{--aurora-line:#345;--aurora-shadow:none;--aurora-muted:#9ab;
                          --aurora-text:#fff;--aurora-cyan:#5ef;--aurora-control-bg:#071322}}
                    *{{box-sizing:border-box}} body{{margin:0}}
                    .page-nav{{position:fixed;left:8px;right:8px;bottom:10px;height:64px}}
                    </style>
                    <script>document.body.dataset.auroraPage = 'inventory'</script>
                    <div class="container">
                      <header class="header"><h1>库存盘点</h1></header>
                      <div class="status-bar aurora-account-status">
                        <span>管理员</span><button>GYJ 已登录</button>
                        <div class="inventory-workspace-tabs"><button>当前盘点</button><button>历史任务</button><button>历史差异</button></div>
                      </div>
                      <main class="inventory-shell">
                        <section id="inventoryCurrentRoot" class="inventory-current">
                          <div class="inventory-section-head"><div><p class="inventory-eyebrow">CURRENT STOCKTAKE</p><h2>当前盘点</h2><p id="inventoryTaskMeta" class="muted">数量盘点 · 最近 GYJ 同步 2026-09-07T07:06:41.937573</p></div><button>完成盘点</button></div>
                          <div id="inventoryNotice" class="inventory-notice"></div>
                          <div class="inventory-summary"></div>
                          <div class="inventory-toolbar"><label class="inventory-search-field"><span>搜索</span><input></label><label class="inventory-filter-field"><span>产品类别</span><select></select></label><label class="inventory-filter-field"><span>查看范围</span><select></select></label></div>
                          <div class="inventory-scroll-region"><div id="inventoryItems" class="inventory-items">{cards}</div></div>
                        </section>
                      </main>
                    </div>
                    <nav class="page-nav"></nav>
                    """
                )
                page.evaluate("document.body.dataset.auroraPage = 'inventory'")
                notice = page.locator("#inventoryNotice")
                panel = page.locator("#inventoryCurrentRoot")
                scroll = page.locator(".inventory-scroll-region")
                nav = page.locator(".page-nav")
                meta = page.locator("#inventoryTaskMeta")
                status = page.locator(".status-bar")
                last_tab = page.locator(".inventory-workspace-tabs button").last
                self.assertEqual(notice.evaluate("node => getComputedStyle(node).display"), "none")
                self.assertLessEqual(float(meta.evaluate("node => parseFloat(getComputedStyle(node).fontSize)")), 12)
                self.assertLessEqual(
                    last_tab.bounding_box()["y"] + last_tab.bounding_box()["height"],
                    status.bounding_box()["y"] + status.bounding_box()["height"] + 1,
                )
                layout = page.evaluate("""() => Object.fromEntries(
                    ['body', '.container', '.header', '.status-bar', '.inventory-shell', '#inventoryCurrentRoot']
                    .map(selector => {
                        const node = selector === 'body' ? document.body : document.querySelector(selector);
                        const box = node.getBoundingClientRect();
                        const style = getComputedStyle(node);
                        return [selector, {top: box.top, height: box.height, overflow: style.overflow, flex: style.flex}];
                    })
                )""")
                self.assertLessEqual(
                    panel.bounding_box()["y"] + panel.bounding_box()["height"],
                    nav.bounding_box()["y"] + 1,
                    layout,
                )
                self.assertEqual(scroll.evaluate("node => getComputedStyle(node).overflowY"), "auto")
                self.assertGreater(scroll.evaluate("node => node.scrollHeight"), scroll.evaluate("node => node.clientHeight"))
                toolbar_y = page.locator(".inventory-toolbar").bounding_box()["y"]
                scroll.evaluate("node => { node.scrollTop = 250; }")
                self.assertAlmostEqual(page.locator(".inventory-toolbar").bounding_box()["y"], toolbar_y, delta=1)
            finally:
                browser.close()

    def test_serial_dialog_uses_close_as_the_only_completion_action(self):
        template = (ROOT / "templates" / "inventory.html").read_text(encoding="utf-8")
        self.assertIn('id="inventorySerialCancel"', template)
        self.assertNotIn('id="inventorySerialFinish"', template)
        self.assertNotIn("完成该商品核对", template)

    def test_mobile_category_and_state_filters_share_one_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                page.set_content(
                    f"""
                    <style>{STYLE.read_text(encoding='utf-8')}</style>
                    <div class="inventory-toolbar">
                        <label class="inventory-search-field"><span>搜索或扫描商品</span><input></label>
                        <label class="inventory-filter-field"><span>产品类别</span><select></select></label>
                        <label class="inventory-filter-field"><span>查看范围</span><select></select></label>
                    </div>
                    """
                )
                search = page.locator(".inventory-search-field").bounding_box()
                filters = page.locator(".inventory-filter-field")
                category = filters.nth(0).bounding_box()
                state = filters.nth(1).bounding_box()
                category_label = filters.nth(0).locator("span").bounding_box()
                category_select = filters.nth(0).locator("select").bounding_box()

                self.assertAlmostEqual(category["y"], state["y"], delta=1)
                self.assertAlmostEqual(category["width"], state["width"], delta=1)
                self.assertGreater(search["width"], category["width"] + 20)
                self.assertLess(search["y"], category["y"])
                self.assertAlmostEqual(
                    category_label["y"] + category_label["height"] / 2,
                    category_select["y"] + category_select["height"] / 2,
                    delta=1,
                )
                self.assertLessEqual(category_select["height"], 36)
            finally:
                browser.close()

    def test_mobile_summary_cards_share_one_row(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 430, "height": 932})
                cards = "".join(
                    f'<div class="inventory-summary-card"><span>指标{index}</span><strong>{index}</strong></div>'
                    for index in range(8)
                )
                page.set_content(
                    f"<style>{STYLE.read_text(encoding='utf-8')}</style>"
                    f'<div class="inventory-summary">{cards}</div>'
                )
                tops = page.locator(".inventory-summary-card").evaluate_all(
                    "nodes => nodes.map(node => node.getBoundingClientRect().top)"
                )
                self.assertEqual(len(tops), 8)
                self.assertLess(max(tops) - min(tops), 1)
            finally:
                browser.close()

    def run_node(self, body):
        program = textwrap.dedent(
            f"""
            const assert = require('assert');
            const fs = require('fs');
            const vm = require('vm');
            const source = fs.readFileSync(process.argv[1], 'utf8');
            {body}
            """
        )
        result = subprocess.run(
            ["node", "-e", program, str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mutation_helper_sends_expected_version_and_adopts_response_version(self):
        self.run_node(
            r"""
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}},
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {
                        ok: true, status: 200,
                        json: async () => ({success: true, item: {barcode: 'A1'}, version: 5}),
                    };
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('inventoryTask = {task_id: "T1", version: 4}; lastInventoryVersion = 4', context);
            (async () => {
                const result = await vm.runInContext(
                    'inventoryMutationPost("/mutation", {device_id: "D1"})', context
                );
                assert.equal(result.version, 5);
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    device_id: 'D1', expected_version: 4,
                });
                assert.equal(vm.runInContext('inventoryTask.version', context), 5);
                assert.equal(vm.runInContext('lastInventoryVersion', context), 5);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_category_filter_combines_with_state(self):
        self.run_node(
            r"""
            const state = {value: 'variance'};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventoryFilters') return state;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext("inventorySelectedCategory = '滤芯'", context);
            const visible = vm.runInContext(`inventoryVisibleItems([
                {barcode: 'A', category: '滤芯', diff_qty: '-1'},
                {barcode: 'B', category: '整机', diff_qty: '-1'},
                {barcode: 'C', category: '滤芯', diff_qty: '0'}
            ])`, context);
            assert.deepEqual(Array.from(visible, item => item.barcode), ['A']);
            """
        )

    def test_product_card_expansion_survives_poll_render(self):
        self.run_node(
            r"""
            function makeNode(tag = 'div') {
                return {
                    tag, className: '', textContent: '', value: '', disabled: false,
                    dataset: {}, children: [], attributes: {}, listeners: {},
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    setAttribute(name, value) { this.attributes[name] = String(value); },
                    addEventListener(name, callback) { this.listeners[name] = callback; },
                };
            }
            const root = makeNode('div');
            const filter = {value: ''};
            const search = {value: ''};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    querySelector() { return null; },
                    getElementById(id) {
                        if (id === 'inventoryItems') return root;
                        if (id === 'inventoryFilters') return filter;
                        if (id === 'inventorySearch') return search;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const items = [
                {barcode: 'A', name: '甲', state: 'matched', book_qty: 1, actual_qty: 1, diff_qty: 0},
                {barcode: 'B', name: '乙', state: 'serial_pending', has_serial: true, book_qty: 2, actual_qty: 1, diff_qty: -1},
            ];
            vm.runInContext(`inventoryTask = {items: ${JSON.stringify(items)}}`, context);
            vm.runInContext("inventoryExpandedBarcodes.add('A'); renderInventoryItems(inventoryTask.items)", context);
            assert(root.children[0].className.includes('is-expanded'));
            assert.equal(root.children[0].children[0].children.at(-1).attributes['aria-expanded'], 'true');

            vm.runInContext('renderInventoryItems(inventoryTask.items)', context);
            assert(root.children[0].className.includes('is-expanded'));

            filter.value = 'variance';
            vm.runInContext('renderInventoryItems(inventoryTask.items)', context);
            assert.deepEqual(Array.from(vm.runInContext('inventoryExpandedBarcodes', context)), []);
            assert.equal(root.children.length, 1);
            const actionRow = root.children[0].children.at(-1);
            assert(actionRow.children.some(node => node.className.includes('inventory-item-primary-action') && node.textContent === '核对序列号'));
            """
        )

    def test_carton_generation_preserves_suffix_width(self):
        self.run_node(
            r"""
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            assert.deepEqual(Array.from(vm.runInContext("generateCartonSerials('REG202609160521', 3)", context)), [
                'REG202609160521', 'REG202609160522', 'REG202609160523',
            ]);
            assert.deepEqual(Array.from(vm.runInContext("generateCartonSerials('SN0099', 3)", context)), [
                'SN0099', 'SN0100', 'SN0101',
            ]);
            assert.throws(
                () => vm.runInContext("generateCartonSerials('NO-SUFFIX', 20)", context),
                /末尾数字/,
            );
            assert.equal(
                vm.runInContext("formatCartonRange(['1422608126281','1422608126300'])", context),
                '1422608126281～6300（2条，含不连续条码）',
            );
            assert.equal(
                vm.runInContext("formatCartonRange(generateCartonSerials('1422608126281', 20))", context),
                '1422608126281～6300（20条）',
            );
            """
        )

    def test_carton_preview_submits_edited_serials_once(self):
        self.run_node(
            r"""
            const requests = [];
            let confirmations = 0;
            const elements = {
                inventoryCartonSave: {disabled: false, textContent: '保存本箱'},
                inventorySerialMessage: {textContent: '', className: ''},
            };
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) { return elements[id] || null; },
                },
                window: {confirm() { confirmations += 1; return confirmations > 1; }},
                fetch: async (url, options) => {
                    requests.push({url, body: JSON.parse(options.body)});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 3, serial: {expected: [], cartons: []},
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'T1', version: 2};
                currentSerialBarcode = 'B1';
                currentSerialData = {item: {serial_synced_at: 'now'}, expected: [{serial: 'S001'}]};
                cartonPreview = ['S001', 'S999'];
                cartonPreviewQuantity = 2;
                renderCartonPreview = () => {};
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                await vm.runInContext('submitSerialCarton()', context);
                assert.equal(requests.length, 0);
                assert.deepEqual(Array.from(vm.runInContext('cartonPreview', context)), ['S001', 'S999']);
                await vm.runInContext('submitSerialCarton()', context);
                assert.equal(requests.length, 1);
                assert.deepEqual(requests[0].body, {
                    device_id: '', preset_quantity: 2,
                    start_serial: 'S001', serials: ['S001', 'S999'],
                });
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_groups_collapse_and_preserve_expansion(self):
        self.run_node(
            r"""
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('currentSerialData = {}; renderSerialReconciliation = () => {}', context);
            assert.deepEqual(Array.from(vm.runInContext('expandedSerialGroups', context)), []);
            vm.runInContext("toggleSerialGroup('classification:matched')", context);
            assert.deepEqual(Array.from(vm.runInContext('expandedSerialGroups', context)), ['classification:matched']);
            vm.runInContext('renderSerialReconciliation(currentSerialData)', context);
            assert.deepEqual(Array.from(vm.runInContext('expandedSerialGroups', context)), ['classification:matched']);
            vm.runInContext('resetSerialGroupState()', context);
            assert.deepEqual(Array.from(vm.runInContext('expandedSerialGroups', context)), []);
            """
        )

    def test_complete_task_warns_once_then_confirms_unverified_serials(self):
        self.run_node(
            r"""
            const requests = [];
            const notices = [];
            const button = {disabled: false, hidden: false, textContent: '完成盘点'};
            const confirmButton = {disabled: false, textContent: '确认完成'};
            const confirmMessage = {textContent: ''};
            const confirmDialog = {
                open: false,
                showModal() { this.open = true; },
                close() { this.open = false; },
            };
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventoryCompleteTask') return button;
                        if (id === 'inventoryCompletionConfirm') return confirmButton;
                        if (id === 'inventoryCompletionConfirmMessage') return confirmMessage;
                        if (id === 'inventoryCompletionConfirmDialog') return confirmDialog;
                        if (id === 'inventoryNotice') return {textContent: '', className: ''};
                        throw new Error('unexpected element ' + id);
                    },
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    if (requests.length === 1) return {
                        ok: false, status: 409, json: async () => ({
                            success: false, confirmation_required: true,
                            pending_serial_count: 2,
                            error: '仍有 2 个商品未核对序列号',
                        }),
                    };
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 8,
                        task: {task_id: 'T1', completed: true},
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                notices,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'T1', version: 7, phase: 'counting'};
                lastInventoryVersion = 7;
                setInventoryNotice = (message, kind) => notices.push({message, kind});
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                await vm.runInContext("completeInventoryTask({type: 'click'})", context);
                assert.equal(confirmDialog.open, true);
                assert.match(confirmMessage.textContent, /2/);
                assert.equal(requests.length, 1);
                await vm.runInContext('completeInventoryTask(true)', context);
                assert.equal(requests.length, 2);
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    allow_unverified_serials: false, expected_version: 7,
                });
                assert.deepEqual(JSON.parse(requests[1].options.body), {
                    allow_unverified_serials: true, expected_version: 7,
                });
                assert.equal(button.disabled, false);
                assert.equal(button.textContent, '完成盘点');
                assert.equal(confirmDialog.open, false);
                assert.equal(notices.at(-1).message, '盘点已完成，未盘商品未计入差异报告。');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_completed_serial_quantity_overrides_original_manual_count(self):
        self.run_node(
            r"""
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            assert.equal(vm.runInContext(`inventoryActualQuantity({
                state: 'serial_complete', count_total: '33', completed_actual_qty: '34'
            })`, context), '34');
            """
        )

    def test_closing_serial_workspace_finishes_before_dismissing(self):
        self.run_node(
            r"""
            const requests = [];
            const input = {disabled: false, focus() {}};
            const closeButton = {disabled: false, textContent: '关闭'};
            const dialog = {open: true, close() { this.open = false; }};
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', closeButton],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {
                            disabled: false, textContent: '', className: '', focus() {},
                        });
                        return elements.get(id);
                    },
                },
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, serial: {barcode: 'B2', counts: {}},
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'B2';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = () => {};
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                await vm.runInContext('requestSerialWorkspaceClose()', context);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/inventory/tasks/task-1/items/B2/serial/finish');
                assert.equal(dialog.open, false);
                assert.equal(vm.runInContext('serialWorkspaceOpen', context), false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_completed_serial_card_has_recheck_action(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.children = []; this.listeners = {}; this.attributes = {};
                    this.textContent = ''; this.className = ''; this.disabled = false;
                    this.dataset = {};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener(name, callback) { this.listeners[name] = callback; }
                setAttribute(name, value) { this.attributes[name] = value; }
            }
            const root = new FakeNode();
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    createElement() { return new FakeNode(); },
                    getElementById(id) { return id === 'inventoryItems' ? root : new FakeNode(); },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`renderInventoryItems([{
                barcode: 'B2', name: '序列商品', state: 'serial_complete',
                has_serial: true, count_entries: [], book_qty: '1',
                completed_actual_qty: '1', diff_qty: '0', updated_at: 'now'
            }])`, context);
            const labels = [];
            function walk(node) {
                if (node.textContent) labels.push(node.textContent);
                (node.children || []).forEach(walk);
            }
            walk(root);
            assert.ok(labels.includes('重新核对'));
            """
        )

    def test_saved_cartons_render_inside_matching_group_with_header_delete(self):
        self.run_node(
            r"""
            function makeNode(tag = 'div') {
                return {
                    tag, className: '', textContent: '', value: '', disabled: false,
                    hidden: false, dataset: {}, children: [], attributes: {}, listeners: {},
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    setAttribute(name, value) { this.attributes[name] = String(value); },
                    addEventListener(name, callback) { this.listeners[name] = callback; },
                };
            }
            const elements = {
                inventorySerialSyncedAt: makeNode(), inventorySerialProduct: makeNode(),
                inventorySerialCounts: makeNode(), inventorySerialDetails: makeNode(),
            };
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) { return elements[id] || null; },
                },
                window: {confirm() { return true; }},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                serialWorkspaceEditable = true;
                renderCartonEntry = () => {};
                expandedSerialGroups.add('classification:matched');
                currentSerialData = {
                    barcode: 'B2', item: {barcode: 'B2', name: '序列商品', serial_synced_at: 'now'},
                    counts: {matched: 2, system_only: 0, physical_only: 0, other_product: 0, duplicates: 0},
                    matched: [
                        {serial: 'S001', classification: 'matched', carton_id: 7},
                        {serial: 'S002', classification: 'matched', carton_id: 7},
                    ],
                    system_only: [], physical_only: [], other_product: [], duplicates: [],
                    cartons: [{carton_id: 7, scans: [
                        {serial: 'S001', classification: 'matched', carton_id: 7},
                        {serial: 'S002', classification: 'matched', carton_id: 7},
                    ]}],
                };
                renderSerialReconciliation(currentSerialData);
            `, context);
            const matchingGroup = elements.inventorySerialDetails.children[0];
            function allText(node) {
                return [node.textContent || '', ...(node.children || []).map(allText)].join(' ');
            }
            const text = allText(matchingGroup);
            assert.match(text, /匹配 \(2\)/);
            assert.match(text, /S001.*02/);
            assert.match(text, /删除整箱/);
            assert(!elements.inventorySerialDetails.children.slice(1).some(
                node => allText(node).includes('S001')
            ));
            """
        )

    def test_complete_task_refreshes_cleanly_on_version_conflict(self):
        self.run_node(
            r"""
            const notices = [];
            const button = {disabled: false, hidden: false, textContent: '完成盘点'};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventoryCompleteTask') return button;
                        if (id === 'inventoryNotice') return {textContent: '', className: ''};
                        throw new Error('unexpected element ' + id);
                    },
                },
                fetch: async () => ({
                    ok: false, status: 409, json: async () => ({
                        success: false, current_version: 9,
                        error: '盘点数据已更新，请刷新后重试',
                    }),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                polls: 0,
                notices,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'T1', version: 7, phase: 'counting'};
                lastInventoryVersion = 7;
                setInventoryNotice = (message, kind) => notices.push({message, kind});
                pollInventoryTask = async () => { polls += 1; };
            `, context);
            (async () => {
                await vm.runInContext('completeInventoryTask()', context);
                assert.equal(context.polls, 1);
                assert.match(context.notices.at(-1).message, /已更新/);
                assert.equal(button.disabled, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_unverified_completion_adopts_refreshed_version_after_conflict(self):
        self.run_node(
            r"""
            const requests = [];
            const button = {disabled: false, textContent: '确认完成'};
            const message = {textContent: ''};
            const dialog = {open: true, close() { this.open = false; }};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventoryCompletionConfirm') return button;
                        if (id === 'inventoryCompletionConfirmMessage') return message;
                        if (id === 'inventoryCompletionConfirmDialog') return dialog;
                        if (id === 'inventoryNotice') return {textContent: '', className: ''};
                        throw new Error('unexpected element ' + id);
                    },
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    if (requests.length === 1) return {
                        ok: false, status: 409, json: async () => ({
                            success: false, current_version: 8,
                            error: '盘点数据已更新',
                        }),
                    };
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 9,
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'T1', version: 7, phase: 'counting'};
                completionConfirmation = {taskId: 'T1', version: 7};
                pollInventoryTask = async () => { inventoryTask.version = 8; };
            `, context);
            (async () => {
                await vm.runInContext('completeInventoryTask(true)', context);
                assert.equal(dialog.open, true);
                assert.equal(vm.runInContext('completionConfirmation.version', context), 8);
                await vm.runInContext('completeInventoryTask(true)', context);
                assert.deepEqual(
                    requests.map(row => JSON.parse(row.options.body).expected_version),
                    [7, 8],
                );
                assert.equal(dialog.open, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_partial_count_rows_render_and_add_without_a_lock(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor(tag = 'div') {
                    this.tagName = tag; this.children = []; this.textContent = '';
                    this.value = ''; this.disabled = false; this.dataset = {};
                    this.className = ''; this.listeners = {};
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener(type, handler) { this.listeners[type] = handler; }
                focus() {}
            }
            const elements = new Map([
                ['inventoryCountEntries', new FakeNode()],
                ['inventoryCountExpression', new FakeNode()],
                ['inventoryCountNewQuantity', new FakeNode('input')],
                ['inventoryCountAdd', new FakeNode('button')],
                ['inventoryCountMessage', new FakeNode()],
            ]);
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    createElement(tag) { return new FakeNode(tag); },
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, new FakeNode());
                        return elements.get(id);
                    },
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 5, item: {
                            barcode: 'A1', name: '商品', count_total: '25',
                            count_expression: '12 + 13 = 25', state: 'variance',
                            count_entries: [
                                {entry_id: 1, quantity: '12', version: 1, created_by: '甲'},
                                {entry_id: 2, quantity: '13', version: 1, created_by: '乙'},
                            ],
                        },
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            assert.equal(vm.runInContext('typeof renderCountEntries', context), 'function');
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', version: 4, items: [{
                    barcode: 'A1', name: '商品', count_total: '12',
                    count_expression: '12', state: 'matched',
                    count_entries: [{entry_id: 1, quantity: '12', version: 1, created_by: '甲'}],
                }]};
                currentCountItem = inventoryTask.items[0];
                countDialogEditable = true;
                renderInventorySummary = () => {};
                renderInventoryItems = () => {};
                updateCountBook = () => {};
                renderLiveDifference = () => {};
                pollInventoryTask = async () => {};
                renderCountEntries(currentCountItem);
            `, context);
            assert.equal(elements.get('inventoryCountExpression').textContent, '12');
            assert.equal(elements.get('inventoryCountEntries').children.length, 1);
            elements.get('inventoryCountNewQuantity').value = '13';
            (async () => {
                await vm.runInContext('addCountEntry()', context);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/inventory/tasks/T1/items/A1/count-entries');
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    device_id: 'device-a', quantity: '13',
                });
                assert.equal(elements.get('inventoryCountNewQuantity').value, '');
                assert.equal(elements.get('inventoryCountExpression').textContent, '12 + 13 = 25');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_poll_render_preserves_unsaved_existing_count_entry_draft(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor(tag = 'div') {
                    this.tagName = tag; this.children = []; this.textContent = '';
                    this.value = ''; this.disabled = false; this.dataset = {};
                    this.className = ''; this.listeners = {};
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener(type, handler) { this.listeners[type] = handler; }
            }
            const elements = new Map([
                ['inventoryCountEntries', new FakeNode()],
                ['inventoryCountExpression', new FakeNode()],
                ['inventoryCountAuditButton', new FakeNode('button')],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    createElement(tag) { return new FakeNode(tag); },
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, new FakeNode());
                        return elements.get(id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                countDialogEditable = true;
                renderCountEntries({barcode: 'A1', count_expression: '12', count_entries: [
                    {entry_id: 1, quantity: '12', version: 1, created_by: '甲'},
                ]});
            `, context);
            let input = elements.get('inventoryCountEntries').children[0].children[1].children[0];
            input.value = '99';
            input.listeners.input({target: input});
            vm.runInContext(`
                renderCountEntries({barcode: 'A1', count_expression: '12', count_entries: [
                    {entry_id: 1, quantity: '12', version: 1, created_by: '甲'},
                ]});
            `, context);
            input = elements.get('inventoryCountEntries').children[0].children[1].children[0];
            assert.equal(input.value, '99');

            vm.runInContext(`
                renderCountEntries({barcode: 'A1', count_expression: '13', count_entries: [
                    {entry_id: 1, quantity: '13', version: 2, created_by: '乙'},
                ]});
            `, context);
            input = elements.get('inventoryCountEntries').children[0].children[1].children[0];
            assert.equal(input.value, '13');
            assert.match(elements.get('inventoryCountMessage').textContent, /其他设备.*重新填写/);
            """
        )

    def test_partial_count_update_and_delete_use_entry_versions(self):
        self.run_node(
            r"""
            const requests = [];
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    value: '', textContent: '', disabled: false, dataset: {},
                    replaceChildren() {}, append() {}, focus() {},
                    classList: {add() {}, remove() {}, toggle() {}},
                });
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    createElement() { return element(`node-${elements.size}`); },
                    getElementById: element,
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: requests.length + 4,
                        item: {
                            barcode: 'A1', count_total: requests.length === 1 ? '8' : null,
                            count_expression: requests.length === 1 ? '8' : '', count_entries: [],
                        },
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-b';
                inventoryTask = {task_id: 'T1', version: 4, items: [{barcode: 'A1'}]};
                currentCountItem = inventoryTask.items[0];
                countDialogEditable = true;
                renderCountEntries = () => {};
                updateCountBook = () => {};
                renderInventorySummary = () => {};
                renderInventoryItems = () => {};
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                await vm.runInContext("updateCountEntry({entry_id: 9, version: 3}, '8')", context);
                await vm.runInContext("deleteCountEntry({entry_id: 9, version: 4})", context);
                assert.equal(requests[0].url, '/api/inventory/tasks/T1/items/A1/count-entries/9');
                assert.equal(requests[0].options.method, 'POST');
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    device_id: 'device-b', quantity: '8', entry_version: 3,
                });
                assert.equal(requests[1].options.method, 'DELETE');
                assert.deepEqual(JSON.parse(requests[1].options.body), {
                    device_id: 'device-b', entry_version: 4,
                });
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_count_entry_mutation_forces_full_task_summary_refresh(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor(tag = 'div') {
                    this.tagName = tag; this.children = []; this.textContent = '';
                    this.value = ''; this.disabled = false; this.hidden = false;
                    this.dataset = {}; this.className = '';
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener() {}
                focus() {}
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    createElement(tag) { return new FakeNode(tag); },
                    getElementById: element,
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    if (requests.length === 1) return {ok: true, status: 200, json: async () => ({
                        success: true, version: 5,
                        item: {barcode: 'A1', count_total: '3', count_entries: []},
                    })};
                    return {ok: true, status: 200, json: async () => ({
                        success: true, task: {
                            task_id: 'T1', version: 5, phase: 'counting', gyj_status: 'synced',
                            summary: {
                                total: 4, completed: 3, pending: 1, matched: 2,
                                surplus: 1, deficit: 0, serial_pending: 0, data_error: 0,
                            },
                            items: [{barcode: 'A1', count_total: '3', count_entries: []}],
                        },
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {
                    task_id: 'T1', version: 4, phase: 'counting', gyj_status: 'synced',
                    summary: {
                        total: 4, completed: 0, pending: 4, matched: 0,
                        surplus: 0, deficit: 0, serial_pending: 0, data_error: 0,
                    },
                    items: [{barcode: 'A1', count_total: '0', count_entries: []}],
                };
                lastInventoryVersion = 4;
                currentCountItem = inventoryTask.items[0];
                countDialogEditable = true;
                document.getElementById('inventoryCountNewQuantity').value = '3';
                renderCountEntries = () => {};
                updateCountBook = () => {};
                renderInventoryItems = () => {};
                renderSerialQueue = () => {};
            `, context);
            (async () => {
                await vm.runInContext('addCountEntry()', context);
                assert.equal(requests.length, 2);
                assert.equal(requests[1].url, '/api/inventory/tasks/active');
                const cards = element('inventoryTaskSummary').children;
                assert.equal(cards[1].children[1].textContent, 3);
                assert.equal(cards[2].children[1].textContent, 1);
                assert.equal(vm.runInContext('inventoryTask.summary.completed', context), 3);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_count_entry_refresh_failure_keeps_saved_add_and_blocks_duplicate_request(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            class FakeNode {
                constructor() {
                    this.children = []; this.textContent = ''; this.value = '';
                    this.disabled = false; this.hidden = false; this.className = '';
                    this.dataset = {}; this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener() {}
                focus() {}
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const saved = deferred();
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement() { return new FakeNode(); },
                    getElementById: element,
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    if (url.includes('/count-entries') && options.method === 'POST') {
                        if (requests.filter(request => request.options.method === 'POST').length > 1) {
                            throw new Error('duplicate add request');
                        }
                        return saved.promise;
                    }
                    if (url === '/api/inventory/tasks/active') {
                        return {ok: false, status: 502, json: async () => ({
                            success: false, error: '统计服务暂不可用',
                        })};
                    }
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', version: 4, phase: 'counting', items: [{barcode: 'A1', count_entries: []}]};
                currentCountItem = inventoryTask.items[0]; countDialogEditable = true;
                document.getElementById('inventoryCountNewQuantity').value = '3';
                renderCountEntries = () => {}; updateCountBook = () => {}; renderInventoryItems = () => {};
            `, context);
            (async () => {
                const first = vm.runInContext('addCountEntry()', context);
                await Promise.resolve();
                const duplicate = vm.runInContext('addCountEntry()', context);
                saved.resolve({ok: true, status: 200, json: async () => ({
                    success: true, version: 5,
                    item: {barcode: 'A1', count_total: '3', count_entries: []},
                })});
                await first;
                await duplicate;
                assert.equal(requests.filter(request => request.options.method === 'POST').length, 1);
                assert.match(element('inventoryCountMessage').textContent, /已保存，但统计刷新失败/);
                assert.equal(vm.runInContext('inventoryTask.items[0].count_total', context), '3');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_update_and_delete_count_entries_force_full_task_refresh(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() { this.value = ''; this.textContent = ''; this.disabled = false; }
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const requests = [];
            const rendered = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    const version = requests.length < 3 ? 5 : 6;
                    if (url === '/api/inventory/tasks/active') return {
                        ok: true, status: 200, json: async () => ({success: true, task: {
                            task_id: 'T1', version, phase: 'counting', gyj_status: 'synced',
                            summary: {total: 4, completed: version - 2, pending: 6 - version}, items: [],
                        }}),
                    };
                    return {ok: true, status: 200, json: async () => ({success: true, version, item: {
                        barcode: 'A1', count_entries: [], count_total: String(version),
                    }})};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', version: 4, phase: 'counting', items: [{barcode: 'A1', count_entries: []}]};
                currentCountItem = inventoryTask.items[0]; countDialogEditable = true;
                renderCountEntries = () => {}; updateCountBook = () => {}; renderInventoryItems = () => {};
                renderInventoryTask = task => rendered.push(task.summary.completed);
            `, context);
            (async () => {
                await vm.runInContext("updateCountEntry({entry_id: 9, version: 3}, '8')", context);
                await vm.runInContext("deleteCountEntry({entry_id: 9, version: 4})", context);
                assert.deepEqual(requests.map(request => request.url), [
                    '/api/inventory/tasks/T1/items/A1/count-entries/9',
                    '/api/inventory/tasks/active',
                    '/api/inventory/tasks/T1/items/A1/count-entries/9',
                    '/api/inventory/tasks/active',
                ]);
                assert.deepEqual(rendered, [3, 4]);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_counting_phase_exposes_serial_reconciliation(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.children = []; this.hidden = true; this.open = false;
                    this.value = ''; this.disabled = false; this.textContent = '';
                    this.dataset = {}; this.className = '';
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener(type, handler) { this.handler = handler; }
                setAttribute() {}
                showModal() { this.open = true; }
                close() { this.open = false; }
                focus() {}
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement() { return new FakeNode(); },
                    getElementById: element,
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 4,
                        serial: {barcode: 'B2', item: {barcode: 'B2', state: 'serial_pending'}, counts: {}},
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', phase: 'counting', version: 3, items: [{
                    barcode: 'B2', name: '序列商品', state: 'serial_pending', has_serial: true,
                }]};
                renderSerialReconciliation = value => { currentSerialData = value; };
                renderSerialQueue(inventoryTask);
            `, context);
            assert.equal(element('inventorySerialQueueRoot').hidden, false);
            assert.equal(element('inventorySerialQueue').children.length, 1);
            (async () => {
                await vm.runInContext("openSerialItem('B2')", context);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/inventory/tasks/T1/items/B2/serial/open');
                assert.deepEqual(JSON.parse(requests[0].options.body), {device_id: 'device-a'});
                assert.equal(vm.runInContext('serialWorkspaceEditable', context), true);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_open_failure_hides_raw_backend_error(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.children = []; this.hidden = true; this.open = false;
                    this.value = ''; this.disabled = false; this.textContent = '';
                    this.className = '';
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                showModal() { this.open = true; }
                focus() {}
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement() { return new FakeNode(); },
                    getElementById: element,
                },
                fetch: async () => ({
                    ok: false, status: 502,
                    json: async () => ({success: false, error: 'SENTINEL_INTERNAL_STACK'}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', phase: 'counting', version: 3, items: [{
                    barcode: 'B2', name: '序列商品', state: 'serial_pending', has_serial: true,
                }]};
            `, context);
            (async () => {
                await vm.runInContext("openSerialItem('B2')", context);
                const message = element('inventorySerialMessage').textContent;
                assert.equal(message, '暂时无法读取账面序列号缓存，请关闭窗口后重试。');
                assert.doesNotMatch(message, /SENTINEL_INTERNAL_STACK/);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_refresh_preserves_unsubmitted_scan(self):
        self.run_node(
            r"""
            const input = {value: 'UNSUBMITTED', disabled: false, focus() {}};
            const elements = new Map([['inventorySerialInput', input]]);
            let revision = 0;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {
                            textContent: '', className: '', disabled: false,
                            replaceChildren() {}, append() {}, focus() {},
                        });
                        return elements.get(id);
                    },
                },
                fetch: async (url, options) => {
                    revision += 1;
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: revision + 4,
                        serial: {
                            barcode: 'B2', item: {barcode: 'B2'},
                            counts: {matched: revision},
                            matched: Array.from({length: revision}, (_, index) => ({serial: `S${index}`})),
                            system_only: [], physical_only: [], other_product: [], duplicates: [],
                        },
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', phase: 'counting', version: 3};
                currentSerialBarcode = 'B2';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                await vm.runInContext('refreshSerialItem()', context);
                await vm.runInContext('refreshSerialItem()', context);
                assert.equal(input.value, 'UNSUBMITTED');
                assert.equal(vm.runInContext('currentSerialData.counts.matched', context), 2);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_stale_count_entry_refreshes_rows_and_preserves_new_entry_draft(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.children = []; this.value = ''; this.disabled = false;
                    this.textContent = ''; this.className = ''; this.dataset = {};
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener() {}
                focus() {}
            }
            const input = new FakeNode();
            input.value = '7';
            const message = {textContent: '', className: ''};
            const elements = new Map([
                ['inventoryCountNewQuantity', input],
                ['inventoryCountEntries', new FakeNode()],
                ['inventoryCountExpression', new FakeNode()],
                ['inventoryCountMessage', message],
            ]);
            let requestCount = 0;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    createElement() { return new FakeNode(); },
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, new FakeNode());
                        return elements.get(id);
                    },
                },
                fetch: async () => {
                    requestCount += 1;
                    if (requestCount === 1) return {
                        ok: false, status: 409,
                        json: async () => ({
                            success: false, error: '该条已修改', current_version: 4,
                        }),
                    };
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 4, item: {
                            barcode: 'A1', count_total: '3', count_expression: '3',
                            count_entries: [{entry_id: 1, quantity: '3', version: 2}],
                        },
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'T1', version: 3, items: [{barcode: 'A1'}]};
                currentCountItem = inventoryTask.items[0];
                countDialogEditable = true;
                updateCountBook = () => {};
            `, context);
            (async () => {
                await vm.runInContext("updateCountEntry({entry_id: 1, version: 1}, '2')", context);
                assert.equal(requestCount, 2);
                assert.equal(input.value, '7');
                assert.equal(elements.get('inventoryCountExpression').textContent, '3');
                assert.match(message.textContent, /刷新/);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_stale_discrepancy_mutations_reload_rows_before_retry(self):
        self.run_node(
            r"""
            const input = {value: '已复核'};
            const status = {textContent: '', className: ''};
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: true},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        return id === 'inventoryDifferencesStatus' ? status : input;
                    },
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {
                        ok: false, status: 409,
                        json: async () => ({
                            success: false, error: '版本过期', current_version: 4,
                        }),
                    };
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                let discrepancyReloads = 0;
                loadDiscrepancies = async () => { discrepancyReloads += 1; };
            `, context);
            (async () => {
                await vm.runInContext("saveDiscrepancyNote(9, 'SN-1', 3)", context);
                await vm.runInContext('archiveDiscrepancy(9, 3)', context);
                await vm.runInContext('restoreDiscrepancy(9, 3)', context);
                assert.equal(vm.runInContext('discrepancyReloads', context), 3);
                assert.match(status.textContent, /刷新|重试/);
                assert.deepEqual(
                    requests.map(request => JSON.parse(request.options.body).expected_version),
                    [3, 3, 3],
                );
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_search_force_refresh_waits_for_current_query_and_ignores_stale_response(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const search = {value: ''};
            const filters = {value: ''};
            const notice = {textContent: '', className: ''};
            const requests = [];
            const rendered = [];
            const scheduled = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false,
                    addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySearch') return search;
                        if (id === 'inventoryFilters') return filters;
                        if (id === 'inventoryNotice') return notice;
                        throw new Error('unexpected element ' + id);
                    },
                },
                fetch(url) {
                    const pending = deferred();
                    requests.push({url, pending});
                    return pending.promise;
                },
                setTimeout(callback, delay) {
                    scheduled.push({callback, delay});
                    return scheduled.length;
                },
                clearTimeout() {}, setInterval() { return 1; }, clearInterval() {},
                rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('renderInventoryTask = task => rendered.push(task.items[0].barcode)', context);

            (async () => {
                const oldPoll = vm.runInContext('pollInventoryTask({force: true})', context);
                search.value = 'ABC';
                vm.runInContext('handleInventorySearchInput()', context);
                assert.equal(scheduled.length, 1);
                assert.equal(scheduled[0].delay, 250);
                const searchPoll = scheduled.shift().callback();
                assert.equal(requests.length, 1);
                requests[0].pending.resolve(response({success: true, task: {
                    task_id: 'task-1', version: 1, items: [{barcode: 'OLD'}],
                }}));
                for (let i = 0; i < 10 && requests.length < 2; i += 1) await new Promise(setImmediate);
                assert.equal(requests.length, 2, 'forced search must be queued behind the old poll');
                assert.match(requests[1].url, /query=ABC/);
                assert.doesNotMatch(requests[1].url, /version=/);
                requests[1].pending.resolve(response({success: true, task: {
                    task_id: 'task-1', version: 1, items: [{barcode: 'ABC'}],
                }}));
                await Promise.all([oldPoll, searchPoll]);
                assert.deepEqual(rendered, ['ABC'], 'stale OLD response must never render');
                assert.equal(vm.runInContext('inventoryTask.items[0].barcode', context), 'ABC');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_scanner_enter_awaits_exact_query_response_before_opening(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const search = {value: ''};
            const filters = {value: ''};
            const requests = [];
            const opened = [];
            const notices = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false,
                    addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySearch') return search;
                        if (id === 'inventoryFilters') return filters;
                        throw new Error('unexpected element ' + id);
                    },
                },
                fetch(url) {
                    const pending = deferred();
                    requests.push({url, pending});
                    return pending.promise;
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                opened, notices,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('renderInventoryTask = () => {}; openCountItem = async barcode => opened.push(barcode); setInventoryNotice = message => notices.push(message)', context);

            (async () => {
                const oldPoll = vm.runInContext('pollInventoryTask({force: true})', context);
                search.value = 'ABC';
                const enterPoll = vm.runInContext("handleInventorySearchEnter({key: 'Enter', preventDefault() {}})", context);
                let enterFinished = false;
                enterPoll.then(() => { enterFinished = true; });
                await Promise.resolve();
                assert.equal(enterFinished, false, 'Enter must not inspect rows while the old poll is pending');
                requests[0].pending.resolve(response({success: true, task: {
                    task_id: 'task-1', version: 1, items: [{barcode: 'OLD'}],
                }}));
                for (let i = 0; i < 10 && requests.length < 2; i += 1) await new Promise(setImmediate);
                assert.equal(requests.length, 2);
                assert.match(requests[1].url, /query=ABC/);
                requests[1].pending.resolve(response({success: true, task: {
                    task_id: 'task-1', version: 1, items: [{barcode: 'ABC'}],
                }}));
                await Promise.all([oldPoll, enterPoll]);
                assert.deepEqual(opened, ['ABC']);
                assert.equal(notices.includes('没有找到完全匹配的商品条码。'), false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_reopening_login_renders_http_200_captcha_waiting_state(self):
        self.run_node(
            r"""
            function classList() { return {toggle() {}, add() {}}; }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    id, value: '', textContent: '', hidden: true, checked: false,
                    open: false, className: '', classList: classList(),
                    focus() {}, removeAttribute() {},
                    showModal() { this.open = true; }, close() { this.open = false; },
                });
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async url => ({
                    ok: true,
                    status: 200,
                    json: async () => url.endsWith('/credentials')
                        ? {success: true, username: 'worker', remember: true}
                        : {success: false, logged_in: false, waiting_captcha: true, message: 'GYJ 等待验证码'},
                }),
                setTimeout() { return 1; }, clearTimeout() {},
                setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('refreshGyjCaptcha = async () => {}', context);

            (async () => {
                await vm.runInContext('openGyjLogin()', context);
                assert.equal(element('inventoryGyjCaptchaRow').hidden, false);
                vm.runInContext('closeGyjLogin()', context);
                assert.equal(element('inventoryGyjCaptchaRow').hidden, true);
                await vm.runInContext('openGyjLogin()', context);
                assert.equal(element('inventoryGyjCaptchaRow').hidden, false);
                assert.equal(element('inventoryGyjLoginButton').textContent, '登录 GYJ');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_opening_an_already_logged_in_account_keeps_dialog_open(self):
        self.run_node(
            r"""
            function classList() { return {toggle() {}, add() {}}; }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    id, value: '', textContent: '', hidden: true, checked: false,
                    open: false, className: '', classList: classList(),
                    focus() {}, removeAttribute() {},
                    showModal() { this.open = true; }, close() { this.open = false; },
                });
                return elements.get(id);
            }
            const timers = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async url => ({
                    ok: true,
                    status: 200,
                    json: async () => url.endsWith('/credentials')
                        ? {success: true, username: 'worker', remember: true}
                        : {success: true, logged_in: true, waiting_captcha: false},
                }),
                setTimeout(callback) { timers.push(callback); return timers.length; },
                clearTimeout() {}, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('pollInventoryTask = async () => {}', context);

            (async () => {
                await vm.runInContext('openGyjLogin()', context);
                for (const callback of timers) callback();
                assert.equal(element('inventoryGyjLoginDialog').open, true);
                assert.equal(element('inventoryGyjLoginMessage').textContent, 'GYJ 登录成功。');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_waiting_captcha_stops_polling_and_only_loads_preview_once(self):
        self.run_node(
            r"""
            function classList() { return {toggle() {}}; }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    id, hidden: true, textContent: '', className: '',
                    classList: classList(),
                });
                return elements.get(id);
            }
            let clearedTimer = null;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                refreshCalls: 0,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                setTimeout, clearTimeout, setInterval() { return 1; },
                clearInterval(timer) { clearedTimer = timer; },
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                gyjLoginPollTimer = 9;
                refreshGyjCaptcha = async () => { refreshCalls += 1; };
            `, context);

            vm.runInContext(`renderGyjLoginState({
                logged_in: false, waiting_captcha: true, message: 'GYJ 等待验证码'
            })`, context);
            vm.runInContext(`renderGyjLoginState({
                logged_in: false, waiting_captcha: true, message: 'GYJ 等待验证码'
            })`, context);

            assert.equal(vm.runInContext('refreshCalls', context), 1);
            assert.equal(clearedTimer, 9);
            assert.equal(vm.runInContext('gyjLoginPollTimer', context), null);
            """
        )

    def test_manual_captcha_refresh_requests_new_challenge(self):
        self.run_node(
            r"""
            const requests = [];
            const image = {src: '', removeAttribute() { this.src = ''; }};
            const message = {textContent: '', className: ''};
            const dialog = {open: true};
            const elements = new Map([
                ['inventoryGyjCaptchaImage', image],
                ['inventoryGyjLoginMessage', message],
                ['inventoryGyjLoginDialog', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) { return elements.get(id); },
                },
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    return {
                        ok: true, status: 200,
                        json: async () => ({
                            success: true,
                            captcha_image: 'data:image/png;base64,bmV3',
                        }),
                    };
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('gyjLoginSession = 3', context);

            (async () => {
                await vm.runInContext('refreshGyjCaptcha(3, true)', context);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/gyj/captcha/refresh');
                assert.equal(requests[0].options.method, 'POST');
                assert.equal(image.src, 'data:image/png;base64,bmV3');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_device_id_survives_throwing_storage_and_missing_random_uuid(self):
        self.run_node(
            r"""
            const storage = {
                getItem() { throw new Error('storage denied'); },
                setItem() { throw new Error('storage denied'); },
            };
            let randomCalls = 0;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {addEventListener() {}},
                localStorage: storage,
                crypto: {
                    getRandomValues(bytes) {
                        randomCalls += 1;
                        for (let index = 0; index < bytes.length; index += 1) bytes[index] = index;
                        return bytes;
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                randomCalls,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const first = vm.runInContext('getInventoryDeviceId()', context);
            const second = vm.runInContext('getInventoryDeviceId()', context);
            assert.equal(first, '00010203-0405-4607-8809-0a0b0c0d0e0f');
            assert.equal(second, first);
            assert.equal(randomCalls, 1);
            """
        )

    def test_device_id_falls_back_when_random_uuid_throws(self):
        self.run_node(
            r"""
            let randomCalls = 0;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {addEventListener() {}},
                localStorage: {getItem() { return ''; }, setItem() {}},
                crypto: {
                    randomUUID() { throw new Error('uuid unavailable'); },
                    getRandomValues(bytes) {
                        randomCalls += 1;
                        bytes.fill(10);
                        return bytes;
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const first = vm.runInContext('getInventoryDeviceId()', context);
            const second = vm.runInContext('getInventoryDeviceId()', context);
            assert.equal(first, '0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a');
            assert.equal(second, first);
            assert.equal(randomCalls, 1);
            """
        )

    def test_device_id_has_stable_page_fallback_without_crypto_or_storage(self):
        self.run_node(
            r"""
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {addEventListener() {}},
                localStorage: {getItem() { return ''; }, setItem() {}},
                crypto: {},
                Date: class extends Date { static now() { return 123456; } },
                Math: Object.assign(Object.create(Math), {random: () => 0.25}),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const first = vm.runInContext('getInventoryDeviceId()', context);
            const second = vm.runInContext('getInventoryDeviceId()', context);
            assert.match(first, /^inventory-page-/);
            assert.equal(second, first);
            """
        )

    def test_open_count_409_shows_server_error_without_lock_copy(self):
        self.run_node(
            r"""
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    id, value: '', textContent: '', disabled: false, open: false,
                    className: '', dataset: {}, focus() {},
                    showModal() { this.open = true; }, close() { this.open = false; },
                });
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async () => ({
                    ok: false,
                    status: 409,
                    json: async () => ({success: false, error: '当前任务不在数量盘点阶段'}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                renderCountProduct = () => {};
                updateCountBook = () => {};
                renderCountEntries = () => {};
                pollInventoryTask = async () => {};
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1', items: [{
                    barcode: 'A1', name: '商品', state: 'pending',
                    completed_actual_qty: null, lock_actor: '过期显示',
                }]};
            `, context);
            (async () => {
                await vm.runInContext("openCountItem('A1')", context);
                assert.equal(
                    element('inventoryCountMessage').textContent,
                    '当前任务不在数量盘点阶段',
                );
                assert.equal(element('inventoryCountNewQuantity').disabled, true);
                assert.equal(element('inventoryCountAdd').disabled, true);
                assert.equal(vm.runInContext('countDialogEditable', context), false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_scan_clears_and_refocuses_input_and_delete_uses_delete_route(self):
        self.run_node(
            r"""
            const requests = [];
            let focusCount = 0;
            const input = {value: ' SN/1 ', disabled: false, focus() { focusCount += 1; }};
            const elements = new Map([['inventorySerialInput', input]]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {
                            textContent: '', className: '', disabled: false,
                            replaceChildren() {}, append() {}, focus() {},
                        });
                        return elements.get(id);
                    },
                },
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    if (options.method === 'DELETE') return {
                        ok: true, status: 200, json: async () => ({success: true, serial: {
                            barcode: 'A/B', counts: {}, matched: [], system_only: [],
                            physical_only: [], other_product: [], duplicates: [],
                        }}),
                    };
                    if (url.endsWith('/serials')) return {
                        ok: true, status: 200,
                        json: async () => ({success: true, scan: {serial: 'SN/1', classification: 'matched'}}),
                    };
                    return {
                        ok: true, status: 200, json: async () => ({success: true, serial: {
                            barcode: 'A/B', counts: {matched: 1},
                            matched: [{serial: 'SN/1', classification: 'matched'}],
                            system_only: [], physical_only: [], other_product: [], duplicates: [],
                        }}),
                    };
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A/B';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                assert.equal(input.value, '');
                assert.equal(focusCount, 1);
                assert.equal(requests[0].url, '/api/inventory/tasks/task-1/items/A%2FB/serials');
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    device_id: 'device-a', serial: 'SN/1',
                });
                await vm.runInContext("deleteSerialScan('SN/1')", context);
                const deletion = requests.find(request => request.options.method === 'DELETE');
                assert.equal(deletion.url, '/api/inventory/tasks/task-1/items/A%2FB/serials/SN%2F1');
                assert.deepEqual(JSON.parse(deletion.options.body), {
                    device_id: 'device-a',
                });
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_manual_serial_refresh_is_the_only_forced_refresh(self):
        self.run_node(
            r"""
            const intervals = [];
            let focusCount = 0;
            class FakeNode {
                constructor() {
                    this.children = []; this.textContent = ''; this.className = '';
                    this.disabled = false; this.value = '';
                }
                append(...nodes) { this.children.push(...nodes); }
                replaceChildren(...nodes) { this.children = [...nodes]; }
                addEventListener() {}
                focus() { focusCount += 1; }
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const requests = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    createElement() { return new FakeNode(); },
                    getElementById: element,
                },
                fetch: async (url, options) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({
                        success: true, version: 4,
                        serial: {
                            barcode: 'B2',
                            item: {barcode: 'B2', name: '序列商品', serial_synced_at: '2026-09-06T12:34:56'},
                            counts: {}, matched: [], system_only: [], physical_only: [],
                            other_product: [], duplicates: [],
                        },
                    })};
                },
                setTimeout, clearTimeout,
                setInterval(callback, delay) { intervals.push({callback, delay}); return intervals.length; },
                clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', phase: 'counting', version: 3};
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                currentSerialBarcode = 'B2';
            `, context);
            (async () => {
                await vm.runInContext('manualRefreshSerialItem()', context);
                assert.equal(requests.length, 1);
                assert.equal(requests.at(-1).url, '/api/inventory/tasks/T1/items/B2/serial/refresh');
                assert.deepEqual(JSON.parse(requests.at(-1).options.body), {
                    device_id: 'device-a', force: true,
                });
                assert.match(element('inventorySerialSyncedAt').textContent, /最近获取/);
                assert.equal(element('inventorySerialRefresh').disabled, false);
                assert.equal(focusCount, 1);
                assert.equal(intervals.length, 0);
                assert.doesNotMatch(source, /serialRefreshTimer|startSerialRefreshTimer|stopSerialRefreshTimer/);
                assert.doesNotMatch(
                    source,
                    /visibilitychange[\s\S]*refreshSerialItem\([\s\S]*inventoryActiveTab/,
                );
                assert.equal((source.match(/refreshSerialItem\(true\)/g) || []).length, 1);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_manual_serial_refresh_failure_hides_raw_backend_error(self):
        self.run_node(
            r"""
            let focusCount = 0;
            const elements = new Map([
                ['inventorySerialRefresh', {disabled: false}],
                ['inventorySerialInput', {focus() { focusCount += 1; }}],
                ['inventorySerialMessage', {textContent: '', className: ''}],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) { return elements.get(id); },
                },
                fetch: async () => ({
                    ok: false, status: 502,
                    json: async () => ({success: false, error: 'SENTINEL_PRIVATE_GYJ_ERROR'}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'T1', phase: 'counting', version: 3};
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                currentSerialBarcode = 'B2';
            `, context);
            (async () => {
                await vm.runInContext('manualRefreshSerialItem()', context);
                const message = elements.get('inventorySerialMessage').textContent;
                assert.equal(
                    message,
                    '重新获取失败。已保留上次成功获取的数据，请确认 GYJ 已登录后重试。',
                );
                assert.doesNotMatch(message, /SENTINEL_PRIVATE_GYJ_ERROR/);
                assert.equal(elements.get('inventorySerialRefresh').disabled, false);
                assert.equal(focusCount, 1);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_superseded_manual_refresh_does_not_update_reopened_dialog_ui(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const oldRefresh = deferred();
            const newOpen = deferred();
            let focusCount = 0;
            const input = {value: '', disabled: false, focus() { focusCount += 1; }};
            const refreshButton = {disabled: false};
            const message = {textContent: '', className: ''};
            const dialog = {
                open: true,
                close() { this.open = false; },
                showModal() { this.open = true; },
            };
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialRefresh', refreshButton],
                ['inventorySerialMessage', message],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {
                            disabled: false, hidden: true, textContent: '', className: '', srcObject: null,
                        });
                        return elements.get(id);
                    },
                },
                fetch: async url => {
                    if (url.endsWith('/items/A1/serial/refresh')) return oldRefresh.promise;
                    if (url.endsWith('/items/B2/serial/open')) return newOpen.promise;
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1', phase: 'serial_check', items: [
                    {barcode: 'A1', state: 'serial_pending'},
                    {barcode: 'B2', state: 'serial_pending'},
                ]};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                const refresh = vm.runInContext('manualRefreshSerialItem()', context);
                await Promise.resolve();
                vm.runInContext('closeSerialWorkspace()', context);
                const opening = vm.runInContext("openSerialItem('B2')", context);
                await Promise.resolve();
                const loadingMessage = message.textContent;

                oldRefresh.resolve(response({
                    success: true, serial: {barcode: 'A1', counts: {matched: 99}},
                }));
                await refresh;

                assert.equal(vm.runInContext('currentSerialBarcode', context), 'B2');
                assert.equal(message.textContent, loadingMessage);
                assert.match(message.textContent, /正在读取/);
                assert.equal(refreshButton.disabled, true);
                assert.equal(input.disabled, true);
                assert.equal(focusCount, 0);

                newOpen.resolve(response({
                    success: true, serial: {barcode: 'B2', counts: {}},
                }));
                await opening;
                assert.equal(refreshButton.disabled, false);
                assert.equal(focusCount, 1);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_camera_continuously_submits_and_stops(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.disabled = false; this.hidden = false; this.open = true;
                    this.textContent = ''; this.className = ''; this.value = '';
                    this.listeners = {}; this.srcObject = null;
                    this.classList = {add() {}, remove() {}, toggle() {}};
                }
                addEventListener(type, callback) { this.listeners[type] = callback; }
                focus() {}
                close() { this.open = false; }
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const visibilityListeners = [];
            const submitted = [];
            const stoppedTracks = [];
            let decoderCallback = null;
            let stopCount = 0;
            class FakeReader {
                async decodeFromConstraints(constraints, video, callback) {
                    assert.deepEqual(constraints, {video: {facingMode: {ideal: 'environment'}}});
                    assert.equal(video, element('inventoryCameraVideo'));
                    decoderCallback = callback;
                    const track = {stop() { stoppedTracks.push(track); }};
                    video.srcObject = {getTracks() { return [track]; }};
                    return {stop() { stopCount += 1; }};
                }
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                ZXingBrowser: {BrowserMultiFormatReader: FakeReader},
                document: {
                    hidden: false,
                    addEventListener(type, callback) {
                        if (type === 'visibilitychange') visibilityListeners.push(callback);
                    },
                    getElementById: element,
                },
                fetch: async (url, options = {}) => {
                    assert.match(url, /\/serials$/);
                    const serial = JSON.parse(options.body).serial;
                    submitted.push(serial);
                    return {ok: true, status: 200, json: async () => ({
                        success: true, scan: {serial, classification: 'duplicate'},
                    })};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                localStorage: {getItem() { return 'device-a'; }, setItem() {}},
                crypto: {randomUUID() { return 'device-a'; }},
                now: 1000,
            };
            context.window = context;
            context.window.isSecureContext = true;
            context.window.location = {hostname: 'inventory.example.test'};
            context.window.addEventListener = () => {};
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                Date.now = () => now;
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                currentSerialData = {counts: {}, duplicates: []};
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
                bindInventoryTablists = () => {};
                renderInventoryTask = () => {};
                pollInventoryTask = async () => {};
                refreshGyjStatusButton = async () => {};
                initializeInventoryPage();
            `, context);
            (async () => {
                await vm.runInContext('startInventoryCamera()', context);
                decoderCallback({getText() { return ' SN-1 '; }}, null);
                decoderCallback({getText() { return 'SN-1'; }}, null);
                context.now += 100;
                decoderCallback({text: 'SN-2'}, null);
                context.now += 1600;
                decoderCallback({getText() { return 'SN-2'; }}, null);
                await vm.runInContext('serialOperationQueue', context);
                assert.deepEqual(submitted, ['SN-1', 'SN-2', 'SN-2']);

                context.document.hidden = true;
                visibilityListeners.forEach(callback => callback());
                assert.equal(stopCount, 1);
                assert.equal(stoppedTracks.length, 1);
                assert.equal(element('inventoryCameraVideo').srcObject, null);
                assert.equal(element('inventoryCameraPanel').hidden, true);

                context.document.hidden = false;
                await vm.runInContext('startInventoryCamera()', context);
                const secondTrack = element('inventoryCameraVideo').srcObject.getTracks()[0];
                vm.runInContext('closeSerialWorkspace()', context);
                assert.equal(stopCount, 2);
                assert.equal(stoppedTracks.filter(track => track === secondTrack).length, 1);
                assert.equal(element('inventoryCameraStart').disabled, false);
                assert.equal(element('inventoryCameraStop').disabled, true);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_carton_camera_fills_editable_start_without_saving(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.disabled = false; this.hidden = true; this.textContent = '';
                    this.className = ''; this.value = ''; this.srcObject = null; this.focused = 0;
                }
                focus() { this.focused += 1; }
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            let decoderCallback = null;
            let stopCount = 0;
            let fetchCount = 0;
            class FakeReader {
                async decodeFromConstraints(_constraints, video, callback) {
                    decoderCallback = callback;
                    video.srcObject = {getTracks() { return [{stop() {}}]; }};
                    return {stop() { stopCount += 1; }};
                }
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async () => { fetchCount += 1; throw new Error('must not save'); },
                ZXingBrowser: {BrowserMultiFormatReader: FakeReader},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            context.window = context;
            context.window.isSecureContext = true;
            context.window.location = {hostname: 'inventory.example.test'};
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('serialWorkspaceOpen = true; serialWorkspaceEditable = true', context);
            (async () => {
                await vm.runInContext("startInventoryCamera('carton-start')", context);
                decoderCallback({getText() { return ' 1422608126281 '; }}, null);
                assert.equal(element('inventoryCartonStartSerial').value, '1422608126281');
                assert.equal(element('inventoryCartonStartSerial').focused, 1);
                assert.equal(stopCount, 1);
                assert.equal(fetchCount, 0);
                assert.equal(vm.runInContext('cartonPreview.length', context), 0);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_explicit_camera_stop_reports_stopped_and_restart_reports_active(self):
        self.run_node(
            r"""
            class FakeNode {
                constructor() {
                    this.disabled = false; this.hidden = true; this.textContent = '';
                    this.className = ''; this.srcObject = null;
                }
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            class FakeReader {
                async decodeFromConstraints(_constraints, video) {
                    video.srcObject = {getTracks() { return [{stop() {}}]; }};
                    return {stop() { video.srcObject = null; }};
                }
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                ZXingBrowser: {BrowserMultiFormatReader: FakeReader},
                document: {hidden: false, addEventListener() {}, getElementById: element},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            context.window = context;
            context.window.isSecureContext = true;
            context.window.location = {hostname: 'inventory.example.test'};
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
            `, context);
            (async () => {
                await vm.runInContext('startInventoryCamera()', context);
                assert.equal(element('inventoryCameraMessage').textContent, '相机连续扫码已开启。');

                vm.runInContext('stopInventoryCamera()', context);
                assert.equal(element('inventoryCameraMessage').textContent, '相机已停止。');

                await vm.runInContext('startInventoryCamera()', context);
                assert.equal(element('inventoryCameraMessage').textContent, '相机连续扫码已开启。');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_stale_camera_start_cannot_clear_newer_session(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            class FakeNode {
                constructor() {
                    this.disabled = false; this.hidden = true; this.textContent = '';
                    this.className = ''; this.srcObject = null;
                }
                focus() {}
            }
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, new FakeNode());
                return elements.get(id);
            }
            const firstReady = deferred();
            const trackStops = [0, 0];
            const controlStops = [0, 0];
            const streams = [0, 1].map(index => ({
                id: index,
                getTracks() {
                    return [{stop() { trackStops[index] += 1; }}];
                },
            }));
            const controls = [0, 1].map(index => ({
                stop() {
                    controlStops[index] += 1;
                    element('inventoryCameraVideo').srcObject = null;
                },
            }));
            let readerIndex = 0;
            class FakeReader {
                decodeFromConstraints(_constraints, video) {
                    const index = readerIndex++;
                    video.srcObject = streams[index];
                    return index === 0 ? firstReady.promise : Promise.resolve(controls[index]);
                }
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                ZXingBrowser: {BrowserMultiFormatReader: FakeReader},
                document: {hidden: false, addEventListener() {}, getElementById: element},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                streams, controls,
            };
            context.window = context;
            context.window.isSecureContext = true;
            context.window.location = {hostname: 'inventory.example.test'};
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
            `, context);
            (async () => {
                const firstStart = vm.runInContext('startInventoryCamera()', context);
                assert.equal(element('inventoryCameraVideo').srcObject, streams[0]);
                vm.runInContext('stopInventoryCamera()', context);

                const secondStart = vm.runInContext('startInventoryCamera()', context);
                await Promise.resolve();
                firstReady.resolve(controls[0]);
                await Promise.all([firstStart, secondStart]);
                assert.equal(controlStops[0], 1);
                assert.equal(controlStops[1], 0);
                assert.equal(trackStops[1], 0);
                assert.equal(element('inventoryCameraVideo').srcObject, streams[1]);
                assert.equal(vm.runInContext('inventoryCameraControls === controls[1]', context), true);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_camera_requires_secure_context(self):
        self.run_node(
            r"""
            let readerCount = 0;
            const serialInput = {disabled: false, focus() {}};
            const elements = new Map([
                ['inventorySerialInput', serialInput],
                ['inventoryCameraStart', {disabled: false}],
                ['inventoryCameraStop', {disabled: true}],
                ['inventoryCameraPanel', {hidden: true}],
                ['inventoryCameraVideo', {srcObject: null}],
                ['inventoryCameraMessage', {textContent: '', className: ''}],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                ZXingBrowser: {BrowserMultiFormatReader: class { constructor() { readerCount += 1; }}},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) { return elements.get(id); },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                readerCount,
            };
            context.window = context;
            context.window.isSecureContext = false;
            context.window.location = {hostname: 'inventory.example.test'};
            vm.createContext(context);
            vm.runInContext(source, context);
            (async () => {
                await vm.runInContext('startInventoryCamera()', context);
                assert.match(elements.get('inventoryCameraMessage').textContent, /需要 HTTPS/);
                assert.equal(readerCount, 0);
                assert.equal(elements.get('inventoryCameraPanel').hidden, true);
                assert.equal(elements.get('inventoryCameraStart').disabled, false);
                assert.equal(serialInput.disabled, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_scan_failure_hides_raw_backend_error(self):
        self.run_node(
            r"""
            const input = {value: 'SN-PRIVATE', disabled: false, focus() {}};
            const message = {textContent: '', className: ''};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialInput') return input;
                        if (id === 'inventorySerialMessage') return message;
                        return {disabled: false, textContent: '', className: ''};
                    },
                },
                fetch: async () => ({
                    ok: false, status: 502,
                    json: async () => ({success: false, error: 'SENTINEL_PRIVATE_GYJ_ERROR'}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
            `, context);
            (async () => {
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                assert.equal(message.textContent, '扫码保存失败，请重试。');
                assert.doesNotMatch(message.textContent, /SENTINEL_PRIVATE_GYJ_ERROR/);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_duplicate_serial_scan_updates_duplicate_count_and_detail(self):
        self.run_node(
            r"""
            const input = {value: 'SN-DUP', focus() {}};
            let rendered = null;
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialInput') return input;
                        return {textContent: '', className: ''};
                    },
                },
                fetch: async () => ({
                    ok: true, status: 200,
                    json: async () => ({success: true, scan: {
                        serial: 'SN-DUP', classification: 'duplicate', lookup_name: '滤芯',
                    }}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
                rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                currentSerialData = {
                    counts: {matched: 0, system_only: 0, physical_only: 0, other_product: 0, duplicates: 0},
                    matched: [], system_only: [], physical_only: [], other_product: [], duplicates: [],
                };
                renderSerialReconciliation = value => { rendered = value; currentSerialData = value; };
            `, context);
            (async () => {
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                const result = vm.runInContext('rendered', context);
                assert.equal(result.duplicates.length, 1);
                assert.equal(result.counts.duplicates, 1);
                assert.equal(result.duplicates[0].serial, 'SN-DUP');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_two_rapid_serial_enters_are_queued_and_submitted_exactly_once(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const firstScan = deferred();
            const submitted = [];
            const input = {value: 'FIRST', focus() {}};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialInput') return input;
                        return {textContent: '', className: ''};
                    },
                },
                fetch: async (url, options = {}) => {
                    if (url.endsWith('/serials')) {
                        const serial = JSON.parse(options.body).serial;
                        submitted.push(serial);
                        if (serial === 'FIRST') return firstScan.promise;
                        return response({success: true, scan: {
                            serial, classification: 'duplicate',
                        }});
                    }
                    return response({success: true, serial: {
                        barcode: 'A1', counts: {matched: 1},
                        matched: [{serial: 'FIRST'}], system_only: [],
                        physical_only: [], other_product: [], duplicates: [],
                    }});
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                currentSerialData = {counts: {}, matched: [], system_only: [], physical_only: [], other_product: [], duplicates: []};
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                const first = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                input.value = 'SECOND';
                const second = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                assert.equal(input.value, '');
                await Promise.resolve();
                assert.deepEqual(submitted, ['FIRST']);
                firstScan.resolve(response({success: true, scan: {
                    serial: 'FIRST', classification: 'matched',
                }}));
                await Promise.all([first, second]);
                assert.deepEqual(submitted, ['FIRST', 'SECOND']);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_refresh_started_before_scan_cannot_replace_newer_scan_result(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const oldRefresh = deferred();
            const requests = [];
            const rendered = [];
            const input = {value: 'NEW', focus() {}};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialInput') return input;
                        return {textContent: '', className: ''};
                    },
                },
                fetch: async (url, options = {}) => {
                    requests.push(url);
                    if (requests.length === 1) return oldRefresh.promise;
                    if (url.endsWith('/serials')) return response({success: true, scan: {
                        serial: 'NEW', classification: 'matched',
                    }});
                    return response({success: true, serial: {marker: 'NEW'}});
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
                renderSerialReconciliation = value => rendered.push(value.marker);
            `, context);
            (async () => {
                const refresh = vm.runInContext('refreshSerialItem()', context);
                const scan = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                oldRefresh.resolve(response({success: true, serial: {marker: 'OLD'}}));
                await Promise.all([refresh, scan]);
                assert.deepEqual(rendered, ['OLD', 'NEW']);
                assert.equal(rendered.at(-1), 'NEW');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_refresh_started_before_delete_cannot_replace_delete_result(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const oldRefresh = deferred();
            const requests = [];
            const rendered = [];
            const input = {focus() {}};
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialInput') return input;
                        return {textContent: '', className: ''};
                    },
                },
                fetch: async (url) => {
                    requests.push(url);
                    if (requests.length === 1) return oldRefresh.promise;
                    return response({success: true, serial: {marker: 'DELETED'}});
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
                renderSerialReconciliation = value => rendered.push(value.marker);
            `, context);
            (async () => {
                const refresh = vm.runInContext('refreshSerialItem()', context);
                const deletion = vm.runInContext("deleteSerialScan('OLD')", context);
                oldRefresh.resolve(response({success: true, serial: {marker: 'OLD'}}));
                await Promise.all([refresh, deletion]);
                assert.deepEqual(rendered, ['OLD', 'DELETED']);
                assert.equal(rendered.at(-1), 'DELETED');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_check_poll_fetches_full_task_for_complete_pending_queue(self):
        self.run_node(
            r"""
            const search = {value: ''};
            const filters = {value: 'pending'};
            const urls = [];
            const rendered = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySearch') return search;
                        if (id === 'inventoryFilters') return filters;
                        return {textContent: '', className: ''};
                    },
                },
                fetch: async url => {
                    urls.push(url);
                    if (url.startsWith('/api/inventory/tasks/active')) return {
                        ok: true, status: 200, json: async () => ({success: true, task: {
                            task_id: 'task-1', phase: 'serial_check', version: 4, summary: {}, items: [],
                        }}),
                    };
                    return {ok: true, status: 200, json: async () => ({success: true, task: {
                        task_id: 'task-1', phase: 'serial_check', version: 4,
                        items: [{barcode: 'ZERO', initial_stock: '0', state: 'serial_pending'}],
                    }})};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('renderInventoryTask = task => rendered.push(task.items.map(item => item.barcode))', context);
            (async () => {
                await vm.runInContext('pollInventoryTask({force: true})', context);
                assert.equal(urls.length, 2);
                assert.equal(urls[1], '/api/inventory/tasks/task-1');
                assert.deepEqual(rendered, [['ZERO']]);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_finish_uses_backend_forced_finish_once_and_keeps_workspace_on_failure(self):
        self.run_node(
            r"""
            const requests = [];
            let focusCount = 0;
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    disabled: false, textContent: '', className: '',
                    focus() { focusCount += 1; },
                });
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    return {
                        ok: false, status: 502,
                        json: async () => ({success: false, error: 'SENTINEL_PRIVATE_GYJ_ERROR'}),
                    };
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A/B';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
            `, context);
            (async () => {
                await vm.runInContext('finishSerialItem()', context);
                assert.equal(requests.length, 1);
                assert.equal(requests[0].url, '/api/inventory/tasks/task-1/items/A%2FB/serial/finish');
                assert.deepEqual(JSON.parse(requests[0].options.body), {
                    device_id: 'device-a',
                });
                assert.equal(vm.runInContext('serialWorkspaceOpen', context), true);
                assert.equal(element('inventorySerialCancel').disabled, false);
                assert.equal(element('inventorySerialInput').disabled, false);
                assert.equal(vm.runInContext('serialFinishPending', context), false);
                assert.equal(
                    element('inventorySerialMessage').textContent,
                    '未能完成核对，请稍后重试。当前扫描界面已保留。',
                );
                assert.doesNotMatch(
                    element('inventorySerialMessage').textContent,
                    /SENTINEL_PRIVATE_GYJ_ERROR/,
                );
                assert.equal(focusCount, 1);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_finish_waits_for_two_accepted_scans_and_preserves_request_order(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const firstScan = deferred();
            const order = [];
            const input = {value: 'FIRST', disabled: false, focus() {}};
            const finishButton = {disabled: false};
            const dialog = {
                open: true,
                close() { this.open = false; },
            };
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', finishButton],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
                        return elements.get(id);
                    },
                },
                fetch: async (url, options = {}) => {
                    if (url.endsWith('/serials')) {
                        const serial = JSON.parse(options.body).serial;
                        order.push(`scan:${serial}`);
                        if (serial === 'FIRST') return firstScan.promise;
                        return response({success: true, scan: {serial, classification: 'duplicate'}});
                    }
                    if (url.endsWith('/serial/finish')) {
                        order.push('finish');
                        return response({success: true, serial: {barcode: 'A1', counts: {}}});
                    }
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1';
                currentSerialData = {counts: {}, duplicates: []};
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                const first = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                input.value = 'SECOND';
                const second = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                const finish = vm.runInContext('finishSerialItem()', context);
                assert.equal(input.disabled, true);
                assert.equal(finishButton.disabled, true);
                await Promise.resolve();
                assert.deepEqual(order, ['scan:FIRST']);

                input.value = 'THIRD';
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                assert.equal(input.value, 'THIRD', 'scan requested after finish must not be consumed');

                firstScan.resolve(response({success: true, scan: {
                    serial: 'FIRST', classification: 'duplicate',
                }}));
                await Promise.all([first, second, finish]);
                assert.deepEqual(order, ['scan:FIRST', 'scan:SECOND', 'finish']);
                assert.equal(order.filter(value => value === 'scan:FIRST').length, 1);
                assert.equal(order.filter(value => value === 'scan:SECOND').length, 1);
                assert.equal(dialog.open, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_stale_refresh_cannot_replace_reopened_serial_workspace(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload, status = 200) {
                return {ok: status >= 200 && status < 300, status, json: async () => payload};
            }
            const oldRefresh = deferred();
            const input = {value: '', disabled: false, focus() {}};
            const finishButton = {disabled: false};
            const message = {textContent: '', className: ''};
            const dialog = {
                open: true,
                close() { this.open = false; },
                showModal() { this.open = true; },
            };
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', finishButton],
                ['inventorySerialMessage', message],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
                        return elements.get(id);
                    },
                },
                fetch: async url => {
                    if (url.endsWith('/items/A1/serial/refresh')) return oldRefresh.promise;
                    if (url.endsWith('/items/B2/serial/open')) return response({
                        success: true, serial: {barcode: 'B2', counts: {}},
                    });
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a';
                inventoryTask = {task_id: 'task-1', phase: 'serial_check', items: [
                    {barcode: 'A1', state: 'serial_pending'},
                    {barcode: 'B2', state: 'serial_pending'},
                ]};
                currentSerialBarcode = 'A1';
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                renderSerialReconciliation = value => { currentSerialData = value; };
            `, context);
            (async () => {
                const refresh = vm.runInContext('refreshSerialItem()', context);
                await Promise.resolve();
                vm.runInContext('closeSerialWorkspace()', context);
                await vm.runInContext("openSerialItem('B2')", context);
                const newMessage = message.textContent;
                oldRefresh.resolve(response({
                    success: true, serial: {barcode: 'A1', counts: {matched: 99}},
                }));
                await refresh;
                assert.equal(vm.runInContext('currentSerialBarcode', context), 'B2');
                assert.equal(vm.runInContext('currentSerialData.barcode', context), 'B2');
                assert.equal(vm.runInContext('serialWorkspaceEditable', context), true);
                assert.equal(input.disabled, false);
                assert.equal(finishButton.disabled, false);
                assert.equal(message.textContent, newMessage);
                assert.match(message.textContent, /可以开始扫描/);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_failed_accepted_scan_blocks_finish_until_that_scan_is_retried(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload, status = 200) {
                return {ok: status >= 200 && status < 300, status, json: async () => payload};
            }
            const failedSecond = deferred();
            const order = [];
            let secondAttempts = 0;
            let focusCount = 0;
            const input = {value: 'FIRST', disabled: false, focus() { focusCount += 1; }};
            const finishButton = {disabled: false};
            const dialog = {open: true, close() { this.open = false; }};
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', finishButton],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
                        return elements.get(id);
                    },
                },
                fetch: async (url, options = {}) => {
                    if (url.endsWith('/serials')) {
                        const serial = JSON.parse(options.body).serial;
                        order.push(`scan:${serial}`);
                        if (serial === 'SECOND' && ++secondAttempts === 1) return failedSecond.promise;
                        return response({success: true, scan: {serial, classification: 'duplicate'}});
                    }
                    if (url.endsWith('/serial/finish')) {
                        order.push('finish');
                        return response({success: true, serial: {barcode: 'A1', counts: {}}});
                    }
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
                currentSerialData = {counts: {}, duplicates: []};
                renderSerialReconciliation = value => { currentSerialData = value; };
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                input.value = 'SECOND';
                const failedScan = vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                const blockedFinish = vm.runInContext('finishSerialItem()', context);
                await Promise.resolve();
                failedSecond.resolve(response({success: false, error: '扫码保存失败'}, 502));
                await Promise.all([failedScan, blockedFinish]);

                assert.deepEqual(order, ['scan:FIRST', 'scan:SECOND']);
                assert.equal(dialog.open, true);
                assert.equal(input.disabled, false);
                assert.equal(finishButton.disabled, false);
                assert.equal(vm.runInContext('serialFinishPending', context), false);
                assert.match(elements.get('inventorySerialMessage').textContent, /扫码保存失败/);
                assert.ok(focusCount > 0);

                input.value = 'SECOND';
                await vm.runInContext("scanSerial({key: 'Enter', preventDefault() {}})", context);
                await vm.runInContext('finishSerialItem()', context);
                assert.deepEqual(order, ['scan:FIRST', 'scan:SECOND', 'scan:SECOND', 'finish']);
                assert.equal(order.filter(value => value === 'scan:FIRST').length, 1);
                assert.equal(dialog.open, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_failed_accepted_delete_blocks_finish_until_delete_is_retried(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload, status = 200) {
                return {ok: status >= 200 && status < 300, status, json: async () => payload};
            }
            const failedDelete = deferred();
            const order = [];
            let deleteAttempts = 0;
            const input = {disabled: false, focus() {}};
            const finishButton = {disabled: false};
            const dialog = {open: true, close() { this.open = false; }};
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', finishButton],
                ['inventorySerialWorkspace', dialog],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
                        return elements.get(id);
                    },
                },
                fetch: async (url, options = {}) => {
                    if (options.method === 'DELETE') {
                        order.push('delete');
                        if (++deleteAttempts === 1) return failedDelete.promise;
                        return response({success: true, serial: {barcode: 'A1', counts: {}}});
                    }
                    if (url.endsWith('/serial/finish')) {
                        order.push('finish');
                        return response({success: true, serial: {barcode: 'A1', counts: {}}});
                    }
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
                renderSerialReconciliation = () => {};
                pollInventoryTask = async () => {};
            `, context);
            (async () => {
                const deletion = vm.runInContext("deleteSerialScan('SN-OLD')", context);
                const blockedFinish = vm.runInContext('finishSerialItem()', context);
                await Promise.resolve();
                failedDelete.resolve(response({
                    success: false, error: 'SENTINEL_PRIVATE_GYJ_ERROR',
                }, 502));
                await Promise.all([deletion, blockedFinish]);

                assert.deepEqual(order, ['delete']);
                assert.equal(dialog.open, true);
                assert.equal(input.disabled, false);
                assert.equal(finishButton.disabled, false);
                assert.match(
                    elements.get('inventorySerialMessage').textContent,
                    /删除扫描记录失败，请重试。/,
                );
                assert.doesNotMatch(
                    elements.get('inventorySerialMessage').textContent,
                    /SENTINEL_PRIVATE_GYJ_ERROR/,
                );

                await vm.runInContext("deleteSerialScan('SN-OLD')", context);
                await vm.runInContext('finishSerialItem()', context);
                assert.deepEqual(order, ['delete', 'delete', 'finish']);
                assert.equal(dialog.open, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_hidden_page_does_not_restart_timer_after_finish_failure(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            function response(payload, status) {
                return {ok: false, status, json: async () => payload};
            }
            const failedFinish = deferred();
            const intervals = [];
            const input = {disabled: false, focus() {}};
            const finishButton = {disabled: false};
            const elements = new Map([
                ['inventorySerialInput', input],
                ['inventorySerialCancel', finishButton],
            ]);
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (!elements.has(id)) elements.set(id, {textContent: '', className: ''});
                        return elements.get(id);
                    },
                },
                fetch: async url => {
                    if (url.endsWith('/serial/finish')) return failedFinish.promise;
                    throw new Error('unexpected request ' + url);
                },
                setTimeout, clearTimeout,
                setInterval(callback, delay) { intervals.push({callback, delay}); return intervals.length; },
                clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryDeviceId = 'device-a'; inventoryTask = {task_id: 'task-1'};
                currentSerialBarcode = 'A1'; serialWorkspaceOpen = true; serialWorkspaceEditable = true;
            `, context);
            (async () => {
                const finish = vm.runInContext('finishSerialItem()', context);
                await Promise.resolve();
                context.document.hidden = true;
                failedFinish.resolve(response({success: false, error: 'GYJ 失败'}, 502));
                await finish;
                assert.equal(intervals.length, 0);
                assert.equal(vm.runInContext('serialWorkspaceOpen', context), true);
                assert.equal(input.disabled, false);
                assert.equal(finishButton.disabled, false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_discrepancy_admin_gating_note_payload_and_encoded_export(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', value: '', disabled: false,
                    children: [], dataset: {},
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener() {}, setAttribute() {},
                };
            }
            const roots = new Map();
            const requests = [];
            const assigned = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (!roots.has(id)) roots.set(id, makeNode('div'));
                        return roots.get(id);
                    },
                },
                window: {location: {assign(url) { assigned.push(url); }}},
                fetch: async (url, options = {}) => {
                    requests.push({url, options});
                    return {ok: true, status: 200, json: async () => ({success: true, note: {
                        id: 2, serial: 'SN/1', note: '已复核', actor: '盘点员', created_at: '2026-09-01T09:10:00',
                    }})};
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const row = {
                id: 9, task_id: 'task-1', barcode: 'A/B', name: '滤芯', serial: 'SN/1',
                kind: 'other_product_serial', state: 'open', notes: [],
            };
            vm.runInContext('renderDiscrepancyRows([globalThis.testRow], "open")',
                Object.assign(context, {testRow: row}));
            function allText(node) {
                return [node.textContent, ...node.children.flatMap(child => allText(child))];
            }
            const compactCard = roots.get('inventoryDifferencesOpen').children[0];
            assert.equal(compactCard.tag, 'details');
            assert.equal(compactCard.open, false);
            assert.equal(compactCard.children[0].tag, 'summary');
            assert.deepEqual(allText(compactCard.children[0]).filter(Boolean), [
                'A/B · 滤芯', 'SN/1',
            ]);
            assert.equal(allText(roots.get('inventoryDifferencesOpen')).includes('归档'), false);
            context.CURRENT_ACCOUNT.is_admin = true;
            vm.runInContext('renderDiscrepancyRows([globalThis.testRow], "open")', context);
            assert.equal(allText(roots.get('inventoryDifferencesOpen')).includes('归档'), true);

            const noteInput = makeNode('input');
            roots.set('inventoryDiscrepancyNote-9-SN%2F1', noteInput);
            noteInput.value = '  已复核  ';
            vm.runInContext(`
                loadDiscrepancies = async () => {};
                inventoryDifferenceState = 'open';
                saveDiscrepancyNote(9, 'SN/1');
            `, context);
            setImmediate(() => {
                const noteRequest = requests.find(request => request.url.endsWith('/notes'));
                assert.deepEqual(JSON.parse(noteRequest.options.body), {
                    note: '已复核', serial: 'SN/1', expected_version: 1,
                });
                context.document.getElementById('inventoryDifferenceSearch').value = 'A/B & SN';
                vm.runInContext("exportDiscrepancies()", context);
                assert.equal(assigned[0], '/api/inventory/discrepancies/export?state=open&query=A%2FB+%26+SN');
            });
            """
        )

    def test_stale_async_tab_response_does_not_render_after_tab_switch(self):
        self.run_node(
            r"""
            function deferred() {
                let resolve;
                const promise = new Promise(done => { resolve = done; });
                return {promise, resolve};
            }
            const pending = deferred();
            const rendered = [];
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    hidden: false, value: '', textContent: '', className: '',
                    classList: {toggle() {}}, setAttribute() {}, replaceChildren() {}, append() {},
                });
                return elements.get(id);
            }
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: url => url.startsWith('/api/inventory/tasks/history?') ? pending.promise : Promise.resolve({
                    ok: true, status: 200, json: async () => ({success: true, discrepancies: []}),
                }),
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('renderInventoryHistory = () => rendered.push("history")',
                Object.assign(context, {rendered}));
            (async () => {
                const history = vm.runInContext("switchInventoryTab('history')", context);
                await Promise.resolve();
                await vm.runInContext("switchInventoryTab('differences')", context);
                pending.resolve({ok: true, status: 200, json: async () => ({success: true, tasks: []})});
                await history;
                assert.deepEqual(rendered, []);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_history_uses_paginated_summary_without_detail_or_discrepancy_fanout(self):
        self.run_node(
            r"""
            function response(payload) {
                return {ok: true, status: 200, json: async () => payload};
            }
            const urls = [];
            const rendered = [];
            const elements = new Map();
            function element(id) {
                if (!elements.has(id)) elements.set(id, {
                    hidden: false, disabled: false, textContent: '', className: '',
                    replaceChildren() {}, append() {},
                });
                return elements.get(id);
            }
            const pages = [
                {success: true, tasks: [{
                    task_id: 'T2', participant_count: 2, product_total: 8,
                    quantity_difference_count: 1, serial_difference_count: 3,
                }], pagination: {limit: 1, offset: 0, total: 2, has_more: true}},
                {success: true, tasks: [{
                    task_id: 'T1', participant_count: 1, product_total: 4,
                    quantity_difference_count: 0, serial_difference_count: 0,
                }], pagination: {limit: 1, offset: 1, total: 2, has_more: false}},
            ];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {hidden: false, addEventListener() {}, getElementById: element},
                fetch: async url => { urls.push(url); return response(pages.shift()); },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {}, rendered,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                inventoryActiveTab = 'history';
                renderInventoryHistory = tasks => rendered.push(tasks.map(task => ({
                    id: task.task_id, participants: task.participant_count,
                    products: task.product_total, quantity: task.quantity_difference_count,
                    serials: task.serial_difference_count,
                })));
            `, context);
            (async () => {
                const generation = vm.runInContext('inventoryTabGeneration', context);
                await vm.runInContext(`loadInventoryHistory(${generation}, true)`, context);
                assert.deepEqual(urls, ['/api/inventory/tasks/history?limit=20&offset=0']);
                assert.deepEqual(rendered[0], [{id: 'T2', participants: 2, products: 8, quantity: 1, serials: 3}]);
                assert.equal(element('inventoryHistoryLoadMore').hidden, false);
                await vm.runInContext(`loadInventoryHistory(${generation}, false)`, context);
                assert.deepEqual(urls, [
                    '/api/inventory/tasks/history?limit=20&offset=0',
                    '/api/inventory/tasks/history?limit=20&offset=1',
                ]);
                assert.deepEqual(rendered[1].map(row => row.id), ['T2', 'T1']);
                assert.equal(element('inventoryHistoryLoadMore').hidden, true);
                assert.equal(urls.some(url => url.includes('/discrepancies')), false);
                assert.equal(urls.some(url => /\/tasks\/T[12]$/.test(url)), false);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_history_renders_numeric_server_summary(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [], href: '',
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener(name, handler) { this[name] = handler; },
                };
            }
            const root = makeNode('div');
            const opened = [];
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (id === 'inventoryHistory') return root;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext('openInventoryHistoryDetail = (taskId, scope) => opened.push([taskId, scope])',
                Object.assign(context, {opened}));
            vm.runInContext(`renderInventoryHistory([{
                task_id: 'T2', task_number: 'PD20260907-081426',
                started_at: 'START', completed_at: 'DONE',
                participant_count: 2, product_total: 8,
                counted_product_count: 6, uncounted_product_count: 2,
                quantity_difference_count: 1, serial_difference_count: 3,
            }])`, context);
            assert.equal(
                root.children[0].children[0].children[0].textContent,
                '盘点任务单号 PD20260907-081426',
            );
            const metrics = root.children[0].children[1].children;
            assert.deepEqual(
                metrics.map(metric => [metric.children[0].textContent, metric.children[1].textContent]),
                [
                    ['参与人数', '2'], ['商品总数', '8'],
                    ['已盘商品', '6'], ['未盘商品', '2'],
                    ['数量差异', '1'], ['序列号差异', '3'],
                ],
            );
            metrics.forEach(metric => metric.click());
            assert.equal(JSON.stringify(opened), JSON.stringify([
                ['T2', 'participants'], ['T2', 'all'],
                ['T2', 'counted'], ['T2', 'uncounted'],
                ['T2', 'quantity'], ['T2', 'serial'],
            ]));
            root.children[0].click({target: {closest() { return null; }}});
            assert.equal(JSON.stringify(opened[6]), JSON.stringify(['T2', undefined]));
            """
        )

    def test_history_detail_renders_quantity_and_serial_archive_states(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [], open: false,
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener() {}, setAttribute() {},
                };
            }
            const roots = new Map();
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (!roots.has(id)) roots.set(id, makeNode('div'));
                        return roots.get(id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`renderInventoryHistoryDetail({
                task: {task_number: 'PD20260907-081426', completed_at: '2026-09-07T08:14:26'},
                items: [{
                    barcode: 'B2', name: '序列号商品', has_serial: true,
                    completed_book_qty: '2', completed_actual_qty: '1', diff_qty: '-1',
                    serial_discrepancies: [
                        {serial: 'SN-OPEN', kind: 'system_only_serial', state: 'open'},
                        {serial: 'SN-DONE', kind: 'physical_only_serial', state: 'archived',
                         archived_by: '管理员', archived_at: '2026-09-07T09:00:00'},
                    ],
                }],
            })`, context);
            function allText(node) {
                return [node.textContent, ...node.children.flatMap(child => allText(child))];
            }
            assert.equal(roots.get('inventoryHistoryDetailTitle').textContent,
                '盘点任务单号 PD20260907-081426');
            const text = allText(roots.get('inventoryHistoryDetailItems')).join(' | ');
            assert.match(text, /账面 2/);
            assert.match(text, /实盘 1/);
            assert.match(text, /差异 -1/);
            assert.match(text, /SN-OPEN.*待归档/);
            assert.match(text, /SN-DONE.*已归档.*管理员.*2026-09-07T09:00:00/);
            """
        )

    def test_history_metric_detail_renders_participants_and_uncounted_items(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [], open: false,
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener() {}, setAttribute() {},
                };
            }
            const roots = new Map();
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (!roots.has(id)) roots.set(id, makeNode('div'));
                        return roots.get(id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            function allText(node) {
                return [node.textContent, ...node.children.flatMap(child => allText(child))];
            }

            vm.runInContext(`renderInventoryHistoryDetail({
                scope: 'participants',
                task: {task_number: 'PD20260907-081426', completed_at: 'DONE'},
                participants: [{
                    actor: '甲', device_id: 'device-a', action_count: 3,
                    first_activity_at: 'START', last_activity_at: 'LAST',
                }],
                items: [],
            })`, context);
            assert.equal(roots.get('inventoryHistoryDetailTitle').textContent,
                '参与人员 · PD20260907-081426');
            let text = allText(roots.get('inventoryHistoryDetailItems')).join(' | ');
            assert.match(text, /甲/);
            assert.match(text, /device-a/);
            assert.match(text, /3 次操作/);
            assert.match(text, /最后操作 LAST/);

            vm.runInContext(`renderInventoryHistoryDetail({
                scope: 'uncounted',
                task: {task_number: 'PD20260907-081426', completed_at: 'DONE'},
                participants: [],
                items: [{
                    barcode: 'D4', name: '未盘商品', has_serial: false,
                    completed_book_qty: '4', completed_actual_qty: null,
                    diff_qty: null, serial_discrepancies: [],
                }],
            })`, context);
            assert.equal(roots.get('inventoryHistoryDetailTitle').textContent,
                '未盘商品 · PD20260907-081426');
            text = allText(roots.get('inventoryHistoryDetailItems')).join(' | ');
            assert.match(text, /D4.*未盘商品/);
            assert.match(text, /状态 未盘/);
            assert.match(text, /账面 4/);
            """
        )

    def test_history_reopen_is_admin_only_and_audit_renders_changes(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [], href: '',
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener(name, handler) { this[name] = handler; },
                };
            }
            const historyRoot = makeNode('div');
            const auditRoot = makeNode('div');
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (id === 'inventoryHistory') return historyRoot;
                        if (id === 'inventoryAuditEvents') return auditRoot;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            const task = `[{task_id: 'T2', started_at: 'START', completed_at: 'DONE',
                participant_count: 2, product_total: 8,
                quantity_difference_count: 1, serial_difference_count: 3}]`;
            vm.runInContext(`renderInventoryHistory(${task})`, context);
            assert.equal(historyRoot.children[0].children[2].children.length, 2);
            context.CURRENT_ACCOUNT.is_admin = true;
            vm.runInContext(`renderInventoryHistory(${task})`, context);
            const actions = historyRoot.children[0].children[2].children;
            assert.equal(actions.length, 3);
            assert.equal(actions[2].textContent, '继续盘点');

            vm.runInContext(`renderInventoryAudit([{
                event_type: 'count_entry_updated', event_label: '修改分次数量',
                actor: '乙', created_at: '2026-09-05T10:00:00',
                before_quantity: '12', after_quantity: '13',
            }])`, context);
            assert.equal(auditRoot.children[0].children[0].textContent, '修改分次数量');
            assert.match(auditRoot.children[0].children[1].textContent, /乙/);
            assert.match(auditRoot.children[0].children[2].textContent, /12.*13/);

            vm.runInContext(`renderInventoryAudit([{
                event_type: 'serial_scan_reclassified', event_label: '重新分类序列号',
                actor: '甲', created_at: '2026-09-05T10:01:00',
                before_quantity: null, after_quantity: null,
                before_serial: 'SN-1', after_serial: 'SN-1',
                before_classification: 'unknown', after_classification: 'matched',
            }])`, context);
            assert.match(auditRoot.children[0].children[2].textContent, /SN-1.*未知.*账实一致/);
            """
        )

    def test_audit_renders_count_entry_number(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [],
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                    addEventListener() {},
                };
            }
            const auditRoot = makeNode('div');
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (id === 'inventoryAuditEvents') return auditRoot;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`renderInventoryAudit([{
                event_type: 'count_entry_updated', event_label: '修改分次数量',
                entry_number: 2, actor: '乙', created_at: '2026-09-06T10:00:00',
                before_quantity: '12', after_quantity: '13',
            }, {
                event_type: 'count_entry_deleted', event_label: '删除分次数量',
                entry_number: null, actor: '甲', created_at: '2026-09-06T10:01:00',
                before_quantity: '13', after_quantity: null,
            }])`, context);
            assert.equal(auditRoot.children[0].children[0].textContent, '修改第 2 笔数量');
            assert.equal(auditRoot.children[1].children[0].textContent, '删除分次数量');
            """
        )

    def test_audit_renders_carton_range_and_affected_serials(self):
        self.run_node(
            r"""
            function makeNode(tag) {
                return {
                    tag, textContent: '', className: '', children: [],
                    append(...nodes) { this.children.push(...nodes); },
                    replaceChildren(...nodes) { this.children = nodes; },
                };
            }
            const auditRoot = makeNode('div');
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                document: {
                    hidden: false, addEventListener() {}, createElement: makeNode,
                    getElementById(id) {
                        if (id === 'inventoryAuditEvents') return auditRoot;
                        throw new Error('unexpected element ' + id);
                    },
                },
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`renderInventoryAudit([{
                event_type: 'carton_created', event_label: '整箱录入',
                actor: '甲', created_at: '2026-09-06T10:00:00',
                start_serial: '1422608126281', end_serial: '1422608126300',
                confirmed_quantity: 20, affected_count: 20,
                serials: ['1422608126281', '1422608126300'],
            }, {
                event_type: 'carton_serial_added', event_label: '箱内补录',
                actor: '乙', created_at: '2026-09-06T10:01:00',
                start_serial: '1422608126281', end_serial: '1422608126301',
                affected_count: 1, serials: ['1422608126301'],
            }, {
                event_type: 'carton_deleted', event_label: '删除整箱',
                actor: '丙', created_at: '2026-09-06T10:02:00',
                start_serial: '1422608126281', end_serial: '1422608126301',
                affected_count: 21, serials: ['1422608126281', '1422608126301'],
            }])`, context);
            assert.equal(auditRoot.children[0].children[0].textContent,
                '整箱录入 · 1422608126281～6300（20条）');
            assert.equal(auditRoot.children[1].children[0].textContent,
                '箱内补录 · 1422608126281～6301');
            assert.match(auditRoot.children[1].children[2].textContent, /1422608126301/);
            assert.equal(auditRoot.children[2].children[0].textContent,
                '删除整箱 · 1422608126281～6301');
            assert.match(auditRoot.children[2].children[2].textContent, /21/);
            const allText = JSON.stringify(auditRoot);
            assert(!allText.includes('carton_id'));
            assert(!allText.includes('details'));
            """
        )

    def test_both_tab_groups_support_roving_keyboard_navigation(self):
        self.run_node(
            r"""
            function makeTab(id, dataset) {
                return {
                    id, dataset, tabIndex: -1, listeners: {}, focused: 0,
                    classList: {toggle() {}},
                    setAttribute(name, value) { this[name] = value; },
                    addEventListener(name, handler) { this.listeners[name] = handler; },
                    focus() { this.focused += 1; },
                };
            }
            const elements = new Map();
            for (const [id, value] of [
                ['inventoryTabCurrent', {inventoryTab: 'current'}],
                ['inventoryTabHistory', {inventoryTab: 'history'}],
                ['inventoryTabDifferences', {inventoryTab: 'differences'}],
                ['inventoryDifferenceTabOpen', {differenceState: 'open'}],
                ['inventoryDifferenceTabArchived', {differenceState: 'archived'}],
            ]) elements.set(id, makeTab(id, value));
            for (const id of [
                'inventoryCurrentRoot', 'inventoryHistoryRoot', 'inventoryDiscrepanciesRoot',
                'inventoryDifferencesOpen', 'inventoryDifferencesArchived',
            ]) elements.set(id, {hidden: false});
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {hidden: false, addEventListener() {}, getElementById: id => elements.get(id)},
                setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                loadInventoryHistory = async () => {};
                loadDiscrepancies = async () => {};
                pollInventoryTask = async () => {};
                closeSerialWorkspace = () => {};
                bindInventoryTablists();
                switchInventoryTab('current');
                switchDifferenceState('open');
            `, context);
            function press(id, key) {
                const tab = elements.get(id);
                let prevented = false;
                tab.listeners.keydown({key, currentTarget: tab, preventDefault() { prevented = true; }});
                assert.equal(prevented, true);
            }
            press('inventoryTabCurrent', 'ArrowRight');
            assert.equal(elements.get('inventoryTabHistory').focused, 1);
            assert.equal(elements.get('inventoryTabHistory').tabIndex, 0);
            press('inventoryTabHistory', 'ArrowLeft');
            assert.equal(elements.get('inventoryTabCurrent').focused, 1);
            press('inventoryTabCurrent', 'ArrowLeft');
            assert.equal(elements.get('inventoryTabDifferences').focused, 1);
            assert.equal(elements.get('inventoryTabDifferences').tabIndex, 0);
            assert.equal(elements.get('inventoryTabCurrent').tabIndex, -1);
            assert.equal(elements.get('inventoryDiscrepanciesRoot').hidden, false);
            press('inventoryTabDifferences', 'Home');
            assert.equal(elements.get('inventoryTabCurrent').focused, 2);
            assert.equal(elements.get('inventoryTabCurrent')['aria-selected'], 'true');
            press('inventoryTabCurrent', 'End');
            assert.equal(elements.get('inventoryTabDifferences').focused, 2);

            press('inventoryDifferenceTabOpen', 'ArrowLeft');
            assert.equal(elements.get('inventoryDifferenceTabArchived').focused, 1);
            assert.equal(elements.get('inventoryDifferenceTabArchived').tabIndex, 0);
            assert.equal(elements.get('inventoryDifferencesArchived').hidden, false);
            press('inventoryDifferenceTabArchived', 'Home');
            assert.equal(elements.get('inventoryDifferenceTabOpen').focused, 1);
            assert.equal(elements.get('inventoryDifferenceTabOpen')['aria-selected'], 'true');
            """
        )


if __name__ == "__main__":
    unittest.main()
