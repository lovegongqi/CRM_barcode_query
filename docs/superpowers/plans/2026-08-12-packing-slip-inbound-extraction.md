# 装箱单入库信息提取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 新增受独立权限保护的“入库”页面，使用空闲且已登录的查询通道连续读取 CRM 装箱单所有明细页，并提供分组结果和每条码一行的 XLSX。

**Architecture:** 纯数据校验、清洗、分组和 XLSX 生成放在新模块 inbound_extraction.py；CRM DOM 导航和严格连续分页放在 inbound_crm.py；现有 CRMSession/CRMWorker 线程边界和 app.py 负责通道与账号隔离的后台任务。新页面 templates/inbound.html 只启动、轮询和展示服务端任务，不持有 CRM 执行权。

**Tech Stack:** Python 3.11、Flask、Playwright sync API、openpyxl、原生 JavaScript、HTML/CSS、unittest。

## Global Constraints

- 导航顺序严格为“查询 → 结果 → 移库 → 入库 → 匹配 → 设置”；页面为 /inbound，权限键为 inbound。
- CRM 操作只读；不得点击保存、提交、审核、批准、删除或编辑动作。
- 分页只能按已验证的 1, 2, 3, …, N 顺序读取，不能遍历当前可见页码按钮。
- 只有激活页码等于期望页且内容指纹未出现过时，当前页才算读取成功。
- 页码跳号、重复、无法识别或提前结束均使任务失败，并禁止 XLSX 下载。
- 结果仅保存在进程内存中并按工具账号隔离；不保存历史，不保存 CRM 原始导出。
- 页面按物料编码分组；XLSX 每个去重后的条码一行。
- 重复条码和应发数量差异必须报告；干净结果只保留重复条码的第一次出现。
- 第一阶段不向另一个 ERP 写数据，不支持一次输入多个装箱单。
- 真实验收使用 SH202607210002，且不得修改 CRM 数据。

## 文件职责

- 新建 inbound_extraction.py：装箱单号规范化、明细清洗、产品分组、警告与 XLSX。
- 新建 inbound_crm.py：B2B 装箱单导航、表头映射、页码识别与连续翻页。
- 修改 app.py：Worker 代理、任务状态、通道占用、路由、权限与导航。
- 新建 templates/inbound.html：独立入库页面、轮询、结果与下载。
- 修改 templates/accounts.html：新增“入库”权限选项。
- 修改 static/aurora.js、static/app_layout.css、static/aurora.css：入库导航元信息和六列布局。
- 新建 tests/test_inbound_extraction.py、tests/test_inbound_crm.py、tests/test_inbound_routes.py。
- 修改 tests/test_frontend_contract.py、tests/test_frontend_routes.py。

---

### Task 1: 纯数据整理与 XLSX

**Files:**
- Create: inbound_extraction.py
- Create: tests/test_inbound_extraction.py

**Interfaces:**
- Produces: normalize_packing_slip_no(value) -> str
- Produces: build_inbound_result(packing_slip_no, rows, page_counts) -> dict
- Produces: build_inbound_workbook(result) -> io.BytesIO
- Raw row keys: page、row_index、order_number、product_code、description、expected_quantity、serial。

- [ ] **Step 1: 写失败的规范化、分组和重复条码测试**

测试必须断言：
- 输入空格和小写的 sh202607210002 规范为 SH202607210002；
- 210524 抛出“装箱单号格式不正确”；
- 两页四行中存在一次重复条码时，pages_read 为 [1, 2]，total_serials 为 3，duplicate_serials 精确列出重复值；
- 分组保持物料首次出现顺序；
- 应发 2、实际 1 的产品 quantity_mismatch 为 true。

使用以下固定测试行：
- 第 1 页：916000024 / 中央净水机 / 应发 2 / SN00000001、SN00000002；
- 第 2 页：916000025 / 软水机 / 应发 2 / SN00000002、SN00000003。

- [ ] **Step 2: 运行测试确认缺少模块**

Run: .venv/bin/python -m unittest tests.test_inbound_extraction -v

Expected: FAIL with ModuleNotFoundError for inbound_extraction.

- [ ] **Step 3: 实现最小数据整理逻辑**

normalize_packing_slip_no 使用正则 ^SH\\d{8,}$。build_inbound_result 使用 OrderedDict 按首次出现的物料编码分组；丢弃缺少物料编码或条码的行；按条码全局去重；保留订单号首次出现顺序；每组使用第一个非空描述和第一个有效应发数量；生成 serial_count、quantity_mismatch、expected_total、total_serials、duplicate_serials、has_warnings、items 和干净 rows。同组出现冲突的非空应发数量时写入该组 warnings，不擅自改值。没有有效行时抛出“装箱单没有可用的产品条码明细”。

- [ ] **Step 4: 运行分组测试**

