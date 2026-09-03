import pathlib
import subprocess
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "static" / "inventory.js"


class InventoryFrontendBehaviorTests(unittest.TestCase):
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

    def test_non_lock_409_does_not_invent_a_lock_owner(self):
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
                assert.equal(element('inventoryActualQuantity').disabled, true);
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
                assert.deepEqual(JSON.parse(requests[0].options.body), {device_id: 'device-a', serial: 'SN/1'});
                await vm.runInContext("deleteSerialScan('SN/1')", context);
                const deletion = requests.find(request => request.options.method === 'DELETE');
                assert.equal(deletion.url, '/api/inventory/tasks/task-1/items/A%2FB/serials/SN%2F1');
                assert.deepEqual(JSON.parse(deletion.options.body), {device_id: 'device-a'});
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """
        )

    def test_serial_workspace_refresh_timer_is_cleaned_on_close_and_tab_switch(self):
        self.run_node(
            r"""
            const intervals = [];
            const cleared = [];
            const refreshEvents = [];
            const dialog = {
                open: true, showModal() { this.open = true; }, close() { this.open = false; },
            };
            const context = {
                console, URLSearchParams, encodeURIComponent, BigInt, Uint8Array,
                CURRENT_ACCOUNT: {is_admin: false},
                document: {
                    hidden: false, addEventListener() {},
                    getElementById(id) {
                        if (id === 'inventorySerialWorkspace') return dialog;
                        return {hidden: false, classList: {toggle() {}}, setAttribute() {}};
                    },
                },
                setTimeout, clearTimeout,
                setInterval(callback, delay) { intervals.push({callback, delay}); return intervals.length; },
                clearInterval(id) { cleared.push(id); },
                refreshEvents,
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`
                serialWorkspaceOpen = true;
                serialWorkspaceEditable = true;
                currentSerialBarcode = 'A1';
                sendSerialHeartbeat = async () => { refreshEvents.push('heartbeat'); };
                refreshSerialItem = async () => { refreshEvents.push('refresh'); };
                startSerialRefreshTimer();
            `, context);
            (async () => {
                assert.equal(intervals[0].delay, 60000);
                await intervals[0].callback();
                assert.deepEqual(refreshEvents, ['heartbeat', 'refresh']);
                vm.runInContext("closeSerialWorkspace()", context);
                assert.deepEqual(cleared, [1]);
                assert.equal(vm.runInContext('serialRefreshTimer', context), null);
                vm.runInContext(`
                    serialWorkspaceOpen = true;
                    currentSerialBarcode = 'A1';
                    startSerialRefreshTimer();
                    loadInventoryHistory = async () => {};
                    switchInventoryTab('history');
                `, context);
                assert.deepEqual(cleared, [1, 2]);
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
                        json: async () => ({success: false, error: 'GYJ 库存读取失败'}),
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
                assert.deepEqual(JSON.parse(requests[0].options.body), {device_id: 'device-a'});
                assert.equal(vm.runInContext('serialWorkspaceOpen', context), true);
                assert.equal(element('inventorySerialFinish').disabled, false);
                assert.match(element('inventorySerialMessage').textContent, /当前扫描界面已保留/);
                assert.equal(focusCount, 1);
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
                assert.deepEqual(JSON.parse(noteRequest.options.body), {note: '已复核', serial: 'SN/1'});
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
                fetch: url => url === '/api/inventory/tasks/history' ? pending.promise : Promise.resolve({
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


if __name__ == "__main__":
    unittest.main()
