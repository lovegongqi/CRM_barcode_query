# GYJ 保存明细与查询通道优先级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 展示已保存的 GYJ 入库明细，并让匹配、结单和装箱单读取在普通条码查询之后的通道释放点优先执行。

**Architecture:** GYJ 任务在保存成功时把实际提交给写入器的行规范化为 `result.products`，前端在保存结果区域渲染这些行并独立折叠条码。查询通道采用内存中的高优先级等待队列：高优先级入口先创建可轮询任务；普通后台批量查询每完成一个条码让出其通道给等待队列，调度器以原子预留方式启动首个等待任务。

**Tech Stack:** Flask、Python threading/queue、Jinja 内联 JavaScript、Python unittest。

## Global Constraints

- 不取消或中断正在进行的单个 CRM 查询。
- 高优先级顺序固定为：装箱单读取、结单确认、匹配在线查询；普通条码批量查询最低。
- 一个查询通道同一时间只能分配给一个 CRM 工作。
- GYJ 保存结果只展示本次写入的行，不新增审核、编辑或保存动作。
- 条码默认收起；无条码配件显示“无条码”。

---

### Task 1: 返回并展示 GYJ 已保存行

**Files:**
- Modify: `app.py:4430-4464, 5360-5425, 9588-9610`
- Modify: `templates/inbound.html:40-80, 500-530`
- Modify: `tests/test_inbound_routes.py`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `build_gyj_purchase_lines(result)` 的 `product_code`、`description`、`quantity`、`serials`、`record_type` 行字典。
- Produces: 成功的 GYJ 状态响应中的 `result.products: list[dict]`；前端 `renderGYJSavedProducts(result)`。

- [x] **Step 1: 写入失败的状态 API 和模板契约测试**

```python
def test_gyj_completed_status_exposes_saved_line_details(self):
    # 启动 FakeGYJWorker 保存任务后断言 result.products 中的每项含编码、名称、数量、条码和类型。
    status = self._wait_for_gyj_job(client, job_id)
    self.assertEqual(status["result"]["products"][0], {
        "product_code": "916000024", "description": "中央净水机",
        "quantity": 1, "serials": ["SN00000001"], "record_type": "条码",
    })

def test_inbound_renders_saved_gyj_lines_with_collapsed_serials(self):
    inbound = self.source("inbound.html")
    self.assertIn("function renderGYJSavedProducts", inbound)
    self.assertIn("function toggleGYJSavedSerials", inbound)
    self.assertIn("展开条码", inbound)
    self.assertIn("无条码", inbound)
```

- [x] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_inbound_routes.InboundRouteTest tests.test_frontend_contract.FrontendContractTest -v`

Expected: 状态响应缺少 `result.products`，模板缺少已保存明细渲染函数。

- [x] **Step 3: 实施最小的行结果与折叠视图**

```python
saved_products = [
    {
        "product_code": str(line.get("product_code") or ""),
        "description": str(line.get("description") or ""),
        "quantity": int(line.get("quantity") or 0),
        "serials": list(line.get("serials") or []),
        "record_type": str(line.get("record_type") or ""),
    }
    for line in lines
]
job["result"] = {**result, "products": saved_products}
```

让 `renderGYJInboundStatus` 在成功完成时调用 `renderGYJSavedProducts(data.result)`；每个产品行使用独立 ID，条码容器初始 `hidden`，切换按钮更新为“展开条码/收起条码”。

- [x] **Step 4: 运行聚焦测试确认通过**

Run: `python3 -m unittest tests.test_inbound_routes.InboundRouteTest tests.test_frontend_contract.FrontendContractTest -v`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add app.py templates/inbound.html tests/test_inbound_routes.py tests/test_frontend_contract.py
git commit -m "feat: show saved GYJ inbound lines"
```

### Task 2: 高优先级等待队列和原子通道预留

**Files:**
- Modify: `app.py:4259-4300, 7040-7180, 8947-9005, 9608-9675, 9927-9965`
- Modify: `tests/test_inbound_routes.py`
- Modify: `tests/test_background_jobs.py`

**Interfaces:**
- Consumes: 现有入库、结单、匹配任务对象和查询 worker。
- Produces: `enqueue_priority_query_work(kind, job_id, launch)` 与 `dispatch_priority_query_work()`；任务状态 `stage='waiting'` 或既有等待状态，带“等待查询通道”日志。

- [ ] **Step 1: 写入失败的排队测试**

