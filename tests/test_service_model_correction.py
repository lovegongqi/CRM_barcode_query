import copy

import pytest

import app as app_module


@pytest.fixture
def model_snapshot():
    return {
        'order_id': 'order-id', 'order_no': 'ORD2610040175',
        'rows': [{'detailid':'row-1', 'new_seq':10, 'new_type':1, 'new_isgifts':False,
                  'new_productname':'906042809', 'new_productid':{'id':'old-id', 'name':'ERO121-3', 'logicalname':'product'},
                  'new_qty':1, 'new_installedqty':0, 'new_returnqty':0, 'new_price':3980, 'new_amount':3980}],
    }


def preview(snapshot, products=None):
    return app_module.build_model_correction_preview(snapshot, products or [
        {'product_code':'906042810', 'product_name':'ERO131-3', 'barcode':'SN1'},
    ])


def test_model_preview_matches_order_line_to_service_model_without_mutating_snapshot(model_snapshot):
    before = copy.deepcopy(model_snapshot)
    result = preview(model_snapshot)
    assert result['order_no'] == 'ORD2610040175'
    option = result['options'][0]
    assert option['detail_id'] == 'row-1'
    assert option['old_code'] == '906042809'
    assert option['new_code'] == '906042810'
    assert option['old_name'] == 'ERO121-3'
    assert option['new_name'] == 'ERO131-3'
    assert option['quantity'] == 1
    assert model_snapshot == before


@pytest.mark.parametrize('field,value', [('new_installedqty',1), ('new_returnqty',1), ('new_qty',2), ('new_isgifts',True), ('new_type',2)])
def test_model_preview_never_offers_installed_returned_or_ambiguous_quantity_lines(model_snapshot, field, value):
    model_snapshot['rows'][0][field] = value
    assert preview(model_snapshot)['options'] == []


def test_model_preview_does_not_replace_models_already_on_both_documents(model_snapshot):
    assert preview(model_snapshot, [{'product_code':'906042809', 'product_name':'ERO121-3'}])['options'] == []


def test_model_preview_fingerprint_changes_when_price_or_service_model_changes(model_snapshot):
    first = preview(model_snapshot)['fingerprint']
    model_snapshot['rows'][0]['new_price'] = 3990
    assert preview(model_snapshot)['fingerprint'] != first
    model_snapshot['rows'][0]['new_price'] = 3980
    assert preview(model_snapshot, [{'product_code':'OTHER'}])['fingerprint'] != first


@pytest.fixture
def model_page(model_snapshot):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content('<main id="order"></main>')
        page.evaluate('''snapshot => {
            window.testState = {writes:[], row:snapshot.rows[0], mode:'success'};
            const s = window.testState;
            window.rt = {get: async url => {
                if (url.includes('crmlookupview')) return {Data:[{productid:'new-id', name:'ERO131-3', productnumber:'906042810'}]};
                if (url.includes('GetOrderLineList')) return [structuredClone(s.row)];
                return structuredClone(s.row);
            }};
            document.querySelector('main').__vue__ = {
                $route:{params:{id:snapshot.order_id}}, editForm:{new_name:snapshot.order_no},
                $refs:{'product-line':{id:snapshot.order_id, loading:false, faultData:[s.row], $refs:{productLine:{
                    dialogFormVisible:false, loading:false, isSubmit:false,
                    updateFormDate: async payload => {
                        s.writes.push(structuredClone(payload));
                        if (s.mode === 'denied') throw Error('CRM 无权修改该订单');
                        if (s.mode === 'unchanged') return;
                        s.row = {...payload, new_productname:payload.productnumber};
                    },
                }}}},
            };
        }''', model_snapshot)
        yield page
        browser.close()


def test_crm_snapshot_reads_fresh_server_values(model_page):
    from service_model_correction import MODEL_SNAPSHOT_JS
    model_page.evaluate('testState.row.new_price = 4000')
    result = model_page.evaluate(MODEL_SNAPSHOT_JS, 'ORD2610040175')
    assert result['rows'][0]['new_price'] == 4000
    assert model_page.evaluate('testState.writes.length') == 0


def test_crm_apply_changes_only_product_reference_and_verifies_readback(model_page, model_snapshot):
    from service_model_correction import MODEL_APPLY_JS
    plan = preview(model_snapshot)
    result = model_page.evaluate(MODEL_APPLY_JS, {'preview':plan, 'option':plan['options'][0]})
    assert result['new_code'] == '906042810'
    writes = model_page.evaluate('testState.writes')
    assert len(writes) == 1
    assert writes[0]['id'] == 'order-id'
    assert writes[0]['new_productid']['id'] == 'new-id'
    assert writes[0]['new_qty'] == 1
    assert writes[0]['new_price'] == writes[0]['new_amount'] == 3980


@pytest.mark.parametrize('mutation', [
    'testState.row.new_installedqty = 1',
    "document.querySelector('main').__vue__.editForm.new_name = 'WRONG'",
    'testState.row.new_price = 1',
])
def test_crm_apply_stops_before_write_if_order_changed(model_page, model_snapshot, mutation):
    from service_model_correction import MODEL_APPLY_JS
    from playwright.sync_api import Error
    plan = preview(model_snapshot)
    model_page.evaluate(mutation)
    with pytest.raises(Error):
        model_page.evaluate(MODEL_APPLY_JS, {'preview':plan, 'option':plan['options'][0]})
    assert model_page.evaluate('testState.writes.length') == 0


@pytest.mark.parametrize('mode,message', [('denied','无权修改'), ('unchanged','未确认新型号')])
def test_crm_apply_reports_rejected_or_unverified_save(model_page, model_snapshot, mode, message):
    from service_model_correction import MODEL_APPLY_JS
    from playwright.sync_api import Error
    plan = preview(model_snapshot)
    model_page.evaluate('(mode) => testState.mode = mode', mode)
    with pytest.raises(Error, match=message):
        model_page.evaluate(MODEL_APPLY_JS, {'preview':plan, 'option':plan['options'][0]})
    assert model_page.evaluate('testState.writes.length') == 1
