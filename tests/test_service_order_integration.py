import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

import app as app_module


SERVICE_NO = "FWD20260914001"
ORDER_PRODUCTS = [
    {"product_name": "A", "product_code": "A", "quantity": 1},
    {"product_name": "B", "product_code": "B", "quantity": 2},
]
REFRESHED_PRODUCTS = [
    {"product_name": "A", "product_code": "A", "barcode": "A1"},
    {"product_name": "A", "product_code": "A", "barcode": "A2"},
    {"product_name": "B", "product_code": "B", "barcode": "B1"},
    {"product_name": "B", "product_code": "B", "barcode": "B2"},
]


@pytest.fixture
def detail_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "SERVICE_ORDER_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "order_product_jobs", {})
    app_module._write_service_order_detail(SERVICE_NO, {
        "service_no": SERVICE_NO,
        "legacy_note": "preserved",
        "products": [REFRESHED_PRODUCTS[0]],
    })
    return tmp_path / f"{SERVICE_NO}.json"


def refresh_products():
    with app_module.app.test_request_context():
        response = app_module.api_service_order_refresh_products(SERVICE_NO)
        assert response.get_json()["success"]


def order_lookup():
    job = app_module._empty_order_product_job(SERVICE_NO)
    app_module.order_product_jobs[job["job_id"]] = job
    worker = mock.Mock()
    worker.query_related_order_products.return_value = (True, {
        "order_no": "SO-NEW",
        "service_fields": [{"label": "关联订单号", "value": "SO-NEW"}],
        "order_products": ORDER_PRODUCTS,
    })
    app_module._run_order_product_job(job["job_id"], worker)
    assert job["success"], job["error"]


@pytest.fixture
def refresh_worker(monkeypatch):
    worker = mock.Mock()
    worker.refresh_service_order_products.return_value = (True, {"products": REFRESHED_PRODUCTS})
    monkeypatch.setattr(app_module, "_select_idle_query_workers_desc", lambda: ([(worker, "query-1", "查询1")], ""))
    return worker


def assert_final_comparison(saved):
    assert [(row["product_code"], row["order_quantity"], row["service_quantity"], row["status"])
            for row in saved["order_lookup"]["comparison"]] == [
        ("A", 1, 2, "quantity_mismatch"),
        ("B", 2, 2, "matched"),
    ]


def test_refresh_recomputes_cached_order_comparison(detail_cache, refresh_worker):
    order_lookup()
    before = json.loads(detail_cache.read_text())
    assert [row["status"] for row in before["order_lookup"]["comparison"]] == ["matched", "service_missing"]

    refresh_products()

    saved = json.loads(detail_cache.read_text())
    assert_final_comparison(saved)
    for key in ("order_no", "products", "queried_at"):
        assert saved["order_lookup"][key] == before["order_lookup"][key]
    assert saved["legacy_note"] == "preserved"


@pytest.mark.parametrize("kinds,expected", [
    (["order_products"], ["order_products"]),
    (["order_products", "service_close", "inbound", "library"],
     ["inbound", "service_close", "order_products", "library"]),
])
def test_priority_waiters_wake_when_channel_recovers_once_in_priority_order(monkeypatch, kinds, expected):
    monkeypatch.setattr(app_module, "priority_query_waiters", [])
    monkeypatch.setattr(app_module, "priority_query_slot_reservations", {})
    monkeypatch.setattr(app_module, "PRIORITY_QUERY_WAKEUP_SECONDS", 0.02, raising=False)
    available = threading.Event()
    launched = []
    all_done = threading.Event()
    worker = object()

    def select_worker():
        if available.is_set() and not app_module._query_slot_has_priority_reservation("query-1"):
            return worker, "query-1", "查询1", ""
        return None, "", "", "等待通道"

    def launch(kind):
        def run(_worker, slot_id, _label):
            assert app_module.priority_query_slot_reservations[slot_id] == kind
            launched.append(kind)
            app_module._release_priority_query_slot(slot_id)
            if len(launched) == len(expected):
                all_done.set()
        return run

    monkeypatch.setattr(app_module, "_select_idle_query_worker_desc", select_worker)
    try:
        for kind in kinds:
            app_module.enqueue_priority_query_work(kind, kind, launch(kind))
        assert not launched
        available.set()
        assert all_done.wait(timeout=1), "queued jobs never resumed after the channel became available"
        assert launched == expected
        assert app_module.priority_query_waiters == []
        assert app_module.priority_query_slot_reservations == {}
        # A drained queue must neither retain a retry timer nor launch twice.
        time.sleep(0.05)
        assert getattr(app_module, "priority_query_wakeup_timer", None) is None
        assert launched == expected
    finally:
        with app_module.priority_query_work_lock:
            app_module.priority_query_waiters.clear()
            timer = getattr(app_module, "priority_query_wakeup_timer", None)
            if timer:
                timer.cancel()
                app_module.priority_query_wakeup_timer = None
        if timer:
            timer.join(timeout=1)


