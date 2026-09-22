# 怡口CRM条码查询 / GYJ 采购入库工具

Flask + Playwright 的内部工具集，包含三条独立流程：

| 流程 | 入口 | 说明 |
|---|---|---|
| **CRM 条码查询** | `/crm` | 登录怡口 CRM，单条/批量条码查询，结果留 HTML、归档、导出 Excel |
| **GYJ 采购入库** | `/inbound` | 把 CRM 装箱单自动录入到 GYJ ERP（cloud.gyjerp.com）。先在 ERP 商品库查重 + 缺失补建，再填写采购入库单并保存草稿 |
| **GYJ 库存盘点** | `/inventory` | 从 GYJ 只读获取全仓库存，完成多人数量盘点、序列号核对、历史差异和 Excel 导出 |

三条流程都用 Playwright 在后台 Chromium 里跑（容器内用 Xvfb 提供虚拟显示），不需要真实桌面，能兼容 GYJ 的老式 Angular + Crystal Reports 页面。

## 项目文件

```text
app.py                                      # Flask 主应用（约 1 万行）
gyj_inbound.py                              # GYJ 采购入库：商品库预检 + 行录入 + 验 + 草稿保存
inbound_crm.py                              # CRM 装箱单详情抓取（Vue 单页 / DOM 抓数）
inbound_extraction.py                       # 装箱单 Excel / RPT CSV 解析
templates/
    index.html                              # 工具主页（CRM查询 / 移动 / 入库 tab 入口）
    inbound.html                            # GYJ 采购入库工作台
    crm.html                                # CRM 条码查询
    product_library.html / transfer.html    # 其它子模块
tests/
    test_gyj_inbound.py                     # GYJ 入库全流程单测（59 个）
    test_inbound_crm.py / test_inbound_extraction.py
    test_inbound_routes.py                  # Flask 入库 API 路由
    test_background_jobs.py / test_product_library_persistence.py …
Dockerfile / docker-compose.yml / requirements.txt
config.example.json / config.docker.example.json
CODEX_REFERENCE.md                          # 怡口 ERP / CRM 端 的非公开字段映射（旧文件）
gyj_e2e_evidence/                           # GYJ 端到端抓取留下的截图 + 状态（人工诊断用）
docs/                                       # 操作 / 排错相关参考
```

不会上传到 GitHub 的本地文件（已在 `.gitignore`）：

```text
config.json                                 # 可选：覆盖默认 CRM 地址和本地路径配置
barcode/ results/ session/                  # 查询结果 / 浏览器登录会话
gyj_session/admin/                          # GYJ 浏览器登录的 Cookie（命名卷持久化）
.venv/
*.log *.pid
```

## 本地运行

```bash
pip install -r requirements.txt
playwright install chromium
cp config.example.json config.json      # 可选
python app.py
```

默认端口 `5002`（macOS 端常驻用法见 `~/Library/LaunchAgents/com.crmbarcodequery.local.plist`）。
访问：

```text
http://127.0.0.1:5002/
http://127.0.0.1:5002/inbound           # GYJ 采购入库工作台
http://127.0.0.1:5002/inventory         # GYJ 库存盘点工作台
http://127.0.0.1:5002/crm
```

CRM 入口 / 账密在浏览器登一次后写到 `gyj_session/` 的 cookie / localStorage，进程不再保存明文。

## GYJ 采购入库流程（`/inbound`）

模块：`gyj_inbound.py`（writer + Page adapter）、`templates/inbound.html`、`tests/test_gyj_inbound.py`。

`save_packing_slip` 把整张装箱单拆成 4 段独立可重试的 stage：

```
pre_check      →  去 GYJ 「商品管理 → 商品信息」（/material/material）查每个唯一物料编码
creating       →  新建采购入库单（设供应商「昆山怡口净水」、填备注「装箱单号：xxx」）
filling        →  录入 N 行明细（按 100 条 SN 拆条、带 SN 的物料走序列号弹窗）
verifying      →  核对表头（供应商）+ 明细数 + 装箱单号
saving         →  保存（点「保存（Ctrl+S）」，从不点 保存并审核 / 审核）
```

任一 stage 抛 `GYJInboundError`，会先调用该 stage 的 `_rollback_*` 清理残留 modal / picker / 表头，再重试（默认 `STAGE_MAX_RETRIES = 2`，即首次 + 2 次重试）。三次都失败才整体中断。

关键策略：

- **预检路径走 GYJ 商品库**：picker 的搜索框偶尔漏码（受控组件的 stale input），商品库的表格搜索更权威。所有入库前的物料存在性判断都来自这一步。
- **缺失物料在商品库原地补建**：点「新增」打开完整的商品表单（不是 picker mini-create），条码字段用「光标末尾 → Backspace × 20 → 真键入」保住自定义编码（GYJ 自动生成的 13 位 EAN 不受 plain `fill()` 影响）；序列号下拉设「有」或「无」（通过 X 点选 `.ant-select-selection--single`）。
- **picker 仍异常时不再盲创**：`add_product_line` 的内层 create-on-fail 看到 `_prechecked_existing` 里已有该编码就直接抛 "GYJ 物料 X 预检查已确认存在，但表单选择器无法定位"，避开重试链里的无效 create。
- **从来不点「保存并审核」/「审核」/「提交」**：流程停在草稿，由人审核。

## GYJ 库存盘点流程（`/inventory`）

