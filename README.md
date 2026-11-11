# DataAgent Pro

> 上传一份表格，用一句中文说清你想看什么，得到一张可交互、可导出、可追问的图表。

`DataAgent Pro` 是一个「拖字段 + 自然语言」的数据可视化工作台，交互参考 Microsoft Data Formulator：

- **两条出图路径**：拖字段 → 确定性编译（不调用模型）；复杂需求 → 自然语言 → 模型写代码并自动修复；
- 后端 **FastAPI + pandas + Plotly**，前端 **原生 JS**，没有构建步骤；
- 大模型走 **OpenAI 兼容协议**，默认使用 **SiliconFlow 免费模型**，主模型限流时自动降级；
- 模型生成的代码不会直接 `exec`，而是先过 **AST 白名单 + 受限 builtins + 超时** 的沙箱；
- 执行报错会把真实 traceback 回传给模型 **自动修复**，最多重试 2 次；
- 数据只在本机解析并保存在本机，**发给模型的只有列名、类型和统计摘要**；
- 内置示例数据、访问码门禁与 Dockerfile，可直接部署成 HR 点开即用的公网演示。

---

## 快速开始

```bat
cd DataAgent_Pro

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

### 填写 API Key

打开项目根目录的 `.env`，把 Key 填在等号后面（**Key 故意留空，需要你自己填**）：

```ini
DATAAGENT_API_KEY=sk-你的SiliconFlow密钥
```

接口地址已经固定为：

```ini
DATAAGENT_BASE_URL=https://api.siliconflow.cn/v1/chat/completions
```

> `.env` 已在 `.gitignore` 中，不会被提交。若 Key 曾经泄露，请到
> <https://cloud.siliconflow.cn/account/ak> 吊销并重新生成。

### 启动

```bat
python run.py
```

浏览器打开 <http://127.0.0.1:8000>。

首屏会自动载入 `sample_data/demo_sales.csv`（360 行 × 19 列的模拟销售明细），
并生成第一张推荐图表，不需要先上传文件。换成自己的数据：直接把文件拖进左栏，或点「换成我的数据」。

端口被占用时可以换一个，不用改代码：

```bat
set DATAAGENT_PORT=8010 && python run.py
```

生产部署建议关闭 reload：

```bat
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

---

## 免费模型选型

默认模型链（全部是 SiliconFlow 官方标注 **¥0** 的模型）：

| 顺序 | 模型 | 作用 |
| --- | --- | --- |
| 主 | `deepseek-ai/DeepSeek-V4.1-Flash` | 免费档旗舰，代码一次写对率最高 |
| 兜底 1 | `Qwen/Qwen3-8B` | 中文 + 代码均衡，8B 级延迟低 |
| 兜底 2 | `Qwen/Qwen3.5-4B` | 免费档里限速最宽松，主模型 429 时最快接上 |
| 兜底 3 | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 带思维链，代码报错自愈时更可靠 |
| 兜底 4 | `THUDM/GLM-4-9B-0414` | 中文语义好，作为最后一层 |

免费名单和限速会调整，部署前以
<https://cloud.siliconflow.cn/pricing> 为准。改模型不需要动代码，只改 `.env`：

```ini
DATAAGENT_MODEL=deepseek-ai/DeepSeek-V4.1-Flash
DATAAGENT_FALLBACK_MODELS=Qwen/Qwen3-8B,Qwen/Qwen3.5-4B,deepseek-ai/DeepSeek-R1-0528-Qwen3-8B,THUDM/GLM-4-9B-0414
```

也内置了其他供应商预设（`/api/models` 可看完整目录）：

| provider | 说明 | 需要 Key |
| --- | --- | --- |
| `siliconflow` | 国内直连，默认 | 是 |
| `openrouter` | 海外，`:free` 模型有每日限额 | 是 |
| `groq` | 免费额度，速度极快 | 是 |
| `ollama` | 本地模型，完全免费 | 否 |
| `custom` | 任意 OpenAI 兼容端点 | 视情况 |

切换到 Ollama 示例：

