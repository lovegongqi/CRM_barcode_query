import json
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNTS_TEMPLATE = ROOT / "templates" / "accounts.html"


def extract_function(source, name):
    markers = (f"async function {name}(", f"function {name}(")
    start = next((source.index(marker) for marker in markers if marker in source), -1)
    if start < 0:
        raise AssertionError(f"function not found: {name}")
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"function is not closed: {name}")


class AccountsFrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ACCOUNTS_TEMPLATE.read_text(encoding="utf-8")

    def run_node(self, body, functions):
        source = "\n".join(extract_function(self.source, name) for name in functions)
        program = f"""
        const assert = require('node:assert/strict');
        const vm = require('node:vm');
        const source = {json.dumps(source)};
        {body}
        """
        result = subprocess.run(
            ["node", "-e", program],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_captcha_button_stays_locked_after_batch_dispatch_is_accepted(self):
        self.run_node(
            r"""
            const elements = {
                bulkCrmCaptcha: {value: '2468'},
                bulkCaptchaBtn: {disabled: false, textContent: '提交验证码'},
            };
            const context = {
                console,
                document: {getElementById(id) { return elements[id]; }},
                alert(message) { throw new Error(message); },
                currentBulkCrmLoginJobId: 'job-1',
                pendingBulkCrmSlots: [],
                bulkLog() {},
                fetch: async () => ({json: async () => ({
                    success: true,
                    job_id: 'job-1',
                    captcha_received: true,
                    captcha_submitting: true,
                })}),
                renderBulkLoginJob() {},
                startBulkLoginPolling() {},
                loadBulkCrmSlots: async () => {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            (async () => {
                await vm.runInContext('submitBulkCrmCaptcha()', context);
                assert.equal(elements.bulkCaptchaBtn.disabled, true);
                assert.equal(elements.bulkCaptchaBtn.textContent, '提交中...');
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """,
            ["submitBulkCrmCaptcha"],
        )

    def test_job_status_controls_captcha_button_lock(self):
        self.run_node(
            r"""
            const elements = {
                bulkCrmLoginBtn: {disabled: false, textContent: ''},
                bulkCaptchaRow: {style: {}},
                bulkCaptchaBtn: {disabled: false, textContent: '提交验证码'},
                bulkCrmCaptcha: {value: '2468', focus() {}},
            };
            const context = {
                console,
                document: {getElementById(id) { return elements[id]; }},
                sessionStorage: {setItem() {}, removeItem() {}},
                currentBulkCrmLoginJobId: 'job-1',
                lastBulkLoginLogSeq: 0,
                pendingBulkCrmSlots: [],
                bulkLoginPollTimer: null,
                clearInterval() {},
                appendBulkLoginServerLog() {},
                mergeBulkCrmSlotProgress() {},
                loadBulkCrmSlots() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            vm.runInContext(`renderBulkLoginJob({
                success: true, job_id: 'job-1', running: true,
                waiting_captcha: true, captcha_received: true,
                captcha_submitting: true, logs: [], slots: [], pending_slots: [],
            })`, context);
            assert.equal(elements.bulkCaptchaBtn.disabled, true);
            assert.equal(elements.bulkCaptchaBtn.textContent, '提交中...');

            vm.runInContext(`renderBulkLoginJob({
                success: true, job_id: 'job-1', running: true,
                waiting_captcha: true, captcha_received: false,
                captcha_submitting: false, logs: [], slots: [], pending_slots: [],
            })`, context);
            assert.equal(elements.bulkCaptchaBtn.disabled, false);
            assert.equal(elements.bulkCaptchaBtn.textContent, '提交验证码');
            """,
            ["renderBulkLoginJob"],
        )

    def test_settings_contains_centralized_five_channel_gyj_workspace(self):
        self.assertIn('id="gyjChannelCard"', self.source)
        self.assertIn('id="gyjChannelTabs"', self.source)
        self.assertIn('id="gyjLoggedInCount"', self.source)
        self.assertIn("['gyj-1', 'gyj-2', 'gyj-3', 'gyj-4', 'gyj-5']", self.source)
        self.assertIn('id="gyjCaptcha" inputmode="text"', self.source)
        self.assertIn('autocapitalize="off"', self.source)

    def test_settings_login_submits_the_selected_gyj_slot(self):
        self.run_node(
            r"""
            const elements = {
                gyjUsername: {value: 'gyj-user'},
                gyjPassword: {value: 'secret'},
                gyjRememberLogin: {checked: true},
                gyjLoginButton: {disabled: false, textContent: ''},
            };
            let request = null;
            const context = {
                console,
                selectedGyjSlotId: 'gyj-4',
                document: {getElementById(id) { return elements[id]; }},
                fetch: async (url, options) => {
                    request = {url, options};
                    return {json: async () => ({success: true, logged_in: true, slots: []})};
                },
                renderGyjManagementState() {},
                loadGyjChannels: async () => {},
                setGyjMessage() {},
            };
            vm.createContext(context);
            vm.runInContext(source, context);
            (async () => {
                await vm.runInContext('startGyjChannelLogin()', context);
                assert.equal(request.url, '/api/gyj/login');
                assert.equal(JSON.parse(request.options.body).slot_id, 'gyj-4');
                assert.equal(JSON.parse(request.options.body).remember, true);
            })().catch(error => { console.error(error); process.exitCode = 1; });
            """,
            ["startGyjChannelLogin"],
        )


if __name__ == "__main__":
    unittest.main()
