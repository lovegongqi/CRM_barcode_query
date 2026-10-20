from playwright.sync_api import expect, sync_playwright

import app as app_module


def test_admin_can_add_and_remove_automatic_query_exclusion_from_page():
    with app_module.app.test_request_context('/product-library'):
        from flask import session
        session['account_username'] = 'admin'
        html = app_module.product_library_page()

    excluded = []
    requests = []

    def api(route):
        request = route.request
        requests.append((request.method, request.url))
        if request.method == 'GET':
            route.fulfill(json={'success': True, 'products': [], 'excluded_prefixes': excluded,
                                'can_edit': True, 'account': {'username': 'admin'}})
        elif request.method == 'POST':
            excluded.append(request.post_data_json['prefix'])
            route.fulfill(json={'success': True})
        else:
            excluded.remove(request.url.rsplit('/', 1)[-1])
            route.fulfill(json={'success': True})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('**/api/product-library**', api)
        page.route('**/static/**', lambda route: route.fulfill(body=''))
        page.route('http://library.test/product-library', lambda route: route.fulfill(body=html, content_type='text/html'))
        page.goto('http://library.test/product-library')

        page.get_by_label('排除查询前缀').fill('923')
        page.get_by_role('button', name='添加排除前缀').click()
        expect(page.locator('#excludedPrefixList')).to_contain_text('923')
        page.get_by_role('button', name='删除 923').click()
        expect(page.locator('#excludedPrefixList')).not_to_contain_text('923')
        assert [method for method, _ in requests if method != 'GET'] == ['POST', 'DELETE']
        browser.close()
