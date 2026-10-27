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
            <div id="transferSummaryTaskCard" hidden><div id="transferSummaryTaskList"></div></div>
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
        openTransferChannelLog('reuse-1');
    }""")

    reuse = page.get_by_role("button", name="复用移库信息")
    assert reuse.count() == 1
    reuse.click()

    assert page.locator("#barcodeInput").input_value() == "435250509H274\n340240520063"
    assert page.locator("#transferDistributor").input_value() == "测试分销商"
    assert page.locator("#transferType").input_value() == "移入"
    assert page.locator("#transferRemark").input_value() == "需要重做"
    assert page.get_by_role("button", name="提交移库").count() == 0
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


def test_two_summaries_continue_independently_with_their_own_details(transfer_page):
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
            summaries: summaryTasks.map(task => task.summary?.groups?.[0]?.product_code)};
    }""")
    assert result["tasks"] == [["乙分销商", "summary-2"], ["甲分销商", "summary-1"]]
    assert result["previewEnabled"]
    assert result["summaries"] == ["summary-2", "summary-1"]
    assert page.locator("#transferSummaryTaskList").get_by_text("甲分销商").count() == 1
    assert page.locator("#transferSummaryTaskList").get_by_text("乙分销商").count() == 1
    assert page.locator("#barcodeInput").input_value() == "CODE2"
    assert page.get_by_role("button", name="提交移库").count() == 2
    page.get_by_role("button", name="展开明细").last.click()
    assert page.locator("#summary-summary-1 .summary-product-quantity").inner_text() == "数量 1"


def test_editing_form_does_not_change_completed_task(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', remark: '', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        renderSummaryTasks();
    }""")
    assert page.get_by_role("button", name="提交移库").is_enabled()
    page.locator("#barcodeInput").fill("DIFFERENT")
    assert page.get_by_role("button", name="提交移库").is_enabled()


def test_delete_completed_summary_task_removes_it_without_touching_transfer_history(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        summaryTasks = [{job_id: 'summary-done', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', remark: '', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        transferRealtimeRecords = [{record_id: 'transfer-1', distributor: '甲分销商'}];
        saveSummaryTasks();
        renderSummaryTasks();
    }""")
    assert page.get_by_role("button", name="提交移库").is_enabled()
    page.get_by_role("button", name="删除汇总任务").click()
    assert page.locator("#transferSummaryTaskCard").is_hidden()
    assert page.locator("#barcodeInput").input_value() == ""
    assert page.evaluate("summaryTasks.length") == 0
    assert page.evaluate("transferRealtimeRecords.length") == 1
    assert page.evaluate("JSON.parse(sessionStorage.getItem('crm_transfer_summary_tasks_v1')).length") == 0


def test_running_summary_task_cannot_be_deleted(transfer_page):
    page = transfer_page
    page.evaluate("""() => {
        summaryTasks = [{job_id: 'summary-running', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', done: false}];
        renderSummaryTasks();
    }""")
    assert page.get_by_role("button", name="删除汇总任务").count() == 0
    assert page.get_by_role("button", name="提交移库").is_disabled()
    assert page.evaluate("summaryTasks.length") == 1


def test_summary_task_copy_barcodes_on_http_page(transfer_page):
    result = transfer_page.evaluate("""async () => {
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1',
            barcodes: ['CODE1', 'CODE2'], distributor: '甲分销商', transfer_type: '移出', done: false}];
        renderSummaryTasks();
        document.execCommand = command => {
            window.copied = command === 'copy' ? document.activeElement.value : '';
            return true;
        };
        await copySummaryBarcodes('summary-1');
        return {copied: window.copied, form: document.getElementById('barcodeInput').value};
    }""")
    assert result == {"copied": "CODE1\nCODE2", "form": ""}


def test_failed_submit_leaves_task_available_for_retry(transfer_page):
    result = transfer_page.evaluate("""async () => {
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        ensureCrmLoggedIn = async () => true;
        fetch = async () => ({json: async () => ({success: false, error: '通道忙'})});
        await submitTransferTask('summary-1');
        return {submitting: summaryTasks[0].submitting,
            submitted: summaryTasks[0].submitted_job_id || '',
            disabled: document.querySelector('#transferSummaryTaskList button[onclick^="submitTransferTask"]').disabled};
    }""")
    assert result == {"submitting": False, "submitted": "", "disabled": False}