Run: .venv/bin/python -m unittest tests.test_inbound_extraction -v

Expected: PASS.

- [ ] **Step 5: 写失败的每条码一行 XLSX 测试**

断言首行七列严格为：装箱单号、订单号、物料编码、物料描述、应发数量、条码、来源页码；工作表总行数为 total_serials + 1；F 列按来源顺序保存 SN00000001、SN00000002；来源页码为整数。

- [ ] **Step 6: 实现内存工作簿**

使用 openpyxl.Workbook 写入七列，按 result["rows"] 来源顺序追加；设置表头、自动筛选、freeze_panes=A2 和列宽；保存到 io.BytesIO 并 seek(0)，不写入磁盘。

- [ ] **Step 7: 验证并提交**

Run: .venv/bin/python -m unittest tests.test_inbound_extraction -v

Expected: PASS with zero failures.

Commit:
git add inbound_extraction.py tests/test_inbound_extraction.py
git commit -m "feat: build inbound packing slip results"

---

### Task 2: CRM 连续分页读取器

**Files:**
- Create: inbound_crm.py
- Create: tests/test_inbound_crm.py

**Interfaces:**
- Produces: PackingSlipReadError(RuntimeError)
- Produces: map_table_rows(headers, rows, page_number) -> list[dict]
- Produces: PackingSlipCRMReader(session, log=None, progress=None).extract(packing_slip_no) -> dict
- Test boundaries: _go_to_first_page、_current_page_number、_total_pages、_read_current_page、_has_next_page、_advance_to_page、_wait_for_page。

- [ ] **Step 1: 写失败的表头映射和八页顺序测试**

建立 ScriptedReader：pages 为八个模拟页面，position 初始为 1；_read_current_page 把页码追加到 visited；_advance_to_page 只把 position 设为 expected_page。调用 _read_all_pages 后断言 visited 严格等于 [1,2,3,4,5,6,7,8]，page_counts 的 page 也严格相等。测试额外设置 visible_buttons=[1,3,5,7]，但生产循环不得读取该属性。

另用乱序表头 ["条码","物料描述","订单号","应发数量","物料编码"] 验证 map_table_rows 能得到 product_code=916000024、serial=SN00000001，证明不依赖固定列号。

- [ ] **Step 2: 运行测试确认缺少模块**

Run: .venv/bin/python -m unittest tests.test_inbound_crm -v

Expected: FAIL with missing inbound_crm.

- [ ] **Step 3: 实现表头别名和严格循环**

精确别名：
- order_number: 订单号、销售订单号；
- product_code: 物料编码、产品编码、商品编码、物料代码；
- description: 物料描述、产品描述、商品描述、品名、名称；
- expected_quantity: 应发数量、数量、出货数量、发货数量；
- serial: 条码、序列号、产品条码、SN。

map_table_rows 必须要求 product_code 与 serial，忽略空行、重复表头和汇总行，并增加 page 与 1-based row_index。

_read_all_pages 必须：
1. 主动进入第 1 页；
2. 每轮验证 actual == expected；
3. 读取并生成包含所有映射字段和行顺序的指纹；
4. 拒绝此前出现过的指纹；
5. 记录 {"page": actual, "row_count": len(page_rows)} 并回调进度；
6. 未到末页时只调用 _advance_to_page(actual + 1)；
7. 最后验证已读页码等于 range(1, last + 1)。

- [ ] **Step 4: 写并通过失败路径测试**

增加四个测试：
- 活跃页从 1 跳到 3，错误含“期望第 2 页，实际第 3 页”；
- 下一页内容指纹重复，错误含“页面重复”；
- 当前页无法识别，错误含“无法识别当前页码”；
- 总页数为 8 但下一页在第 7 页提前失效，错误含“未读完总页数”。

- [ ] **Step 5: 实现真实 CRM DOM 适配**

extract 的只读步骤：
1. 在可见 page/frame 中精确点击“B2B订单管理”，再精确点击“装箱单”。
2. 找到“装箱单号”标签关联输入框，填规范化号码，点击可见“查询”或“搜索”。
3. 结果表必须存在完全相同装箱单号，再打开该行明细。
4. 选择同时映射 product_code 和 serial 的可见表格。
5. 当前页优先读取 aria-current=page，再读 active/current/selected 分页元素，最后读页码输入框；结果必须唯一。
6. 总页数解析“共 N 页”；没有总页数时用明确禁用的“下一页”判断末页。
7. 翻页只点击启用的“下一页”，或在跳页输入框填精确 N+1 并回车；绝不收集并遍历可见数字按钮。
8. 等待条件同时满足激活页码为期望值且表格指纹已变化。

代码和选择器中不得包含保存、提交、审核、批准、删除或编辑动作。

- [ ] **Step 6: 验证并提交**

Run: .venv/bin/python -m unittest tests.test_inbound_crm -v