```python
def test_inbound_is_queued_when_all_query_slots_are_running_low_priority(self):
    # 单个查询通道正执行普通后台条码；启动入库返回 200 和 job_id，状态为 waiting。
    response = client.post("/api/inbound/start", json={"packing_slip_no": PACKING_SLIP_NO})
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.get_json()["stage"], "waiting")
    self.assertIn("等待查询通道", self._wait_for_status_log(response.get_json()["job_id"]))

def test_priority_dispatch_reserves_released_slot_before_low_priority_can_claim_next_barcode(self):
    # 用 BlockingQueryWorker 让第一条普通条码结束；断言入库读取先开始，第二条普通条码尚未开始。
    self.assertTrue(inbound_worker.started.wait(timeout=1))
    self.assertEqual(low_priority_worker.completed_barcodes, ["first"])
    self.assertNotIn("second", low_priority_worker.started_barcodes)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_inbound_routes.InboundRouteTest tests.test_background_jobs.BackgroundJobTests -v`

Expected: 无空闲通道时入库仍返回 409，普通批量会直接领取下一条码。

- [x] **Step 3: 实施共享等待队列和调度器**

```python
priority_query_work_lock = threading.RLock()
priority_query_waiters = []
priority_query_slot_reservations = {}

def enqueue_priority_query_work(kind, job_id, launch):
    with priority_query_work_lock:
        priority_query_waiters.append({"kind": kind, "job_id": job_id, "launch": launch})
        priority_query_waiters.sort(key=lambda row: PRIORITY_QUERY_WORK_ORDER[row["kind"]])
    dispatch_priority_query_work()
```

`dispatch_priority_query_work()` 在 `query_slot_reservation_lock` 中选取一个未占用通道，在启动线程前写入 `priority_query_slot_reservations[slot_id]`；启动包装器在任务结束后释放预留并继续派发。查询通道选择器把该预留视作占用。

入库、结单、匹配在线查询在无空闲通道时创建原有任务记录并加入队列，接口返回成功、任务 ID 与等待状态；有空闲通道时仍立即派发。结单排队时从第一个释放通道启动，不等待全部通道。

- [x] **Step 4: 运行聚焦测试确认通过**

Run: `python3 -m unittest tests.test_inbound_routes.InboundRouteTest tests.test_background_jobs.BackgroundJobTests -v`

Expected: PASS；高优先级任务获得释放的通道且不存在双重占用。

- [x] **Step 5: 提交**

```bash
git add app.py tests/test_inbound_routes.py tests/test_background_jobs.py
git commit -m "feat: queue high priority CRM work"
```

### Task 3: 普通后台查询在条码边界让位

**Files:**
- Modify: `app.py:5636-5865, 10403-10465`
- Modify: `tests/test_background_jobs.py`

**Interfaces:**
- Consumes: `dispatch_priority_query_work()` 和通道预留状态。
- Produces: 普通查询只在无高优先级等待或预留时领取下一个条码。

- [ ] **Step 1: 写入失败的协作让位测试**

```python
def test_low_priority_worker_waits_after_one_barcode_while_priority_work_is_queued(self):
    # 向优先队列放入等待项并释放第一条查询；断言 worker 在完成第一条后不领取第二条。
    worker.release_first.set()
    self.assertTrue(priority_started.wait(timeout=1))
    self.assertEqual(worker.started_barcodes, ["first"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m unittest tests.test_background_jobs.BackgroundJobTests -v`

Expected: 普通 worker 立即领取第二个条码。

- [x] **Step 3: 在领取下一条前执行让位检查**

```python
while not stop_requested():
    if yield_query_slot_to_priority_work(slot_id):
        wait_for_priority_slot_release(slot_id)
        continue
    item_index = item_queue.get_nowait()
```

`yield_query_slot_to_priority_work` 仅在当前条码已结束且有等待高优先级任务时解除该普通任务对通道的活动占用、触发派发；不调用 `worker.request_stop()`，不修改当前条码的终态。

- [x] **Step 4: 运行聚焦测试确认通过**

Run: `python3 -m unittest tests.test_background_jobs.BackgroundJobTests -v`

Expected: PASS；普通查询在高优先级工作清空后恢复处理其剩余条码。

- [x] **Step 5: 完整验证、重启与提交**

Run: `python3 -m unittest discover -s tests -v && git diff --check && node --check static/aurora.js`

Then: `launchctl kickstart -k gui/$(id -u)/com.crmbarcodequery.local`

Expected: 全套测试通过，本地服务重新加载；CRM/GYJ 持久会话不删除。