def test_accepted_transfer_removes_only_its_summary_task(transfer_page):
    result = transfer_page.evaluate("""async () => {
        summaryTasks = [
            {job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['CODE1'],
                distributor: '甲分销商', transfer_type: '移出', done: true,
                summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}},
            {job_id: 'summary-2', slot_id: 'transfer-2', barcodes: ['CODE2'],
                distributor: '乙分销商', transfer_type: '移出', done: true,
                summary_success: true, summary: {groups: [{product_code: 'P2'}], details: []}}
        ];
        selectedSummaryTaskId = 'summary-1';
        document.getElementById('barcodeInput').value = 'NEXT-ORDER';
        saveSummaryTasks();
        renderSummaryTasks();
        ensureCrmLoggedIn = async () => true;
        fetch = async () => ({json: async () => ({success: true, slot_id: 'transfer-1', job_id: 'job-1'})});
        startTransferPolling = () => {};
        await submitTransferTask('summary-1');
        return {tasks: summaryTasks.map(task => task.job_id),
            stored: JSON.parse(sessionStorage.getItem('crm_transfer_summary_tasks_v1')).map(task => task.job_id),
            record: transferRealtimeRecords[0].job_id,
            selected: selectedSummaryTaskId,
            form: document.getElementById('barcodeInput').value};
    }""")
    assert result == {"tasks": ["summary-2"], "stored": ["summary-2"],
                      "record": "job-1", "selected": "", "form": "NEXT-ORDER"}


def test_double_click_starts_only_one_transfer(transfer_page):
    result = transfer_page.evaluate("""async () => {
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['CODE1'],
            distributor: '甲分销商', transfer_type: '移出', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        let allowLogin;
        ensureCrmLoggedIn = () => new Promise(resolve => {allowLogin = resolve;});
        let posts = 0;
        fetch = async () => {
            posts++;
            return {json: async () => ({success: true, slot_id: 'transfer-1', job_id: 'job-1'})};
        };
        startTransferPolling = () => {};
        const first = submitTransferTask('summary-1');
        const second = submitTransferTask('summary-1');
        allowLogin(true);
        await Promise.all([first, second]);
        return {posts, remaining: summaryTasks.length};
    }""")
    assert result == {"posts": 1, "remaining": 0}


def test_submit_lets_server_assign_channel_and_updates_record(transfer_page):
    result = transfer_page.evaluate("""async () => {
        document.getElementById('barcodeInput').value = 'NEXT-ORDER';
        document.getElementById('transferDistributor').value = '下一单分销商';
        summaryTasks = [{job_id: 'summary-1', slot_id: 'transfer-1', barcodes: ['435250509H274'],
            distributor: '测试分销商', transfer_type: '移入', remark: '原单备注', done: true,
            summary_success: true, summary: {groups: [{product_code: 'P1'}], details: []}}];
        renderSummaryTasks();
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
        await submitTransferTask('summary-1');
        return {
            sentPayload,
            slot: currentTransferSlot,
            started: window.startedTransfer,
            record: transferRealtimeRecords[0],
            form: document.getElementById('barcodeInput').value,
            remaining: summaryTasks.length
        };
    }""")

    assert "slot_id" not in result["sentPayload"]
    assert result["slot"] == "transfer-4"
    assert result["started"][0:2] == ["transfer-4", "transfer-job-1"]
    assert result["record"]["slot_id"] == "transfer-4"
    assert result["record"]["slot_label"] == "移库4"
    assert result["record"]["barcodes"] == ["435250509H274"]
    assert result["sentPayload"]["barcodes"] == ["435250509H274"]
    assert result["sentPayload"]["distributor"] == "测试分销商"
    assert result["sentPayload"]["transfer_type"] == "移入"
    assert result["sentPayload"]["remark"] == "原单备注"
    assert result["form"] == "NEXT-ORDER"
    assert result["remaining"] == 0