Expected: PASS for mapping、1–8 连续分页和全部失败路径。

Commit:
git add inbound_crm.py tests/test_inbound_crm.py
git commit -m "feat: read packing slip pages consecutively"

---

### Task 3: 后台任务、通道占用、权限与下载

**Files:**
- Modify: app.py
- Create: tests/test_inbound_routes.py
- Modify: tests/test_frontend_routes.py

**Interfaces:**
- Produces: CRMSession.extract_packing_slip(packing_slip_no, log=None, progress=None) -> tuple[bool, object]
- Produces: CRMWorker.extract_packing_slip(packing_slip_no, log=None, progress=None) -> tuple[bool, object]
- Produces: _empty_inbound_job(owner, packing_slip_no, slot_id, slot_label) -> dict
- Produces routes: GET /inbound、POST /api/inbound/start、GET /api/inbound/status、GET /api/inbound/export。

- [ ] **Step 1: 写失败的权限、账号隔离、成功与下载测试**

使用临时 CRM_DATA_DIR 和 FakeInboundWorker。Fake worker 通过 log/progress 回调返回第一页一条固定记录：订单 210524、物料 916000024、描述中央净水机、应发 1、条码 SN00000001。

测试必须断言：
- 匿名页面重定向，匿名 start API 返回 401；
- 无 inbound 权限账号页面/API 返回 403，导航不显示“入库”；
- 有 inbound 权限账号页面返回 200；
- 输入 210524 在选通道前返回 400；
- 后台任务完成后 pages_read=[1] 且返回 download_url；
- 另一个账号用该 job_id 查询和下载均为 404；
- 下载为 XLSX attachment，文件名含 SH202607210002。

- [ ] **Step 2: 运行测试确认路由不存在**

Run: .venv/bin/python -m unittest tests.test_inbound_routes -v

Expected: FAIL on missing page/API.

- [ ] **Step 3: 注册独立权限和页面**

在 PAGE_LINKS 的移库与匹配之间插入 permission=inbound、label=入库、href=/inbound；管理员默认权限和账号保存 allowed 集合加入 inbound；required_permission_for_path 为 /inbound 与 /api/inbound 前缀返回 inbound；新增页面路由。不得复用宽泛的 crm 权限。

- [ ] **Step 4: 通过现有 Worker 线程调用读取器**

CRMSession.extract_packing_slip 在 self.lock 内恢复浏览器、验证登录、调用 PackingSlipCRMReader.extract，将 PackingSlipReadError 转为明确失败；CRMWorker.extract_packing_slip 使用现有 _call，确保 Playwright 不跨线程。

- [ ] **Step 5: 实现账号隔离的内存任务**

新增 inbound_job_lock、inbound_jobs、latest_inbound_job_by_owner、latest_inbound_job_by_slot。状态字段包含 job/owner/装箱单/通道、stage、running/done/success、error、current_page、page_counts、最多 300 条 logs、result 和起止时间。

stage 只使用 waiting、navigation、search、reading、organizing、success、failed 七个值；status payload 保持同名字段，页面直接映射中文文案，避免前后端状态名分叉。

_run_inbound_job 调 worker；成功后才调用 build_inbound_result；无论结果如何都释放 slot 映射并结束任务。新增 _query_slot_has_running_inbound，让现有 _select_idle_query_workers_desc 跳过已预留通道。启动接口必须在创建线程前完成通道预留，避免竞态。

- [ ] **Step 6: 实现 API**

- start：规范化；同账号运行中冲突返回 409；选择一个空闲查询通道；预留；启动 daemon thread。
- status：无 job_id 或 latest=1 时取当前账号最近任务；外部账号任务返回 404；成功后才含完整 result 与 download_url。
- export：只读取当前账号成功任务的服务端 result，调用 build_inbound_workbook，用 send_file 返回“装箱单号_入库明细.xlsx”。

导出接口不得接受前端回传的结果行。

- [ ] **Step 7: 增加失败与通道占用测试**

Fake worker 返回失败“分页跳号：期望第 2 页，实际第 3 页”，断言任务 done、success=false 且无下载链接。设置运行中的 slot 映射，断言 _select_idle_query_workers_desc 不返回该通道。

- [ ] **Step 8: 验证并提交**

Run: .venv/bin/python -m unittest tests.test_inbound_routes tests.test_frontend_routes -v

Expected: PASS.

Commit:
git add app.py tests/test_inbound_routes.py tests/test_frontend_routes.py
git commit -m "feat: add inbound extraction jobs"

---

### Task 4: 入库页面、六按钮导航与权限控件

**Files:**
- Create: templates/inbound.html
- Modify: templates/accounts.html
- Modify: static/aurora.js
- Modify: static/app_layout.css
- Modify: static/aurora.css
- Modify: tests/test_frontend_contract.py
- Modify: tests/test_frontend_routes.py

