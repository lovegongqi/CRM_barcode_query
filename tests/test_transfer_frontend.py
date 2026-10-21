from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def transfer_page():
    root = Path(__file__).resolve().parents[1]
    source = (root / "templates/transfer.html").read_text(encoding="utf-8")
    inline_script = source[source.rindex("<script>") + len("<script>"):source.rindex("</script>")]
    inline_script = inline_script.split("(async function initPage()", 1)[0]
    inline_script = inline_script.replace("{{ business_config|tojson }}", "{}")
    inline_script = inline_script.replace(
        "{{ 'true' if account and account.is_admin else 'false' }}", "true"
    )
    aurora_script = (root / "static/aurora.js").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        script_errors = []
        page.on("pageerror", lambda error: script_errors.append(str(error)))
        markup = """
            <textarea id="barcodeInput" oninput="saveTransferInput()"></textarea>
            <input id="transferDistributor" oninput="saveTransferInput()">
            <select id="transferType" onchange="saveTransferInput()"><option value="移出">移出</option><option value="移入">移入</option></select>
            <input id="transferRemark" oninput="saveTransferInput()">
            <button id="previewBtn">汇总预览</button>
            <button id="submitBtn">提交移库</button>
            <div id="transferSummaryTaskCard" hidden><div id="transferSummaryTaskList"></div></div>
            <div id="transferSummaryCard"><button id="transferSummaryToggle"></button><div id="summaryBox"></div><span id="transferSummaryTotal"></span></div>
            <div id="previewLog"></div>
            <div id="transferLog">等待操作...</div>
            <table><tbody id="transferRealtimeRows"></tbody></table>
        """
        page.route(
            "http://transfer.test/",
            lambda route: route.fulfill(
                body=markup,
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
        )
        page.goto("http://transfer.test/")
        page.add_script_tag(content=aurora_script)
        page.add_script_tag(content=inline_script)
        assert not script_errors, script_errors
        yield page
        browser.close()


def test_log_reuse_restores_transfer_form_without_submitting(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        transferRealtimeRecords = [{
            record_id: 'reuse-1', slot_id: 'transfer-2', slot_label: '移库2',
            order_no: 'TRSF100', state: 'success', status: '移库成功',
            distributor: '测试分销商', transfer_type: '移入', remark: '需要重做',
            barcodes: ['435250509H274', '340240520063'],
            started_at: '2026-09-29 10:43:25', finished_at: '2026-09-29 10:44:00',
            logs: [{time: '10:44:00', message: '移库完成', level: 'success'}]
        }];
        lastSummary = {groups: [{product_code: 'OLD'}]};
        document.getElementById('submitBtn').disabled = false;
        openTransferChannelLog('reuse-1');
    }""")

    reuse = page.get_by_role("button", name="复用移库信息")
    assert reuse.count() == 1
    reuse.click()

    assert page.locator("#barcodeInput").input_value() == "435250509H274\n340240520063"
    assert page.locator("#transferDistributor").input_value() == "测试分销商"
    assert page.locator("#transferType").input_value() == "移入"
    assert page.locator("#transferRemark").input_value() == "需要重做"
    assert page.locator("#submitBtn").is_disabled()
    assert page.evaluate("lastSummary") is None
    assert not page.locator("#auroraLogDialog").evaluate("element => element.classList.contains('show')")


def test_legacy_record_without_barcodes_has_no_reuse_action(transfer_page):
    transfer_page.evaluate("""() => {
        transferRealtimeRecords = [{
            record_id: 'legacy-1', slot_id: 'transfer-1', slot_label: '移库1',
            state: 'error', status: '移库失败', distributor: '旧记录', logs: []
        }];
        openTransferChannelLog('legacy-1');
    }""")

    assert transfer_page.get_by_role("button", name="复用移库信息").count() == 0


def test_realtime_record_shows_actor_above_submission_time(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        transferRealtimeRecords = [{
            record_id: 'record-1', slot_id: 'transfer-1', slot_label: '移库1',
            order_no: 'TRSF100', state: 'success', status: '移库成功',
            distributor: '测试分销商', actor: 'admin',
            started_at: '2026-09-29 10:43:25', finished_at: '2026-09-29 10:44:00',
            logs: []
        }];
        renderTransferRealtimeRecords();
    }""")

    operator = page.locator("#transferRealtimeRows .transfer-record-operator")
    assert operator.locator("strong").inner_text() == "admin"
    assert operator.locator("small").inner_text() == "2026/09/29"