```ini
DATAAGENT_PROVIDER=ollama
DATAAGENT_BASE_URL=http://127.0.0.1:11434/v1/chat/completions
DATAAGENT_MODEL=qwen2.5-coder:7b
DATAAGENT_FALLBACK_MODELS=qwen3:8b
```

---

## 相比「能跑的原型」做了什么

| # | 能力 | 为什么重要 |
| --- | --- | --- |
| 1 | **数据集会话化** | 上传一次得到 `dataset_id`，换问题、多轮追问都不用重新上传；TTL + LRU 控制内存，原始文件落盘，重启可恢复 |
| 2 | **SSE 流式生成** | 思考 → 生成代码 → 沙箱执行 → 修复 → 完成，每个阶段实时显示，不用盯着白屏猜 |
| 3 | **受限代码执行** | 禁 import / 函数定义 / while / 下划线属性；禁 `open`、`eval`、`read_*`、`to_*`、`write_html` 等；`range` 有长度上限，执行有超时 |
| 4 | **失败自愈** | 把真实 traceback 回传给模型做最小修复，最多 2 次，页面上标记「自愈 N 次」 |
| 5 | **多模型降级** | 429 / 5xx / 超时自动切下一档免费模型；401 直接给出可操作提示，不白等 |
| 6 | **结果缓存** | 按「数据指纹 + 规范化问题 + 主模型」缓存 30 分钟，同样问题不重复烧额度 |
| 7 | **数据画像与推荐问题** | 行列数、类型、缺失率、异常值、相关性、时间跨度、质量评分；推荐问题由规则生成，**不消耗 token** |
| 8 | **多轮上下文** | 自动带上最近 3 轮的代码，支持「把柱子改成折线」「只保留前 10 名」这类增量修改 |
| 9 | **四种导出** | PNG（浏览器端）、交互 HTML、CSV（还原 trace 数据）、一页带需求与数据概况的分析报告 |
| 10 | **历史与会话** | SQLite 持久化会话、消息、图表、模型调用指标；历史抽屉可回看并继续追问 |
| 11 | **可运维** | 统一错误码 + `request_id`、按 IP 滑动窗口限流、可选访问令牌、`/api/health`、`/api/metrics` |
| 12 | **体验细节** | 亮 / 暗主题、拖拽上传、Ctrl / ⌘ + Enter 生成、Plotly 本地直供（版本与后端一致）、响应式布局 |
| 13 | **拖字段工作台** | 三栏布局（字段 / 画布 / 视觉通道），拖拽或点击分配 X / Y / 颜色，支持 6 种图、聚合、排序、Top N；由后端确定性编译成 Plotly 代码，**不消耗模型额度** |
| 14 | **公网演示** | 内置示例数据首屏自动出图、访问码门禁、Dockerfile / `render.yaml`、上传与行数限额、每分钟限流 |

---

## 项目结构

```text
DataAgent_Pro/
├─ app/
│  ├─ api/               # HTTP 入口：路由 + pydantic 模型
│  ├─ core/              # 配置、错误体系、日志、中间件、SSE
│  ├─ llm/               # 免费模型目录、提示词、OpenAI 兼容客户端
│  ├─ security/          # AST 白名单 + 受限执行沙箱
│  ├─ services/          # 解析 / 画像 / 编排 / 缓存 / 历史 / 导出
│  └─ main.py            # 应用装配、异常处理、静态资源
├─ static/
│  ├─ index.html         # 三栏工作台
│  ├─ app.js             # 拖拽 / 视觉通道 / SSE / 导出 / 历史
│  ├─ styles.css         # 自写设计系统（亮 / 暗）
│  └─ access.html        # 访问码登录页（公网演示）
├─ sample_data/
│  └─ demo_sales.csv     # 内置演示数据（360 行 × 19 列）
├─ tests/                # 125 个离线单元测试（unittest）
├─ data/                 # 运行期生成：上传、导出、SQLite（已 gitignore）
├─ .env                  # 你的本地配置（已 gitignore）
├─ .env.example          # 配置模板
├─ requirements.txt
├─ Dockerfile            # 公网镜像
├─ render.yaml           # Render 一键部署
├─ DEPLOY.md             # 公网部署与访问码说明
├─ pyproject.toml        # ruff 规则
└─ run.py                # python run.py 开发启动
```

