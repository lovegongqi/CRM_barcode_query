# Task 2 实现报告

## 改动

- 在 `inventory_store.py` 增加 `create_task`、`get_active_task`、`get_task_snapshot`、`list_items`。
- `create_task` 使用 `BEGIN IMMEDIATE`，以 UUID 创建任务，在同一事务内写入任务、全量商品和 `task_created` audit；同一 owner 对 loading/counting/serial_check/sync_error 任务拒绝重复创建。
- 商品数量通过 `normalize_quantity` 以精确十进制文本写入；仅持久化简报规定的商品字段。
- 快照支持版本 unchanged 短路（不返回 `items`）；商品列表支持条码/名称大小写不敏感搜索、状态过滤，以及默认隐藏零库存但非空搜索覆盖隐藏。
- `tests/test_inventory_store.py` 增加上述生命周期、可见性、版本和 audit 测试。

## TDD 证据

- RED：`python3 -m unittest tests.test_inventory_store.InventoryStoreTests.test_one_active_task_per_owner_and_restart_persistence tests.test_inventory_store.InventoryStoreTests.test_zero_stock_is_hidden_until_search tests.test_inventory_store.InventoryStoreTests.test_snapshot_omits_items_when_version_is_unchanged tests.test_inventory_store.InventoryStoreTests.test_task_creation_inserts_audit_event -v`；4 项均因缺少 `create_task` 报 `AttributeError` 失败。
- GREEN：`python3 -m unittest tests.test_inventory_store -v`；8/8 通过。

## 测试结果

- 全量：`python3 -m unittest discover -s tests -v`；268/268 通过。
- `python3 -m unittest discover -v` 在仓库根目录未发现测试（退出码 5）；已使用项目实际测试目录参数完成全量验证。

## Commit

- `feat(inventory): persist stocktake tasks and products`（本任务提交）

## 风险

- 当前生命周期接口只覆盖 Task 2；锁、数量变更、阶段推进等行为由后续任务实现。
