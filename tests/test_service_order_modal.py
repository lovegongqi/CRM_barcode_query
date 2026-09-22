from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def modal_page():
    source = (Path(__file__).resolve().parents[1] / "templates/index.html").read_text()
    globals_source = source[source.index("let serviceDetailCurrentServiceNo"):source.index("function showToast")]
    escape_source = source[source.index("function escapeHtml"):source.index("function buildFilterUI")]
    functions = source[source.index("function renderServiceOrderDetail"):source.index("function hideDetail")]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content('''<div id="detailModal"><span id="modalTitle"></span>
            <div id="serviceDetailContent"></div><iframe id="detailFrame"></iframe></div>''')
        page.add_script_tag(content=globals_source + escape_source + functions + """
            window.toasts = [];
            function showToast(text) { toasts.push(text); }
            window.pending = [];
            window.requests = [];
            window.jobStatus = {running: false, done: false, success: false};
            window.fetch = (url, options) => {
                requests.push(url);
                if (url.includes('/order-products/status')) {
                    return Promise.resolve({ok: true, json: async () => window.jobStatus});
                }
                return new Promise(resolve => pending.push({url, resolve}));
            };
            window.respond = (index, data, deferJson = false) => {
                pending[index].resolve({ok: true, status: 200, json: () => deferJson
                    ? new Promise(resolve => pending[index].resolveJson = resolve)
                    : Promise.resolve(data)});
            };
            window.detail = (name) => ({service_no: 'FWD100', products: [
                {product_name: name, product_code: 'A', barcode: 'SN1'}
            ], order_lookup: {order_no: 'SO-OLD', queried_at: '2026-09-14 10:00:00', comparison: [
                {product_code: 'A', order_quantity: 1, service_quantity: 1, status: 'matched', status_label: '一致'}
            ]}});
            window.openModal = () => showServiceOrderDetail('FWD100', '/api/service-orders/FWD100');
        """)
        yield page
        browser.close()


def open_loaded_modal(page, name="CURRENT"):
    page.evaluate("() => { window.currentOpen = openModal(); }")
    page.evaluate("name => respond(pending.length - 1, detail(name))", name)
    page.evaluate("() => window.currentOpen")


@pytest.fixture
def service_close_detail_page():
    source = (Path(__file__).resolve().parents[1] / "templates/service_close.html").read_text()
    styles = source[source.index("<style>") + len("<style>"):source.index("</style>")]
    escape_source = source[source.index("const escapeHtml"):source.index("const closeState")]
    functions = source[source.index("function fieldValue"):source.index("async function showServiceOrderDetail")]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 430, "height": 932})
        page.set_content('<div id="serviceDetailContent" class="service-detail-content"></div>')
        page.add_style_tag(content=styles)
        page.add_script_tag(content=escape_source + functions)
        page.evaluate("""() => renderServiceOrderDetail({
            service_no: 'FWD202609201457',
            fields: [
                {label: '客户姓名', value: '张总'},
                {label: '联系电话', value: '18779725092'},
                {label: '联系地址', value: '江西省 赣州市 瑞金市 豪门旺族5-2-1603'},
                {label: '受理时间', value: '2026-09-20 17:20:08'},
                {label: '客户预约时间', value: '2026-09-21 16:00:00'},
                {label: '服务人员', value: '邓雅文'}
            ],
            products: [],
            order_lookup: {}
        })""")
        yield page
        browser.close()


def test_mobile_customer_name_and_phone_share_one_row(service_close_detail_page):
    fields = service_close_detail_page.locator(
        ".service-detail-section:first-child .service-detail-field"
    )
    name_box = fields.nth(0).bounding_box()
    phone_box = fields.nth(1).bounding_box()
    address_box = fields.nth(2).bounding_box()

    assert abs(name_box["y"] - phone_box["y"]) < 2
    assert address_box["y"] > name_box["y"]
    assert address_box["width"] > name_box["width"] * 1.8


def test_mobile_service_information_defaults_collapsed_and_expands_on_tap(service_close_detail_page):
    service = service_close_detail_page.locator(".service-detail-service")

    assert service.count() == 1
    assert service.evaluate("element => element.tagName") == "DETAILS"
    assert not service.evaluate("element => element.open")
    assert not service.locator(".service-detail-fields").is_visible()

    service.locator("summary").click()

    assert service.evaluate("element => element.open")
    assert service.locator(".service-detail-fields").is_visible()