### 一次请求的调用链

```text
浏览器
  │ POST /api/datasets
  ▼
api/routes.py ──► data_service.load_table ──► 解析 / 清洗 / 画像
  │                    └──► dataset_store.put（内存 + data/uploads）
  │
  │ POST /api/chat/stream
  ▼
chart_service.ChartService.generate
  ├─ 结果缓存命中？ ──是──► 直接返回
  ├─ prompts.build_generate_messages（数据字典 + 历史代码）
  ├─ llm.client.LLMClient.chat（多模型降级）
  ├─ security.sandbox.run_chart_code（校验 → 执行 → 超时）
  ├─ 失败？ ──► prompts.build_repair_messages（traceback 回传）──► 再执行
  ├─ history_store.add_message / record_metric
  └─ SSE: stage thinking → generated → executing → done → result
```

---

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 版本、模型链、Key 是否配置、用量与存储统计 |
| GET | `/api/models` | 供应商预设、免费模型目录、当前选择 |
| GET | `/api/metrics` | 数据集 / 缓存 / 历史 / token 用量 |
| POST | `/api/datasets` | 上传表格（multipart），返回 `dataset_id`、画像、预览、推荐问题 |
| POST | `/api/demo` | 载入内置示例数据，返回和上传接口相同的结构（带 `demo=true`） |
| POST | `/api/access` | 提交访问码，换取 7 天有效的 HttpOnly Cookie |
| GET | `/api/datasets/{id}` | 查看数据画像 |
| DELETE | `/api/datasets/{id}` | 释放数据集 |
| GET | `/api/datasets/{id}/specs` | 按列类型推荐视觉通道配置（不调用模型） |
| POST | `/api/chart/spec` | 按视觉通道确定性生成图表（不调用模型） |
| POST | `/api/sessions` | 新建会话 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{id}` | 会话详情（含每轮代码与图表） |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| POST | `/api/chat` | 同步生成图表 |
| POST | `/api/chat/stream` | **SSE 流式生成**（前端默认走这个） |
| GET | `/api/charts` | 最近图表列表 |
| GET | `/api/charts/{message_id}` | 取回单张历史图表 |
| POST | `/api/export` | 导出 `png` / `html` / `csv` / `report` |
| GET | `/api/exports/{filename}` | 下载导出文件 |

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/datasets -F "file=@sales.csv"

curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"<上一步返回>","prompt":"按地区统计销售额并画柱状图"}'

# 不调用模型：直接按视觉通道出图
curl -X POST http://127.0.0.1:8000/api/chart/spec \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":"<id>","spec":{"chart_type":"bar","x":"区域","y":"销售额","aggregation":"sum","sort":"desc","top_n":10}}'
```

### SSE 事件格式

```text
data: {"type":"stage","stage":"thinking","message":"正在让 deepseek-ai/DeepSeek-V4.1-Flash 读数据、写代码…","model":"deepseek-ai/DeepSeek-V4.1-Flash"}

data: {"type":"stage","stage":"executing","message":"在受限沙箱中执行（第 1 次）","attempt":1}

data: {"type":"result","data":{"chart_json":"{...}","code":"fig = px.bar(...)","attempts":1,"cached":false,...}}

data: {"type":"end"}
```

`stage` 取值：`cache` / `thinking` / `generated` / `executing` / `repairing` / `repaired` / `done` / `error`。

---

## 环境变量