- 登录工具账号后，从顶部导航进入紧接在「入库」后的「盘点」。盘点与入库共用当前工具账号的 GYJ 登录状态；如状态失效，可在盘点页唯一的 GYJ 登录入口重新登录。
- 点击「开始新盘点」会只读加载全部启用商品和所有仓库的库存，不读取、显示或导出价格。先完成所有商品的数量盘点；初始零库存商品默认隐藏，扫描条码或搜索名称/条码即可打开。
- 第一台打开商品的设备获得编辑锁，页面每 20 秒续期；停止续期约 2 分钟后锁自动失效。已完成商品的 GYJ 账面库存约每 60 秒同步一次，其他设备可看到锁定人和实时进度。
- 所有数量提交后，仅有序列号差异的商品进入第二阶段。扫描结果区分匹配、账面缺失、实物多出、属于其他商品和重复扫描；完成全部序列号核对后再结束任务。
- 「历史任务」保留已完成盘点；有 `inventory` 权限的账号可查看差异和追加备注，只有管理员可归档、恢复差异或强制解锁。任务报告与历史差异报告均可导出 Excel，商品盘点报告包含「商品差异汇总」「序列号差异明细」两个工作表，且不含价格字段。
- 盘点任务、锁、历史、备注和归档状态保存在 `CRM_DATA_DIR/config/inventory_stocktake.sqlite3`。Docker 默认 `CRM_DATA_DIR=/app/data`，实际文件为 `/app/data/config/inventory_stocktake.sqlite3`，由 `crm_app_data` 卷持久化；不要运行 `docker compose down -v`。

## Docker 部署

容器内用 Xvfb 提供 `:99` 显示，让 Playwright 的 Chromium 能跑老式 Crystal Reports 页面。云服务器不需要装桌面。

### 1. 服务器装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker
docker --version && docker compose version
```

如在**国内**，把 Docker Hub 替换成可达镜像（写入 `/etc/docker/daemon.json`）：

```json
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
```

### 2. 拉取项目

```bash
git clone https://github.com/lovegongqi/CRM_barcode_query.git
cd CRM_barcode_query
```

### 3. 启动

```bash
mkdir -p barcode results session
docker compose up -d --build
```

访问 `http://服务器IP:5002/`。防火墙放行 TCP `5002`。

### 4. 可选：覆盖配置

```bash
cp config.docker.example.json config.json
```

然后编辑 `docker-compose.yml`，取消这一行的注释并改 config 挂载：

```yaml
volumes:
  - ./config.json:/app/config.json:ro
```

最后 `docker compose up -d --build`。

### 5. 停止、重启、更新

```bash
docker compose down          # 停止
docker compose restart       # 重启
git pull && docker compose up -d --build   # 更新
```

### 6. 架构

`Dockerfile` 来自 `python:3.11-slim`，**不锁架构**，构建时会按服务器 CPU 拉对应平台的 base image。

- 普通 x86 云服务器：`linux/amd64`（QNAP / 群晖 x86 系列、阿里云 / 腾讯云）
- ARM 服务器（群晖 DS220+ 之类、`M1/M2 Mac`）：`linux/arm64`

`buildx` 多架构：

```bash
docker buildx create --use --name multiarch-builder
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t 你的用户名/crm-barcode-query:latest --push .
```

`docker-compose.yml` 改 `image: 你的用户名/crm-barcode-query:latest`，服务器上 `docker compose pull && docker compose up -d`。

> **重要**：Mac（M 系列是 arm64）上 build 默认产出 arm64。往 x86 群晖 / 服务器上传之前必须 `docker build --platform linux/amd64 ...` 一次，否则 NAS 拉起来会因为平台不匹配立即退出。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

当前 12 个测试模块、~244 个用例。`tests/test_gyj_inbound.py` 覆盖 GYJ writer / page adapter 的 mock，不依赖真实 GYJ。

## 数据持久化

`docker-compose.yml` 里：

```yaml
volumes:
  - crm_app_data:/app/data
  - crm_browser_session:/app/session
```

完整产品库 / 登录态 / 历史记录都落在这两个命名卷里，升级容器不会丢。

主要文件：

```text
/app/data/config/product_library.json
/app/data/config/distributor_history.json
/app/data/config/barcode_data.json
/app/data/config/accounts.json
/app/data/config/runtime_config.json
/app/data/config/crm_credentials.json
/app/data/config/crm_slot_state.json
/app/data/config/transfer_records.sqlite3
/app/data/barcode/*.html
/app/data/barcode/archived/*.html
/app/session/admin/Default/Cookies       # GYJ Chrome cookie（加密）
```

不要 `docker compose down -v`，否则会清空所有卷数据。

## 安全建议

- **不要把自己的 `config.json` 上传到公开仓库**。
- **SSH / GYJ 凭证**别贴在聊天记录里——对话日志会随 token 一起存档；长期凭证请放进 1Password / Keychain。
- 云服务器防火墙只放行可信 IP 访问 `5002`。
- 公网长期使用：套 Nginx + HTTPS + 访问密码。
- Docker Hub 在国内可能不可达；用 `docker.m.daocloud.io` 这类镜像加速并写到 daemon.json。

## 排错

- GYJ 登录态过期：在 `/inbound` → GYJ 采购入库 tab → 点「后台登录 GYJ」重新登（要走一次验证码）。
- `playwright install` 在容器构建时拉 chromium-headless-shell 比较慢（百兆+）；构建网络差时先单独 `playwright install chromium --with-deps` 缓存到 host。
- 服务起不来：`docker compose logs -f`；前端跑到一半 page 卡死：`docker compose restart`。
