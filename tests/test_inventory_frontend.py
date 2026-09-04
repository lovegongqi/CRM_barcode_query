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
                sendSerialHeartbeat = async () => { refreshEvents.push('heartbeat'); return true; };
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
                assert.equal(element('inventorySerialInput').disabled, false);
                assert.equal(vm.runInContext('serialFinishPending', context), false);
                assert.match(element('inventorySerialMessage').textContent, /当前扫描界面已保留/);
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
                ['inventorySerialFinish', finishButton],
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

    def test_stale_heartbeat_409_cannot_disable_reopened_serial_workspace(self):
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
            const oldHeartbeat = deferred();
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
                ['inventorySerialFinish', finishButton],
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
                    if (url.endsWith('/items/A1/heartbeat')) return oldHeartbeat.promise;
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
                renderSerialReconciliation = () => {};
            `, context);
            (async () => {
                const heartbeat = vm.runInContext('sendSerialHeartbeat()', context);
                vm.runInContext('closeSerialWorkspace()', context);
                await vm.runInContext("openSerialItem('B2')", context);
                const newMessage = message.textContent;
                oldHeartbeat.resolve(response({
                    success: false, error: '商品已被其他设备锁定', lock_owner: '旧设备',
                }, 409));
                await heartbeat;
                assert.equal(vm.runInContext('currentSerialBarcode', context), 'B2');
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
                ['inventorySerialFinish', finishButton],
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
                ['inventorySerialFinish', finishButton],
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
                failedDelete.resolve(response({success: false, error: '删除失败'}, 502));
                await Promise.all([deletion, blockedFinish]);

                assert.deepEqual(order, ['delete']);
                assert.equal(dialog.open, true);
                assert.equal(input.disabled, false);
                assert.equal(finishButton.disabled, false);
                assert.match(elements.get('inventorySerialMessage').textContent, /删除失败/);

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
                ['inventorySerialFinish', finishButton],
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
                    addEventListener() {},
                };
            }
            const root = makeNode('div');
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
            vm.runInContext(`renderInventoryHistory([{
                task_id: 'T2', started_at: 'START', completed_at: 'DONE',
                participant_count: 2, product_total: 8,
                quantity_difference_count: 1, serial_difference_count: 3,
            }])`, context);
            const metrics = root.children[0].children[1].children;
            assert.deepEqual(
                metrics.map(metric => [metric.children[0].textContent, metric.children[1].textContent]),
                [['参与人数', '2'], ['商品总数', '8'], ['数量差异', '1'], ['序列号差异', '3']],
            );
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
