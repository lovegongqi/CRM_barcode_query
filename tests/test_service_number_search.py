import re
from pathlib import Path
from unittest import mock

import pytest
from playwright.sync_api import sync_playwright, expect

import app as app_module


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["light", "dark"])
def search_page(request):
    source = (ROOT / 'templates/service_close.html').read_text()
    source = source.replace("{{ 'true' if can_manage_history else 'false' }}", 'false')
    if request.param == 'dark':
        source = re.sub(r'{% if theme == "light" %}.*?{% endif %}', '', source, flags=re.S)
    source = re.sub(r'{[{%].*?[%}]}', '', source)
    requests = []
    state = {'error': '', 'history_saved': True, 'records': [], 'theme':request.param}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 430, 'height': 932})
        page.set_default_timeout(2000)

        def route_request(route):
            path = route.request.url.split('http://service.test')[1]
            if path == '/':
                return route.fulfill(body=source, content_type='text/html')
            if path.startswith('/static/'):
                file = ROOT / path.split('?')[0].lstrip('/')
                return route.fulfill(path=str(file))
            requests.append((route.request.method, path))
            if path.endswith('/refresh-products'):
                if state['error']:
                    return route.fulfill(json={'success': False, 'error': state['error']})
                if route.request.post_data_json == {'record_query': True} and state['history_saved']:
                    state['records'] = [{'id':'query-1', 'actor':'admin', 'finished_at':'2026-10-15 15:00:00', 'service_rows':[{'service_no':'FWD123456', 'state':'queried', 'message':'已查询服务单，尚未提交结单', 'barcodes':['SN1'], 'selected_barcodes':[]}]}]
                return route.fulfill(json={'success': True, 'history_saved':state['history_saved'], 'detail_url': '/api/service-orders/FWD123456'})
            if path == '/api/service-orders/FWD123456':
                return route.fulfill(json={'service_no': 'FWD123456', 'fields': [{'label': '客户姓名', 'value': '测试客户'}], 'products': [{'barcode': 'SN1', 'product_name': '测试产品'}]})
            return route.fulfill(json={'success': True, 'records': state['records'], 'running': False})

        page.route('http://service.test/**', route_request)
        page.goto('http://service.test/')
        page.screenshot(path=f'/tmp/service-search-{request.param}.png', full_page=True)
        yield page, requests, state
        browser.close()


def test_service_number_enter_queries_crm_and_opens_detail_without_closing_order(search_page):
    page, requests, _ = search_page
    page.get_by_label('服务单号', exact=True).fill(' fwd123456 ')
    page.get_by_label('服务单号', exact=True).press('Enter')
    expect(page.locator('#detailModal')).to_have_class(re.compile('overlay-show'))
    expect(page.locator('#serviceDetailContent')).to_contain_text('测试客户')
    expect(page.locator('#serviceDetailContent')).to_contain_text('SN1')
    assert ('POST', '/api/service-orders/FWD123456/refresh-products') in requests
    assert not any(path == '/api/service-close/start' for _, path in requests)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_query_validation_and_failure_allow_retry(search_page):
    page, requests, state = search_page
    field = page.get_by_label('服务单号', exact=True)
    field.fill('../wrong')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    expect(page.locator('#serviceSearchMessage')).to_contain_text('服务单号')
    assert not any(method == 'POST' for method, _ in requests)
    state['error'] = 'CRM 当前未登录，请先登录 CRM'
    field.fill('FWD123456')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    expect(page.locator('#serviceSearchMessage')).to_have_text(state['error'])
    expect(page.get_by_role('button', name='查询服务单', exact=True)).to_be_enabled()
    expect(field).to_have_value('FWD123456')