所有变量都有默认值，只有 Key 是必需的。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATAAGENT_API_KEY` | 空 | 模型平台 Key，**需要你填** |
| `DATAAGENT_PROVIDER` | `siliconflow` | 供应商预设 |
| `DATAAGENT_BASE_URL` | SiliconFlow 地址 | OpenAI 兼容端点 |
| `DATAAGENT_MODEL` | `deepseek-ai/DeepSeek-V4.1-Flash` | 主模型 |
| `DATAAGENT_FALLBACK_MODELS` | 四个免费模型 | 逗号分隔的兜底链 |
| `DATAAGENT_TEMPERATURE` | `0.1` | 生成温度，画图建议低 |
| `DATAAGENT_LLM_TIMEOUT` | `90` | 单次模型调用超时（秒） |
| `DATAAGENT_LLM_MAX_RETRIES` | `1` | 同一模型的重试次数 |
| `DATAAGENT_MAX_REPAIR_ATTEMPTS` | `2` | 自愈修复次数 |
| `DATAAGENT_HISTORY_TURNS` | `3` | 带多少轮历史代码 |
| `DATAAGENT_MAX_FILE_SIZE` | `104857600` | 上传大小上限（100 MB） |
| `DATAAGENT_MAX_ROWS` | `200000` | 超过则截断 |
| `DATAAGENT_EXEC_TIMEOUT` | `10` | 图表代码执行超时（秒） |
| `DATAAGENT_MAX_DATASETS` | `30` | 内存中最多保留的数据集 |
| `DATAAGENT_DATASET_TTL` | `86400` | 数据集有效期（秒） |
| `DATAAGENT_RESULT_CACHE_TTL` | `1800` | 结果缓存有效期（秒） |
| `DATAAGENT_RATE_LIMIT` | `60` | 每 IP 每分钟 API 请求上限 |
| `DATAAGENT_LLM_RATE_LIMIT` | `0` | 每 IP 每分钟模型调用上限（0 = 不限，只作用于 `/api/chat`） |
| `DATAAGENT_LLM_DAILY_LIMIT` | `0` | 每 IP 每天模型调用上限（0 = 不限） |
| `DATAAGENT_AUTH_TOKEN` | 空 | 设置后 `/api` 需要 `X-API-Token` |
| `DATAAGENT_ACCESS_CODE` | 空 | 公网访问码；设置后浏览器需先输入，脚本可带 `X-Access-Code` |
| `DATAAGENT_DEMO` | `1` | 首屏是否自动载入内置示例数据 |
| `DATAAGENT_CORS_ORIGINS` | 空 | 逗号分隔的跨域白名单 |
| `DATAAGENT_LOG_LEVEL` | `INFO` | 日志级别 |
| `DATAAGENT_LOG_LLM_CODE` | `0` | 设为 1 时把模型代码打进日志（排障用） |

---

## 测试

全部离线，不需要 API Key，也不会发网络请求。

```bat
python -m compileall -q app tests run.py
python -m unittest discover -s tests -t .
node --check static/app.js
```

覆盖范围：

- 表格解析（UTF-8 / GB18030 / TSV / JSON / 分隔符嗅探 / 非法格式）
- 清洗与画像（重名列、空行空列、缺失率、相关性、时间跨度）
- 沙箱（合法代码、import / open / dunder / while / lambda / 文件写入拦截、运行时报错）
- 编排（成功、缓存、`refresh` 绕过缓存、自愈、超过重试次数、会话不匹配）
- 模型客户端（429 降级、401 不降级、解析失败切换、重试后成功、用量统计）
- 历史库（会话、消息、最近轮次、列表排序、删除、指标）
- 导出（HTML / CSV 含饼图 / 报告转义 / 路径穿越拦截 / PNG 降级提示）
- 数据集仓库（容量淘汰、过期清理、重启恢复、磁盘清理）
- 视觉通道编译（6 种图、聚合 / 排序 / Top N、颜色拆分、非法配置、推荐可执行）
- 演示数据与访问码（示例数据载入与复用、门禁 Cookie / 请求头、错误访问码）

最近一次结果：**125 tests OK**；另跑过一轮「真服务 + 假模型」端到端冒烟（上传 → SSE → 缓存 → 三种导出 → 历史 → 参数校验），以及真实浏览器的拖字段 / 推荐图表 / AI 生成验证。

---

## 公网部署（HR 点开即用）

完整步骤见 [DEPLOY.md](DEPLOY.md)，最短路径：

**0 元（推荐先跑通）**：本机 + Cloudflare 快速隧道，不注册、不绑卡：

```powershell
winget install --id Cloudflare.cloudflared --silent   # 只需一次
.\启动公网演示.bat                                      # 以后每次演示双击即可
```

窗口会打印公网链接和访问码，Ctrl+C 即停。注意：电脑需保持开机，隧道重启后域名会变。

**固定链接**：推到 GitHub → Render / Railway / 腾讯云轻量（详见 DEPLOY.md）。⚠️ `onrender.com` 国内实测无法访问。

Docker 本地验证：

```bash
docker build -t dataagent-pro .
docker run -p 8000:8000 \
  -e DATAAGENT_API_KEY=sk-xxx \
  -e DATAAGENT_ACCESS_CODE=你的访问码 \
  dataagent-pro
