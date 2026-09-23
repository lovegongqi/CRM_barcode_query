from pathlib import Path

from playwright.sync_api import sync_playwright


def test_service_close_records_form_one_newest_first_list():
    source = (Path(__file__).resolve().parents[1] / "templates/service_close.html").read_text()
    render_rows = source[source.index("const escapeHtml"):source.index("function fieldValue")]
    render_records = source[source.index("function renderRecords"):source.index("async function pollCurrentJob")]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content('<span id="serviceCloseLatestSummary"></span><div id="serviceCloseRecordList"></div>')
        page.add_script_tag(content="const canManageHistory = true; let historyRecords = [], currentCloseRecord = null;" + render_rows + render_records)
        page.evaluate("""async () => {
            const record = (id, finishedAt, serviceNos) => ({
                id, actor: 'admin', finished_at: finishedAt, closed_count: serviceNos.length,
                failed_count: 0, service_rows: serviceNos.map(service_no => ({service_no, state: 'closed'}))
            });
            window.fetch = async () => ({json: async () => ({records: [
                record('older', '2026-09-23 09:27:41', ['FWD-OLD']),
                record('newer', '2026-09-23 12:39:49', ['FWD-NEW-1', 'FWD-NEW-2'])
            ]})});
            await loadHistory();
        }""")

        rows = page.locator("#serviceCloseRecordList > .service-close-status-row")
        assert rows.count() == 3
        assert rows.locator(".service-close-order-link").all_text_contents() == [
            "FWD-NEW-1", "FWD-NEW-2", "FWD-OLD"
        ]
        assert page.locator("#serviceCloseRecordList .service-close-record").count() == 0
        assert page.locator("#serviceCloseLatestSummary").inner_text() == "本次批量结单：新结单2/失败0"
        browser.close()
