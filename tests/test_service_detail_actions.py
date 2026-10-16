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