def test_preview_lets_server_assign_an_idle_transfer_channel(transfer_page):
    result = transfer_page.evaluate("""async () => {
        document.getElementById('barcodeInput').value = '435250509H274';
        ensureCrmLoggedIn = async () => true;
        let sentPayload = null;
        startSummaryPolling = (slotId, jobId) => { window.startedSummary = [slotId, jobId]; };
        fetch = async (url, options) => {
            sentPayload = JSON.parse(options.body);
            return {json: async () => ({success: true, slot_id: 'transfer-3', job_id: 'summary-1'})};
        };
        await previewTransfer();
        return {sentPayload, slot: currentTransferSlot, started: window.startedSummary};
    }""")

    assert "slot_id" not in result["sentPayload"]
    assert result["slot"] == "transfer-3"
    assert result["started"] == ["transfer-3", "summary-1"]


def test_two_summaries_continue_independently_and_reuse_restores_correct_order(transfer_page):
    page = transfer_page
    result = page.evaluate("""async () => {
        ensureCrmLoggedIn = async () => true;
        let next = 0;
        fetch = async (url, options) => {
            if (url === '/api/transfer/summary/start') {
                next++;
                return {json: async () => ({success: true, slot_id: `transfer-${next}`, job_id: `summary-${next}`})};
            }
            const jobId = new URL(url, location.href).searchParams.get('job_id');
            const done = jobId === 'summary-2' || window.firstComplete;
            return {json: async () => ({job_id: jobId, running: !done,
                done, summary_success: done,
                summary: done ? {groups: [{product_code: jobId, quantity: 1}], details: []} : null,
                logs: []})};
        };
        document.getElementById('barcodeInput').value = 'CODE1';
        document.getElementById('transferDistributor').value = '甲分销商';
        await previewTransfer();
        clearForm();
        document.getElementById('barcodeInput').value = 'CODE2';
        document.getElementById('transferDistributor').value = '乙分销商';
        await previewTransfer();
        await pollSummaryStatus('transfer-2', 'summary-2');
        window.firstComplete = true;
        await pollSummaryStatus('transfer-1', 'summary-1');
        return {tasks: summaryTasks.map(task => [task.distributor, task.job_id]),
            previewEnabled: !document.getElementById('previewBtn').disabled,
            submitEnabled: !document.getElementById('submitBtn').disabled,
            currentSummary: lastSummary.groups[0].product_code};
    }""")
    assert result["tasks"] == [["乙分销商", "summary-2"], ["甲分销商", "summary-1"]]
    assert result["previewEnabled"]
    assert result["submitEnabled"]
    assert result["currentSummary"] == "summary-2"
    assert page.locator("#transferSummaryTaskList").get_by_text("甲分销商").count() == 1
    assert page.locator("#transferSummaryTaskList").get_by_text("乙分销商").count() == 1
    page.get_by_role("button", name="复用条码").last.click()
    assert page.locator("#barcodeInput").input_value() == "CODE1"
    assert page.locator("#transferDistributor").input_value() == "甲分销商"
    assert page.locator("#submitBtn").is_enabled()


def test_editing_reused_summary_detaches_submit(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', remark: '', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        renderSummaryTasks();
        reuseSummaryTask('summary-1');
    }""")
    assert page.locator("#submitBtn").is_enabled()
    page.locator("#barcodeInput").fill("DIFFERENT")
    assert page.locator("#submitBtn").is_disabled()
    assert page.evaluate("lastSummary") is None


def test_submit_lets_server_assign_channel_and_updates_record(transfer_page):
    result = transfer_page.evaluate("""async () => {
        document.getElementById('barcodeInput').value = '435250509H274';
        document.getElementById('transferDistributor').value = '测试分销商';
        lastSummary = {groups: [{product_code: 'P1'}]};
        ensureCrmLoggedIn = async () => true;
        transferSlots = [{id: 'transfer-4', label: '移库4', logged_in: true}];
        let sentPayload = null;
        startTransferPolling = (slotId, jobId, recordId) => {
            window.startedTransfer = [slotId, jobId, recordId];
        };
        fetch = async (url, options) => {
            sentPayload = JSON.parse(options.body);
            return {json: async () => ({success: true, slot_id: 'transfer-4', job_id: 'transfer-job-1'})};
        };
        await submitTransfer();
        return {
            sentPayload,
            slot: currentTransferSlot,
            started: window.startedTransfer,
            record: transferRealtimeRecords[0]
        };
    }""")

    assert "slot_id" not in result["sentPayload"]
    assert result["slot"] == "transfer-4"
    assert result["started"][0:2] == ["transfer-4", "transfer-job-1"]
    assert result["record"]["slot_id"] == "transfer-4"
    assert result["record"]["slot_label"] == "移库4"
    assert result["record"]["barcodes"] == ["435250509H274"]
