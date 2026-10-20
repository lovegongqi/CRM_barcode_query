from unittest import mock

import pytest

import app as app_module


@pytest.fixture
def direct_close(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'SERVICE_ORDER_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'service_close_jobs', {})
    monkeypatch.setattr(app_module, 'current_account', lambda: {'username': 'operator'})
    monkeypatch.setattr(app_module.threading, 'Thread', mock.Mock())
    app_module._write_service_order_detail('FWD123456', {
        'service_no': 'FWD123456',
        'fields': [{'label': '客户姓名', 'value': '测试客户'}],
        'products': [{'product_code': 'P1', 'barcode': 'SN1'}],
    })


def start_close(payload):
    with app_module.app.test_request_context(json=payload):
        response = app_module.api_service_close_start()
        return response if isinstance(response, tuple) else (response, 200)


def test_direct_close_queues_only_the_current_cached_service_order(direct_close):
    response, status = start_close({'service_no': 'FWD123456'})
    data = response.get_json()
    assert status == 200
    assert data['success']
    assert data['total'] == 1
    assert data['orders'][0]['service_no'] == 'FWD123456'
    job = app_module.service_close_jobs[data['job_id']]
    assert job['running']
    assert job['actor'] == 'operator'
    assert job['service_rows'][0]['customer_names'] == ['测试客户']
    assert job['selected_barcodes'] == []


def test_batch_close_uses_only_selected_cached_services(direct_close):
    app_module._write_service_order_detail('FWD654321', {'service_no': 'FWD654321', 'products': []})
    response, status = start_close({'service_nos': ['FWD123456', 'FWD654321']})
    assert status == 200
    assert response.get_json()['total'] == 2
    assert [row['service_no'] for row in response.get_json()['orders']] == ['FWD123456', 'FWD654321']
    assert not start_close({'service_nos': ['FWD123456', 'FWD123456']})[0].get_json()['success']


def test_batch_close_requires_fresh_barcode_model_agreement(monkeypatch):
    worker = mock.Mock()
    preview = {'service_lines': [{'barcode': '9232506010078', 'code': '926051587'}],
               'rows': [{'new_productname': '926051571', 'new_qty': 1}], 'options': []}
    worker.correct_service_order_model.return_value = (True, preview)
    monkeypatch.setattr(app_module, 'load_product_library', lambda: {'923': {'product_code': '926051571'}})
    ready, reason = app_module._service_batch_close_ready(worker, 'FWD123456', lambda *_: None)
    assert not ready and '不一致' in reason
    preview['service_lines'][0]['code'] = '926051571'
    assert app_module._service_batch_close_ready(worker, 'FWD123456', lambda *_: None)[0]
    preview['options'] = [{'new_code': '926051571'}]
    assert not app_module._service_batch_close_ready(worker, 'FWD123456', lambda *_: None)[0]
    preview['options'] = []
    preview['service_lines'][0]['barcode'] = ''
    assert not app_module._service_batch_close_ready(worker, 'FWD123456', lambda *_: None)[0]
    preview['service_lines'][0]['barcode'] = '9232506010078'
    preview['rows'][0]['new_qty'] = 2
    assert not app_module._service_batch_close_ready(worker, 'FWD123456', lambda *_: None)[0]


def test_batch_close_skips_mismatch_without_submitting_crm_close(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'service_close_jobs', {})
    monkeypatch.setattr(app_module, '_append_service_close_history', lambda _record: True)
    monkeypatch.setattr(app_module, '_start_order_product_job', lambda *_args, **_kwargs: (None, False))
    monkeypatch.setattr(app_module, 'load_product_library', lambda: {'923': {'product_code': '926051571'}})
    worker = mock.Mock()
    worker.correct_service_order_model.return_value = (True, {
        'service_lines': [{'barcode': '9232506010078', 'code': '926051587'}], 'options': [],
    })
    job = app_module._empty_service_close_job(orders=[{'service_no': 'FWD123456'}])
    job.update(running=True, started_at='2026-10-20 12:00:00')
    app_module.service_close_jobs[job['job_id']] = job
    app_module._run_service_close_job(job['job_id'], [(worker, 'query-1', '查询1')], job['orders'], verify_models=True)
    worker.close_service_orders.assert_not_called()
    assert job['failed_count'] == 1
    assert job['service_rows'][0]['state'] == 'failed'


def test_detail_annotates_exact_barcode_model_conflict(direct_close, monkeypatch):
    app_module._write_service_order_detail('FWD123456', {
        'products':[{'barcode':'3752311060186','product_code':'906042810','product_name':'ERO131-3'}],
    })
    monkeypatch.setattr(app_module, 'load_product_library', lambda: {
        '375':{'source_barcode':'3752311060186','product_code':'906042809','product_name':'ERO121-3'},
    })
    with app_module.app.test_request_context():
        data = app_module.api_service_order_detail('FWD123456').get_json()
    assert data['products'][0]['barcode_match'] == {
        'product_code':'906042809','product_name':'ERO121-3','exact':True,'mismatch':True,
    }


def test_detail_checks_barcode_prefix_before_order_service_agreement(direct_close, monkeypatch):
    app_module._write_service_order_detail('FWD123456', {
        'products': [{'barcode': '9232506010078', 'product_code': '926051587',
                      'product_name': 'CTO-20替换滤芯成品'}],
        'order_lookup': {'comparison': [{'product_code': '926051587', 'status': 'matched',
                                         'status_label': '一致'}]},
    })
    monkeypatch.setattr(app_module, 'load_product_library', lambda: {
        '923': {'source_barcode': '9232505290418', 'product_code': '926051571',
                'product_name': 'H-CTO-12L滤芯'},
    })
    with app_module.app.test_request_context():
        data = app_module.api_service_order_detail('FWD123456').get_json()
    assert data['products'][0]['barcode_match'] == {
        'product_code': '926051571', 'product_name': 'H-CTO-12L滤芯',
        'exact': False, 'mismatch': True,
    }


@pytest.mark.parametrize('service_no', ['../../other', 'FWD-NOT-QUERIED'])
def test_direct_close_rejects_invalid_or_unqueried_service(direct_close, service_no):
    response, _ = start_close({'service_no': service_no})
    assert not response.get_json()['success']
    assert not app_module.service_close_jobs


def test_duplicate_direct_close_reuses_the_running_job(direct_close):
    first, _ = start_close({'service_no': 'FWD123456'})
    second, status = start_close({'service_no': 'FWD123456'})
    assert status == 409
    assert second.get_json()['job_id'] == first.get_json()['job_id']
    assert len(app_module.service_close_jobs) == 1


@pytest.fixture
def model_jobs(direct_close, monkeypatch):
    monkeypatch.setattr(app_module, 'service_model_jobs', {})
    monkeypatch.setattr(app_module, 'enqueue_priority_query_work', mock.Mock())


def start_model(payload, service_no='FWD123456'):
    with app_module.app.test_request_context(json=payload):
        result = app_module.api_service_model_correction_start(service_no)
        return result if isinstance(result, tuple) else (result, 200)


def test_model_apply_requires_successful_owned_preview(model_jobs):
    response, status = start_model({'action':'apply', 'preview_job_id':'invented', 'option':0})
    assert status == 400
    assert not response.get_json()['success']
    assert not app_module.service_model_jobs


def test_model_preview_starts_a_read_job_and_duplicate_request_does_not_duplicate(model_jobs):
    response, _ = start_model({'action':'preview'})
    job = app_module.service_model_jobs[response.get_json()['job_id']]
    assert job['action'] == 'preview'
    assert job['actor'] == 'operator'
    assert job['running']
    duplicate, status = start_model({'action':'preview'})
    assert status == 409
    assert duplicate.get_json()['job_id'] == job['job_id']
    assert len(app_module.service_model_jobs) == 1


def test_manual_model_preview_keeps_server_validated_barcode_and_code(model_jobs):
    response, status = start_model({'action':'preview', 'override':{
        'barcode':'9232506010078', 'product_code':' 926051571 ',
    }})
    assert status == 200
    job = app_module.service_model_jobs[response.get_json()['job_id']]
    assert job['_override'] == {'barcode':'9232506010078', 'product_code':'926051571'}


def test_manual_model_preview_accepts_service_line_without_barcode(model_jobs):
    response, status = start_model({'action':'preview', 'override':{
        'service_line_id':'12345678-1234-1234-1234-123456789abc', 'product_code':' NEW002 ',
    }})
    assert status == 200
    job = app_module.service_model_jobs[response.get_json()['job_id']]
    assert job['_override'] == {
        'service_line_id':'12345678-1234-1234-1234-123456789abc', 'product_code':'NEW002',
    }


def test_model_preview_status_exposes_service_lines_for_manual_selection():
    job = {'job_id': 'preview-1', 'service_no': 'FWD123456', 'action': 'preview',
           'success': True, 'result': {'order_no': 'ORD123', 'options': [],
                                       'service_lines': [{'id': 'line-1', 'name': '旧型号',
                                                          'code': 'OLD001', 'barcode': ''}]}}
    payload = app_module._service_model_job_payload(job)
    assert payload['preview']['service_lines'][0]['id'] == 'line-1'


def test_manual_model_preview_rejects_invalid_code(model_jobs):
    response, status = start_model({'action':'preview', 'override':{
        'barcode':'9232506010078', 'product_code':'<script>',
    }})
    assert status == 400
    assert not response.get_json()['success']
    assert not app_module.service_model_jobs


def ready_preview():
    import time
    job = {
        'job_id':'preview-1', 'service_no':'FWD123456', 'actor':'operator', 'action':'preview',
        'running':False, 'success':True, 'done':True, '_created_ts':time.time(),
        'result':{'fingerprint':'server-hash', 'order_no':'ORD-1', 'options':[{'detail_id':'server-row','new_code':'server-product'}]},
    }
    app_module.service_model_jobs[job['job_id']] = job
    return job


def test_model_apply_uses_server_preview_and_rejects_repeat_submission(model_jobs):
    source = ready_preview()
    response, status = start_model({'action':'apply', 'preview_job_id':'preview-1', 'option':0,
                                    'detail_id':'forged-row','new_code':'forged-product'})
    assert status == 200
    job = app_module.service_model_jobs[response.get_json()['job_id']]
    assert job['_option'] == {'detail_id':'server-row','new_code':'server-product'}
    assert job['_expected']['fingerprint'] == 'server-hash'
    job.update(running=False, done=True, success=True)
    repeated, status = start_model({'action':'apply', 'preview_job_id':'preview-1', 'option':0})
    assert status == 409
    assert repeated.get_json()['job_id'] == job['job_id']
    assert len(app_module.service_model_jobs) == 2
    assert source['_apply_job_id'] == job['job_id']


def test_model_apply_keeps_manual_target_from_preview(model_jobs):
    source = ready_preview()
    source['_override'] = {'barcode':'9232506010078', 'product_code':'926051571'}
    response, status = start_model({'action':'apply', 'preview_job_id':source['job_id'], 'option':0,
                                    'override':{'barcode':'forged', 'product_code':'FORGED'}})
    assert status == 200
    job = app_module.service_model_jobs[response.get_json()['job_id']]
    assert job['_override'] == source['_override']


@pytest.mark.parametrize('change', ['actor', 'expired', 'other_service', 'invalid_option'])
def test_model_apply_rejects_foreign_expired_or_invalid_preview(model_jobs, change):
    source = ready_preview()
    index = 0
    if change == 'actor': source['actor'] = 'someone-else'
    elif change == 'expired': source['_created_ts'] -= 901
    elif change == 'other_service': source['service_no'] = 'FWD-OTHER'
    else: index = -1
    response, status = start_model({'action':'apply', 'preview_job_id':'preview-1', 'option':index})
    assert status == 400
    assert not response.get_json()['success']
    assert len(app_module.service_model_jobs) == 1


def test_close_is_blocked_while_model_correction_is_running(model_jobs):
    source = ready_preview()
    source.update(running=True, action='apply')
    response, status = start_close({'service_no':'FWD123456'})
    assert status == 409
    assert not response.get_json()['success']
    assert not app_module.service_close_jobs