@pytest.mark.parametrize("boundary", ["fetch", "json"])
def test_late_initial_detail_cannot_replace_same_service_reopened_modal(modal_page, boundary):
    page = modal_page
    page.evaluate("() => { window.oldOpen = openModal(); }")
    if boundary == "json":
        page.evaluate("respond(0, null, true)")
        page.wait_for_function("Boolean(pending[0].resolveJson)")
    page.evaluate("closeDetailModal()")
    open_loaded_modal(page)
    request_count = page.evaluate("requests.length")

    page.evaluate("boundary => boundary === 'json' ? pending[0].resolveJson(detail('STALE')) : respond(0, detail('STALE'))", boundary)
    page.evaluate("() => window.oldOpen")

    assert "CURRENT" in page.locator("#serviceDetailContent").inner_text()
    assert "STALE" not in page.locator("#serviceDetailContent").inner_text()
    assert page.evaluate("requests.length") == request_count, "old detail must not recover a new query lifecycle"
    assert page.evaluate("toasts") == []


@pytest.mark.parametrize("boundary", ["refresh_fetch", "refresh_json", "detail_fetch", "detail_json"])
def test_late_product_refresh_cannot_mutate_same_service_reopened_modal(modal_page, boundary):
    page = modal_page
    open_loaded_modal(page, "ORIGINAL")
    page.evaluate("() => { window.oldButton = document.querySelector('.service-detail-refresh'); window.oldRefresh = refreshServiceOrderProducts('FWD100'); }")
    if boundary == "refresh_json":
        page.evaluate("respond(1, null, true)")
        page.wait_for_function("Boolean(pending[1].resolveJson)")
    elif boundary.startswith("detail_"):
        page.evaluate("respond(1, {success: true, product_count: 2, slot_label: '查询1'})")
        page.wait_for_function("pending.length === 3")
        if boundary == "detail_json":
            page.evaluate("respond(2, null, true)")
            page.wait_for_function("Boolean(pending[2].resolveJson)")
    page.evaluate("closeDetailModal(); toasts.length = 0;")
    open_loaded_modal(page)
    request_count = page.evaluate("requests.length")

    page.evaluate("""boundary => {
        if (boundary === 'refresh_json') pending[1].resolveJson({success: false, error: 'STALE ERROR'});
        if (boundary === 'refresh_fetch') respond(1, {success: false, error: 'STALE ERROR'});
        if (boundary === 'detail_json') pending[2].resolveJson(detail('STALE'));
        if (boundary === 'detail_fetch') respond(2, detail('STALE'));
    }""", boundary)
    page.evaluate("() => window.oldRefresh")

    assert "CURRENT" in page.locator("#serviceDetailContent").inner_text()
    assert "STALE" not in page.locator("#serviceDetailContent").inner_text()
    assert page.evaluate("requests.length") == request_count
    assert page.evaluate("toasts") == [], "closed refresh must not toast after reopen"
    assert page.evaluate("oldButton.disabled"), "closed refresh must not restore detached controls"
    assert not page.locator(".service-detail-refresh").first.is_disabled()


@pytest.mark.parametrize("reopen", [False, True])
def test_failed_order_attempt_shows_reason_and_time_without_replacing_success(modal_page, reopen):
    page = modal_page
    open_loaded_modal(page)
    page.evaluate("""() => { window.jobStatus = {
        job_id: 'failed-attempt', running: false, done: true, success: false,
        error: '订单 <SO-NEW> 产品编码缺失', started_at: '2026-09-15 08:00:00', finished_at: '2026-09-15 08:00:05'
    }; }""")
    if reopen:
        page.evaluate("closeDetailModal()")
        open_loaded_modal(page)
    else:
        page.evaluate("() => { startOrderProductQuery('FWD100'); }")
        page.evaluate("respond(1, {success: true, job_id: 'failed-attempt'})")
    page.wait_for_function("document.querySelector('.service-detail-order-query').disabled === false")
    content = page.locator("#serviceDetailContent").inner_text()
    assert "订单 <SO-NEW> 产品编码缺失" in content
    assert "2026-09-15 08:00:00" in content
    assert "SO-OLD" in content
    assert "2026-09-14 10:00:00" in content
    assert "一致" in content
    assert page.locator("#serviceDetailContent SO-NEW").count() == 0
    assert page.evaluate("orderProductQueryTimer") is None
