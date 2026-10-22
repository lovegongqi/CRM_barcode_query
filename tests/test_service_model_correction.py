import copy
import tempfile
from unittest import mock

import pytest

import app as app_module
from service_model_correction import build_barcode_model_correction_preview


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


def test_order_model_preview_uses_known_related_order_without_researching_service(model_snapshot):
    session = app_module.CRMSession.__new__(app_module.CRMSession)
    session.lock = mock.MagicMock()
    service_no = 'FWD202306280970'
    order_no = 'ORD2306280837'
    model_snapshot['order_no'] = order_no
    products = [{'product_code':'906042810', 'product_name':'目标型号', 'barcode':'243221114K129'}]
    session.page = mock.Mock()
    order = copy.deepcopy(model_snapshot)
    def evaluate(script, value):
        if script == app_module.MODEL_SNAPSHOT_JS:
            return copy.deepcopy(order)
        if script == app_module.MODEL_APPLY_JS:
            order['rows'][0]['new_productname'] = value['option']['new_code']
            return {'new_code': value['option']['new_code']}
        raise AssertionError('不应读取服务单编辑页')
    session.page.evaluate.side_effect = evaluate
    with tempfile.TemporaryDirectory() as directory, \
         mock.patch.object(app_module, 'SERVICE_ORDER_DIR', directory), \
         mock.patch.object(session, 'query_related_order_products', return_value=(True, {
             'order_no':order_no, 'service_fields':[{'label':'关联订单', 'value':order_no}],
             'service_products':products,
         })), mock.patch.object(session, 'refresh_service_order_products', side_effect=AssertionError('不应重新搜索服务单')), \
         mock.patch.object(app_module, 'load_product_library', return_value={}):
        app_module._write_service_order_detail(service_no, {'fields':[{'label':'关联订单', 'value':order_no}], 'products':products})
        ok, result = session.correct_service_order_model(service_no)
        assert ok, result
        option = result['options'][0]
        applied, saved = session.correct_service_order_model(service_no, expected=result, option=option)
    assert ok, result
    assert option['new_code'] == '906042810'
    assert applied, saved
    assert saved['new_code'] == '906042810'
    assert session.page.evaluate.call_args_list[2] == mock.call(app_module.MODEL_APPLY_JS, {'preview':result, 'option':option})


def test_barcode_preview_targets_verified_model_and_orders_writes_first(model_snapshot):
    model_snapshot['rows'][0]['new_productname'] = '906042810'
    model_snapshot['rows'][0]['new_productid']['name'] = 'ERO131-3'
    service = {'service_no':'FWD202610040292', 'work_order_id':'service-id', 'editable':True,
               'rows':[{'id':'service-line', 'new_userprofile_number':'3752311060186',
                        'productnumber':'906042810', 'new_product_id':{'id':'old-id', 'name':'ERO131-3'}}]}
    library = {'375':{'prefix':'375', 'product_code':'906042809', 'product_name':'ERO121-3',
                      'source_barcode':'3752311060186'}}
    result = build_barcode_model_correction_preview(model_snapshot, service, library)
    assert len(result['options']) == 1
    option = result['options'][0]
    assert option['old_code'] == '906042810'
    assert option['new_code'] == '906042809'
    assert option['service_line_id'] == 'service-line'
    assert option['order_detail_id'] == 'row-1'
    assert option['barcode'] == '3752311060186'
    assert option['steps'] == ['order', 'service']


def test_barcode_preview_accepts_prefix_model_but_requires_unique_order_line(model_snapshot):
    model_snapshot['rows'][0]['new_productname'] = '906042810'
    service = {'work_order_id':'service-id', 'editable':True,
               'rows':[{'id':'service-line', 'new_userprofile_number':'3752311060186',
                        'productnumber':'906042810'}]}
    library = {'375':{'product_code':'906042809','product_name':'ERO121-3',
                      'source_barcode':'3759999999999'}}
    assert build_barcode_model_correction_preview(model_snapshot, service, library)['options'][0]['new_code'] == '906042809'
    library['375']['source_barcode'] = '3752311060186'
    model_snapshot['rows'].append({**model_snapshot['rows'][0], 'detailid':'row-2'})
    assert build_barcode_model_correction_preview(model_snapshot, service, library)['options'] == []


def test_model_correction_manual_code_overrides_prefix_for_selected_barcode():
    barcode = '9232506010078'
    order = {'order_id': 'order-id', 'order_no': 'ORD2610140356', 'rows': [{
        'detailid': 'row-1', 'new_seq': 10, 'new_type': 1, 'new_isgifts': False,
        'new_productname': '926051587', 'new_productid': {'name': 'CTO-20替换滤芯成品'},
        'new_qty': 1, 'new_installedqty': 0, 'new_returnqty': 0,
    }]}
    service = {'service_no': 'FWD202610140423', 'editable': True, 'rows': [{
        'id': 'service-row', 'new_userprofile_number': barcode, 'productnumber': '926051587',
    }]}
    library = {'923': {'source_barcode': '9232505290418', 'product_code': '926051571',
                       'product_name': 'H-CTO-12L滤芯'}}
    prefix_option = build_barcode_model_correction_preview(order, service, library)['options'][0]
    assert prefix_option['new_code'] == '926051571'
    custom = {barcode: {'product_code': '926099999', 'product_name': '手填型号'}}
    option = build_barcode_model_correction_preview(order, service, library, custom)['options'][0]
    assert option['new_code'] == '926099999'
    assert option['new_name'] == '手填型号'
    assert option['steps'] == ['order', 'service']