def test_direct_query_creates_detail_without_barcode_or_prior_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'SERVICE_ORDER_DIR', str(tmp_path))
    worker = mock.Mock()
    worker.refresh_service_order_products.return_value = (True, {
        'fields': [{'label': '客户姓名', 'value': '测试客户'}],
        'products': [{'barcode': 'SN1', 'product_name': '测试产品'}],
    })
    monkeypatch.setattr(app_module, '_select_idle_query_workers_desc', lambda: ([(worker, 'query-1', '查询1')], ''))
    with app_module.app.test_request_context():
        result = app_module.api_service_order_refresh_products('FWD123456').get_json()
        assert result['success']
        detail = app_module.api_service_order_detail('FWD123456').get_json()
    assert detail['products'][0]['barcode'] == 'SN1'
    assert detail['fields'][0]['value'] == '测试客户'
    worker.close_service_orders.assert_not_called()


@pytest.fixture
def query_history(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'CONFIG_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'SERVICE_ORDER_DIR', str(tmp_path / 'details'))
    monkeypatch.setattr(app_module, 'SERVICE_CLOSE_HISTORY_FILE', str(tmp_path / 'history.json'))
    monkeypatch.setattr(app_module, 'current_account', lambda: {'username': 'query-user'})
    worker = mock.Mock()
    worker.refresh_service_order_products.return_value = (True, {
        'fields': [{'label': '客户姓名', 'value': '测试客户'}],
        'products': [{'barcode': 'SN1', 'product_name': '测试产品'}],
    })
    monkeypatch.setattr(app_module, '_select_idle_query_workers_desc', lambda: ([(worker, 'query-1', '查询1')], ''))
    return worker


def recorded_query(service_no='FWD123456'):
    with app_module.app.test_request_context(json={'record_query': True}):
        response = app_module.api_service_order_refresh_products(service_no)
        return response.get_json()


def test_successful_query_is_durable_deduplicated_and_not_a_close(query_history):
    assert recorded_query()['history_saved']
    assert recorded_query()['history_saved']
    records = app_module.load_service_close_history()
    assert len(records) == 1
    record = records[0]
    assert record['actor'] == 'query-user'
    assert record['finished_at']
    assert record['closed_count'] == record['failed_count'] == 0
    row = record['service_rows'][0]
    assert row['service_no'] == 'FWD123456'
    assert row['state'] == 'queried'
    assert row['customer_names'] == ['测试客户']
    assert row['barcodes'] == ['SN1']
    assert row['selected_barcodes'] == []
    assert row['detail_url'] == '/api/service-orders/FWD123456'


@pytest.mark.parametrize('state', ['closed', 'already_closed', 'failed'])
def test_query_does_not_replace_a_close_result_and_close_replaces_query(query_history, state):
    recorded_query()
    job = app_module._empty_service_close_job(orders=[{'service_no': 'FWD123456'}])
    job.update(done=True, actor='closer', finished_at='2026-10-15 15:00:00')
    job['service_rows'][0].update(state=state, message='保留结单结果')
    assert app_module._append_service_close_history(job)
    assert recorded_query()['history_saved']
    records = app_module.load_service_close_history()
    assert len(records) == 1
    assert records[0]['actor'] == 'closer'
    assert records[0]['service_rows'][0]['state'] == state
    assert records[0]['service_rows'][0]['message'] == '保留结单结果'


def test_failed_query_creates_no_history(query_history):
    query_history.refresh_service_order_products.return_value = (False, {'error': '服务单不存在'})
    assert not recorded_query()['success']
    assert app_module.load_service_close_history() == []


def test_detail_refresh_does_not_create_query_history(query_history):
    with app_module.app.test_request_context():
        assert app_module.api_service_order_refresh_products('FWD123456').get_json()['success']
    assert app_module.load_service_close_history() == []


def test_history_write_failure_is_reported_without_losing_query_detail(query_history, monkeypatch):
    monkeypatch.setattr(app_module, '_save_service_close_history', lambda records: False)
    response = recorded_query()
    assert response['success']
    assert response['history_saved'] is False
    assert app_module._cached_service_order_detail('FWD123456')['products'][0]['barcode'] == 'SN1'


def test_query_displays_persistent_history_and_reopens_detail(search_page):
    page, requests, _ = search_page
    page.get_by_label('服务单号', exact=True).fill('FWD123456')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    expect(page.locator('#detailModal')).to_have_class(re.compile('overlay-show'))
    page.get_by_role('button', name='✕', exact=True).click()
    expect(page.locator('#serviceCloseRecordList')).to_contain_text('已查询')
    expect(page.locator('#serviceCloseLatestSummary')).not_to_contain_text('新结单')
    page.reload()
    expect(page.locator('.service-close-order-link')).to_have_count(1)
    page.locator('.service-close-order-link').click()
    expect(page.locator('#serviceDetailContent')).to_contain_text('测试客户')
    assert not any(path == '/api/service-close/start' for _, path in requests)


def test_query_warns_when_history_could_not_be_saved(search_page):
    page, _, state = search_page
    state['history_saved'] = False
    page.get_by_label('服务单号', exact=True).fill('FWD123456')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    expect(page.locator('#detailModal')).to_have_class(re.compile('overlay-show'))
    page.get_by_role('button', name='✕', exact=True).click()
    expect(page.locator('#serviceSearchMessage')).to_contain_text('记录保存失败')


def test_modal_close_button_queues_only_this_service_and_shows_result(search_page):
    page, _, _ = search_page
    submitted = []
    def close_route(route):
        submitted.append(route.request.post_data_json)
        route.fulfill(json={'success':True, 'job_id':'close-1'})
    page.route('**/api/service-close/start', close_route)
    page.route('**/api/service-close/status?job_id=close-1', lambda route: route.fulfill(json={
        'job_id':'close-1', 'running':False, 'done':True, 'service_rows':[{
            'service_no':'FWD123456', 'state':'closed', 'message':'已结单',
        }],
    }))
    page.get_by_label('服务单号', exact=True).fill('FWD123456')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    page.get_by_role('button', name='结单确认', exact=True).click()
    expect(page.locator('#serviceCloseActionStatus')).to_contain_text('已结单')
    assert submitted == [{'service_no':'FWD123456'}]
    expect(page.locator('.service-detail-close')).to_be_disabled()


def test_model_correction_requires_preview_then_explicit_save(search_page):
    page, _, state = search_page
    submitted = []
    option = {'detail_id':'row-1', 'seq':10, 'old_code':'906042809', 'old_name':'ERO121-3',
              'new_code':'906042810', 'new_name':'ERO131-3', 'quantity':1}
    def model_start(route):
        payload = route.request.post_data_json
        submitted.append(payload)
        route.fulfill(json={'job_id':payload['action'], 'action':payload['action'], 'running':True})
    def model_status(route):
        action = route.request.url.rsplit('=',1)[1]
        route.fulfill(json={'job_id':action, 'action':action, 'running':False, 'success':True,
                            'message':'型号已修正并核验', 'preview':{'order_no':'ORD2610040175', 'options':[option]}})
    page.route('**/model-correction', model_start)
    page.route('**/model-correction/status?job_id=*', model_status)
    page.get_by_label('服务单号', exact=True).fill('FWD123456')
    page.get_by_role('button', name='查询服务单', exact=True).click()
    page.get_by_role('button', name='型号修正', exact=True).click()
    expect(page.locator('#modelCorrectionPanel')).to_contain_text('ORD2610040175')
    expect(page.locator('#modelCorrectionPreview')).to_contain_text('ERO121-3')
    expect(page.locator('#modelCorrectionPreview')).to_contain_text('ERO131-3')
    page.screenshot(path=f'/tmp/model-preview-{state["theme"]}.png', full_page=True)
    assert submitted == [{'action':'preview'}]
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='确认修正', exact=True).click()
    expect(page.locator('#modelCorrectionPanel')).to_contain_text('型号已修正并核验')
    assert submitted[-1] == {'action':'apply', 'preview_job_id':'preview', 'option':0}