**Interfaces:**
- DOM IDs: packingSlipInput、startInboundBtn、inboundStage、inboundSlot、inboundCurrentPage、inboundLogs、inboundSummary、inboundPageCounts、inboundWarnings、inboundProducts、downloadInboundBtn。
- Browser functions: startInboundExtraction、pollInboundStatus、renderInboundStatus、renderInboundResult、downloadInboundXlsx。

- [ ] **Step 1: 写失败的页面契约测试**

将 inbound.html 加入 page_templates。断言所有 DOM ID、data-aurora-page=inbound、start/status fetch、document.hidden 存在。断言 app.py 权限顺序为 crm/results/transfer/inbound/product-library/accounts；aurora.js 有 /inbound；两份共享 CSS 的 desktop/mobile nav 使用 repeat(6,minmax(0,1fr))；设置页有 value=inbound 的“入库”checkbox。

- [ ] **Step 2: 运行测试确认缺失**

Run: .venv/bin/python -m unittest tests.test_frontend_contract tests.test_frontend_routes -v

Expected: FAIL on missing template/nav/permission control.

- [ ] **Step 3: 创建独立 Aurora 页面**

沿用 crm.html 外壳与共享资源。页面显示输入、阶段、通道、当前页、实时日志、汇总、每页行数、警告、按物料编码展开的完整条码列表。所有 CRM 字符串经过本地 escapeHtml。只有 done、success 和 download_url 同时为真时启用下载。

- [ ] **Step 4: 实现开始、恢复、轮询和下载**

start POST packing_slip_no，并把 job ID 写入 sessionStorage.inbound_job_id。初始化优先恢复该 ID，否则请求 latest=1。页面隐藏时跳过轮询，但后台任务继续。下载只跳转服务端返回的同源 URL，不回传结果行。

- [ ] **Step 5: 增加权限控件和六列导航**

设置页在移库后加入入库 checkbox；aurora.js 加 /inbound 的入库 glyph；只把 static/app_layout.css 与 static/aurora.css 中 desktop/mobile 共享 nav 的五列网格改为六列网格，不改相邻布局。

- [ ] **Step 6: 扩展路由 smoke 测试**

把 /inbound 加入登录要求、退出链接、Aurora 页面映射；管理员访问返回 200 且包含 packingSlipInput。

- [ ] **Step 7: 验证并提交**

Run: .venv/bin/python -m unittest tests.test_frontend_contract tests.test_frontend_routes -v

Expected: PASS.

Commit:
git add templates/inbound.html templates/accounts.html static/aurora.js static/app_layout.css static/aurora.css tests/test_frontend_contract.py tests/test_frontend_routes.py
git commit -m "feat: add inbound extraction page"

---

### Task 5: 完整回归与真实只读验收

**Files:**
- Modify only when live evidence requires it: inbound_crm.py
- Test alongside any correction: tests/test_inbound_crm.py

- [ ] **Step 1: 运行所有入库相关测试**

Run:
.venv/bin/python -m unittest tests.test_inbound_extraction tests.test_inbound_crm tests.test_inbound_routes tests.test_frontend_contract tests.test_frontend_routes -v

Expected: zero failures and zero errors.

- [ ] **Step 2: 运行完整测试套件**

Run: .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v

Expected: zero failures and zero errors.

- [ ] **Step 3: 本地页面验收**

启动现有本地应用并打开 /inbound。确认导航严格为“查询、结果、移库、入库、匹配、设置”，设置页显示入库权限，离开后返回能恢复当前内存任务。

- [ ] **Step 4: 用 SH202607210002 做只读验收**

使用一个空闲且已登录的查询通道启动任务。证据必须包括：
- 实际使用的查询通道；
- pages_read 精确等于 1…last_page，没有缺页；
- 每页有效行数；
- 产品种类、应发总数、干净条码总数、重复数和数量差异；
- 完整分页成功后才出现 XLSX 下载；
- 工作簿行数等于 total_serials + 1，F 列条码顺序与页面干净结果一致。

不得点击 CRM 保存、提交、审核、批准、删除或编辑操作。

- [ ] **Step 5: 真实 DOM 不符时先写复现测试再修正**

只记录相关表头、分页文字和 DOM 属性，不读取账号、密码、cookie、localStorage 或 session 文件。把最小 DOM 事实做成测试 fixture，先验证失败，再对 inbound_crm.py 做最小选择器或别名修正，随后重跑定向和完整测试。

- [ ] **Step 6: 最终差异检查**

Run:
git diff --check
git status --short
git log --oneline -6

Expected: 无空白错误，只保留任务相关变更。如产生真实 DOM 修正，提交：
git add inbound_crm.py tests/test_inbound_crm.py
git commit -m "fix: align inbound reader with CRM pager"
