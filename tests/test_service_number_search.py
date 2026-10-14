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
    state = {'error': ''}
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
                return route.fulfill(json={'success': True, 'detail_url': '/api/service-orders/FWD123456'})
            if path == '/api/service-orders/FWD123456':
                return route.fulfill(json={'service_no': 'FWD123456', 'fields': [{'label': '客户姓名', 'value': '测试客户'}], 'products': [{'barcode': 'SN1', 'product_name': '测试产品'}]})
            return route.fulfill(json={'success': True, 'records': [], 'running': False})

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
