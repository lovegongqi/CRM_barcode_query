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


if __name__ == "__main__":
    unittest.main()