```

公网演示默认限额：5 MB / 2 万行 / 5 个数据集 / 1 小时过期 / 每分钟 30 次请求，
其中自然语言生成单独限额为**每 IP 每分钟 3 次、每天 50 次**。
拖字段出图这条主路径不调用模型，也不消耗 AI 额度，因此即使模型限额用完，工作台仍然可用。

---

## 安全边界（请务必读）

1. **不信任模型生成的代码**。执行前做 AST 白名单校验，禁掉 import、函数 / 类定义、循环、下划线属性、文件 / 网络 / 进程入口；只给数学与容器相关 builtins，`range` 有长度上限。
2. **这不是操作系统级沙箱**。Python 层的限制只能「显著抬高攻击成本」。对公网开放时，请把执行器换成独立子进程 / 容器（例如 nsjail、gVisor、Firecracker），并限制 CPU、内存、网络。
3. **上传的文件默认可信**。服务会解析 CSV / Excel / JSON，但不防恶意构造的压缩炸弹式表格；对外服务时请加文件大小、行数、列数与解析时间限制（本项目已限制大小和行数）。
4. **API Key 不要进代码库**。只放在 `.env`，`.env` 已 gitignore；日志只会打印 `sk-abc***1234` 形式的掩码。
5. **访问控制**。`DATAAGENT_AUTH_TOKEN` 适合脚本 / 内网调用（请求头令牌）；给浏览器演示用 `DATAAGENT_ACCESS_CODE`，验证通过后签发 7 天 HttpOnly Cookie。两者都可以留空，但公网部署至少启用一个并保留限流。

---

## 常见问题

**Q：页面提示「待填 Key」或接口返回 `AUTH_ERROR`？**
`.env` 里的 `DATAAGENT_API_KEY` 还是空的。填好后重启服务；Key 无效 / 过期 / 没有该模型权限也会返回 401。

**Q：一直 429 / 限流？**
免费模型有固定 RPM。系统会自动重试并切到兜底模型；也可以把 `DATAAGENT_MODEL` 换成 `Qwen/Qwen3.5-4B`（限速最宽松），或稍等几十秒。

**Q：PNG 导出返回 501？**
服务端没装 `kaleido`。这是预期行为：页面上的「PNG」按钮会优先用浏览器端 Plotly 导出，不依赖 kaleido。要服务端出图就 `pip install kaleido`。

**Q：图表执行超时 / 数据点太多？**
默认执行上限 10 秒、图表 JSON 上限 6 MB。让模型先聚合（如「只显示前 10 名」）通常能解决。

**Q：上传后重启服务，数据集还在吗？**
在。原始文件保存在 `data/uploads`，有 TTL（默认 24 小时）；重启后首次访问会懒加载恢复。

**Q：历史记录存在哪里？**
`data/history.sqlite3`。删掉它就会清空历史（不影响已上传文件的恢复）。

**Q：模型总写错列名怎么办？**
提示词里已经带了完整列名、类型、缺失率和样本值。若仍失败，可在问题里直接写列名，例如「用 `销售额` 列按 `地区` 汇总」。

---

## 开发约定

- 后端分层：`api` 只做校验与响应组装，业务在 `services`，模型调用在 `llm`，执行隔离在 `security`。
- 所有可预期错误继承 `AppError`，带稳定的 `code`、HTTP 状态码和 `hint`；前端据此给出不同引导。
- 不引入前端构建链：改完 `static/` 刷新即可。
- 代码风格：`ruff`（配置见 `pyproject.toml`），行宽 100。
- 新增供应商：在 `app/llm/catalog.py` 加预设即可，客户端本身与供应商无关。

---

用一句话开始：

> 「按月份统计销售额，画折线图并标出最高点」