def test_manual_model_correction_can_target_service_line_without_barcode():
    order = {'order_id': 'order-id', 'order_no': 'ORD2610200062', 'rows': [{
        'detailid': 'order-line', 'new_seq': 10, 'new_type': 1, 'new_isgifts': False,
        'new_productname': 'OLD001', 'new_productid': {'name': '旧型号'},
        'new_qty': 1, 'new_installedqty': 0, 'new_returnqty': 0,
    }]}
    service = {'service_no': 'FWD202610200133', 'editable': True, 'rows': [{
        'id': 'service-line', 'new_userprofile_number': '', 'productnumber': 'OLD001',
        'new_product_id': {'name': '旧型号'}, 'new_mem_salesorderlineid': 'order-line',
    }]}
    target = {'product_code': 'NEW002', 'product_name': '新型号'}
    result = build_barcode_model_correction_preview(order, service, {}, {'service-line': target})
    assert result['options'] == [{
        'barcode': '', 'service_line_id': 'service-line', 'order_detail_id': 'order-line',
        'seq': 10, 'old_code': 'OLD001', 'old_name': '旧型号',
        'new_code': 'NEW002', 'new_name': '新型号', 'quantity': 1,
        'source': 'manual', 'steps': ['order', 'service'],
    }]


@pytest.mark.parametrize('barcode', ['3752311060186', ''])
def test_service_model_apply_waits_for_order_and_preserves_barcode(model_page, model_snapshot, barcode):
    from service_model_correction import SERVICE_APPLY_JS
    model_snapshot['rows'][0]['new_productname'] = '906042809'
    model_page.evaluate('''barcode => {
        const s = window.testState;
        s.service = {id:'service-line',new_userprofile_number:barcode,productnumber:'906042810',
                     new_product_id:{id:'old-id',name:'ERO131-3'},new_mem_salesorderlineid:'row-1'};
        s.serviceWrites = [];
        const previousGet = rt.get;
        rt.get = async url => {
            if (url.includes('GetWorkOrderById')) return {new_name:'FWD202610040292',new_approvalstatus:1};
            if (url.includes('GetSrvProductlineById')) return structuredClone(s.service);
            if (url.includes('crmlookupview')) return {Data:[{productid:'target-id',name:'ERO121-3',productnumber:'906042809'}]};
            return previousGet(url);
        };
        rt.post = async (url,payload) => { s.serviceWrites.push({url,payload:structuredClone(payload)});
            s.service = {...payload,productnumber:payload.productnumber}; };
    }''', barcode)
    service = {'service_no':'FWD202610040292','work_order_id':'service-id','editable':True,
               'rows':[model_page.evaluate('testState.service')]}
    option = {'service_line_id':'service-line','order_detail_id':'row-1','new_code':'906042809'}
    plan = {'order_id':'order-id','service':service,'rows':model_snapshot['rows']}
    model_page.evaluate('testState.row.new_productname = "906042810"')
    from playwright.sync_api import Error
    with pytest.raises(Error, match='先修正关联订单'):
        model_page.evaluate(SERVICE_APPLY_JS, {'preview':plan,'option':option})
    assert model_page.evaluate('testState.serviceWrites.length') == 0
    model_page.evaluate('testState.row.new_productname = "906042809"')
    model_page.evaluate(SERVICE_APPLY_JS, {'preview':plan,'option':option})
    writes = model_page.evaluate('testState.serviceWrites')
    assert len(writes) == 1
    assert writes[0]['url'] == 'api/WorkOrder/UpdateSrvProductline'
    assert writes[0]['payload']['new_userprofile_number'] == barcode
    assert writes[0]['payload']['new_mem_salesorderlineid'] == 'row-1'


def test_service_snapshot_reads_full_line_even_when_detail_omits_id(model_page):
    from service_model_correction import SERVICE_SNAPSHOT_JS
    model_page.evaluate('''() => {
        location.hash = '#/workOrder/edit/service-id';
        rt.get = async url => {
            if (url.includes('GetWorkOrderById')) return {new_name:'FWD202610040292',new_approvalstatus:1};
            if (url.includes('GetSrvProductlineById')) return {productnumber:'906042810',new_userprofile_number:'3752311060186'};
            if (url.includes('GetSrvProductline')) return [{id:'service-line'}];
        };
    }''')
    result = model_page.evaluate(SERVICE_SNAPSHOT_JS, 'FWD202610040292')
    assert result['editable']
    assert result['rows'][0]['id'] == 'service-line'


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


def test_manual_model_lookup_requires_one_exact_crm_product(model_page):
    from playwright.sync_api import Error
    from service_model_correction import MODEL_LOOKUP_JS
    assert model_page.evaluate(MODEL_LOOKUP_JS, '906042810') == {
        'product_code':'906042810', 'product_name':'ERO131-3',
    }
    with pytest.raises(Error, match='未找到唯一'):
        model_page.evaluate(MODEL_LOOKUP_JS, '906042811')


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