@pytest.mark.parametrize("first", ["refresh", "order"])
def test_concurrent_cache_transactions_preserve_both_results(detail_cache, refresh_worker, monkeypatch, first):
    first_merged = threading.Event()
    first_may_commit = threading.Event()
    write = app_module._write_service_order_detail
    cache_lock = app_module._service_order_cache_lock
    operations = {"refresh": refresh_products, "order": order_lookup}

    def observed_cache_lock(service_no):
        if first_merged.is_set():
            first_may_commit.set()
        return cache_lock(service_no)

    def gated_write(service_no, detail):
        is_first = ("order_lookup" in detail and bool(detail["order_lookup"])) == (first == "order")
        if is_first and not first_merged.is_set():
            first_merged.set()
            # With serialization, the second transaction signals its lock
            # attempt. Without it, the second completes before this stale write.
            assert first_may_commit.wait(timeout=2)
        return write(service_no, detail)

    def second_operation():
        try:
            operations["order" if first == "refresh" else "refresh"]()
        finally:
            first_may_commit.set()

    monkeypatch.setattr(app_module, "_write_service_order_detail", gated_write)
    monkeypatch.setattr(app_module, "_service_order_cache_lock", observed_cache_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(operations[first])
        assert first_merged.wait(timeout=2)
        second_result = executor.submit(second_operation)
        first_result.result(timeout=3)
        second_result.result(timeout=3)

    saved = json.loads(detail_cache.read_text())
    assert [row["barcode"] for row in saved["products"]] == ["A1", "A2", "B1", "B2"]
    assert saved["order_lookup"]["order_no"] == "SO-NEW"
    assert saved["fields"] == [{"label": "关联订单号", "value": "SO-NEW"}]
    assert saved["legacy_note"] == "preserved"
    assert_final_comparison(saved)


def test_crm_work_happens_outside_cache_transaction(detail_cache, refresh_worker, monkeypatch):
    cache_lock = app_module._service_order_cache_lock(SERVICE_NO)

    def crm_refresh(*_args):
        assert not cache_lock.locked(), "CRM refresh must not hold a cache transaction"
        return True, {"products": REFRESHED_PRODUCTS}

    refresh_worker.refresh_service_order_products.side_effect = crm_refresh
    refresh_products()
    job = app_module._empty_order_product_job(SERVICE_NO)
    app_module.order_product_jobs[job["job_id"]] = job

    def crm_order(*_args, **_kwargs):
        assert not cache_lock.locked(), "CRM order lookup must not hold a cache transaction"
        return True, {"order_no": "SO-NEW", "order_products": ORDER_PRODUCTS}

    worker = mock.Mock()
    worker.query_related_order_products.side_effect = crm_order
    app_module._run_order_product_job(job["job_id"], worker)
    assert job["success"], job["error"]
    assert_final_comparison(json.loads(detail_cache.read_text()))


def test_concurrent_dispatch_and_retry_do_not_reserve_a_busy_slot_twice(monkeypatch):
    monkeypatch.setattr(app_module, "priority_query_waiters", [])
    monkeypatch.setattr(app_module, "priority_query_slot_reservations", {})
    monkeypatch.setattr(app_module, "PRIORITY_QUERY_WAKEUP_SECONDS", 0.01)
    available = threading.Event()
    launched = []
    first_started = threading.Event()
    worker = object()

    def select_worker():
        if available.is_set() and not app_module._query_slot_has_priority_reservation("query-1"):
            return worker, "query-1", "查询1", ""
        return None, "", "", "等待通道"

    def launch(_worker, slot_id, _label):
        launched.append(app_module.priority_query_slot_reservations[slot_id])
        first_started.set()

    monkeypatch.setattr(app_module, "_select_idle_query_worker_desc", select_worker)
    try:
        app_module.enqueue_priority_query_work("order_products", "first", launch)
        app_module.enqueue_priority_query_work("order_products", "second", launch)
        available.set()
        assert first_started.wait(timeout=1)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: app_module.dispatch_priority_query_work(), range(8)))
        assert launched == ["first"]
        assert app_module.priority_query_slot_reservations == {"query-1": "first"}
        app_module._release_priority_query_slot("query-1")
        assert launched == ["first", "second"]
        app_module._release_priority_query_slot("query-1")
        assert app_module.priority_query_slot_reservations == {}
        assert app_module.priority_query_wakeup_timer is None
    finally:
        with app_module.priority_query_work_lock:
            app_module.priority_query_waiters.clear()
            timer = app_module.priority_query_wakeup_timer
            app_module._schedule_priority_query_wakeup()
        if timer is not None:
            timer.join(timeout=2)
