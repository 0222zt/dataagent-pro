# 公网部署（HR 点开即用）

目标：把 DataAgent Pro 部署成一个链接，HR 不需要装 Python / Node，手机或电脑打开就能拖字段出图。

推荐顺序：

1. **0 元：本机 + Cloudflare 快速隧道**：不注册、不绑卡，`trycloudflare.com` 国内实测可达。代价是电脑要开机、每次重启域名会变。
2. **Render**：连 GitHub 自动构建。⚠️ 国内实测 `onrender.com` 无法访问，只适合海外访问者。
3. **Railway**：同样识别 `Dockerfile`，但免费额度只有 30 天 $5，之后 $1/月跑不住常驻服务。
4. **阿里云 / 腾讯云**：国内最稳、可长期放简历；8000 端口直连不需要备案。

---

## 零、0 元方案：本机 + Cloudflare 隧道

适合“今天就要把链接发给 HR”的场景，全程 0 元、无需注册任何账号。

### 一次性准备

```powershell
winget install --id Cloudflare.cloudflared --silent
```

### 每次演示

双击项目根目录的 **`启动公网演示.bat`**，或在 PowerShell 里：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_public_demo.ps1
```

脚本会依次做四件事：

1. 用公网演示参数启动本地服务（5 MB / 2 万行 / 5 数据集 / 1 小时 / 30 次每分 / AI 3 次每分 + 50 次每天）
2. 建立 Cloudflare 快速隧道
3. 打印公网链接和访问码，并写入 `public_url.txt`（已加入 `.gitignore`）
4. 按 Ctrl+C 时同时关掉隧道和服务

实测输出形如：

```
公网链接： https://xxxx-yyyy-zzzz.trycloudflare.com
访问码：   hrdemo2026
```

### 必须知道的限制

| 限制 | 影响 | 规避 |
| --- | --- | --- |
| 电脑关机 / 休眠 / 断网 → 链接立刻失效 | HR 打开会看到错误页 | 电源计划里把“睡眠”设为“从不”；投递后保持开机 |
| 每次重启隧道域名都变 | 简历里写死的链接会过期 | 面试窗口期内不重启；或改用腾讯云轻量（68 元/年）拿固定 IP |
| Cloudflare 快速隧道无 SLA | 偶发连不上，重开脚本即可 | 演示前自己先点一次预热 |
| 走的是你家宽带上行 | 上传大文件会慢 | 演示用内置示例数据，5 MB 以内很快 |

> 这是一条**诚实可用**的免费路线：能跑、能让 HR 点开、不花钱。但它不适合当成“长期在线的产品链接”写进简历——若后续愿意花 5.7 元/月，换成腾讯云轻量的固定 IP 即可。

---

## 一、部署前准备

| 需要 | 说明 |
| --- | --- |
| `DATAAGENT_API_KEY` | SiliconFlow 的 Key。建议**单独申请一个低额度 Key**，不要用主 Key |
| `DATAAGENT_ACCESS_CODE` | 访问码，建议 8 位以上随机串；留空则不设门禁 |
| GitHub 仓库 | Render / Railway 都从仓库构建 |

生成一个访问码（任选）：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(9))"
```

---

## 二、Render 部署（推荐）

1. 把项目推到 GitHub（`.env` 已被忽略，不会泄露 Key）。
2. 打开 <https://dashboard.render.com/blueprints> → **New Blueprint Instance**。
3. 选择仓库，Render 会读取根目录的 `render.yaml`。
4. 在环境变量里填写：
   - `DATAAGENT_API_KEY` = 你的 SiliconFlow Key
   - `DATAAGENT_ACCESS_CODE` = 访问码
5. 点 **Apply**，等待 Docker 构建（首次约 3–6 分钟）。
6. 打开 `https://<服务名>.onrender.com`，输入访问码即可。

`render.yaml` 已内置公网资源限制：

| 变量 | 值 | 原因 |
| --- | --- | --- |
| `DATAAGENT_MAX_FILE_SIZE` | `5242880`（5 MB） | 免费档内存有限 |
| `DATAAGENT_MAX_ROWS` | `20000` | 避免大表拖慢沙箱 |
| `DATAAGENT_MAX_DATASETS` | `5` | 控制内存占用 |
| `DATAAGENT_DATASET_TTL` | `3600` | 1 小时后自动清理 |
| `DATAAGENT_RATE_LIMIT` | `30` | 每分钟每 IP 30 次，够正常交互又能防刷 |
| `DATAAGENT_LLM_RATE_LIMIT` | `3` | 只有自然语言生成算额度，每分钟每 IP 3 次 |
| `DATAAGENT_LLM_DAILY_LIMIT` | `50` | 每 IP 每天 50 次模型调用，把最坏情况锁死 |

> Render 免费档 15 分钟无访问会休眠，首次打开需要 30–60 秒唤醒。简历投递前可以先自己点一次预热。

---

## 三、Railway 部署

1. <https://railway.app> → **New Project** → **Deploy from GitHub repo**。
2. Railway 自动识别 `Dockerfile`，无需改启动命令。
3. 在 **Variables** 里添加：
   ```
   DATAAGENT_API_KEY=sk-xxx
   DATAAGENT_ACCESS_CODE=你的访问码
   DATAAGENT_DEMO=1
   DATAAGENT_MAX_FILE_SIZE=5242880
   DATAAGENT_MAX_ROWS=20000
   DATAAGENT_MAX_DATASETS=5
   DATAAGENT_DATASET_TTL=3600
   DATAAGENT_RATE_LIMIT=30
   DATAAGENT_LLM_RATE_LIMIT=3
   DATAAGENT_LLM_DAILY_LIMIT=50
   ```
4. **Settings → Networking → Generate Domain**，得到公网链接。

---

## 四、阿里云 / 腾讯云（国内最稳）

服务器装好 Docker 后：

```bash
git clone <你的仓库> && cd DataAgent_Pro
docker build -t dataagent-pro .
docker run -d --name dataagent \
  -p 8000:8000 \
  --restart unless-stopped \
  -e DATAAGENT_API_KEY=sk-xxx \
  -e DATAAGENT_ACCESS_CODE=你的访问码 \
  -e DATAAGENT_DEMO=1 \
  -e DATAAGENT_MAX_FILE_SIZE=5242880 \
  -e DATAAGENT_MAX_ROWS=20000 \
  -e DATAAGENT_MAX_DATASETS=5 \
  -e DATAAGENT_DATASET_TTL=3600 \
  -e DATAAGENT_RATE_LIMIT=30 \
  -e DATAAGENT_LLM_RATE_LIMIT=3 \
  -e DATAAGENT_LLM_DAILY_LIMIT=50 \
  dataagent-pro
```

安全组放行 8000 端口，访问 `http://服务器公网IP:8000` 即可。
若要用域名 + HTTPS，建议前面挂 Nginx / Caddy 反代，并保留 `X-Forwarded-Proto` 头（项目已用 `--proxy-headers`）。

---

## 五、本地验证（部署前）

```powershell
copy .env.example .env
# 填写 DATAAGENT_API_KEY、DATAAGENT_ACCESS_CODE
python run.py
```

打开 <http://127.0.0.1:8000>：

1. 首屏应自动载入示例数据并出现一张图。
2. 左侧拖字段 / 点推荐图表，应秒出图（这条路径不调用模型）。
3. 右侧 AI 助手输入一句需求，应看到阶段进度并出图。
4. 刷新页面后如果要求访问码，说明门禁生效。

命令行自检：

```powershell
python scripts/smoke_demo.py http://127.0.0.1:8000 你的访问码
```

也可以逐条 curl：

```powershell
curl http://127.0.0.1:8000/api/health
curl -X POST http://127.0.0.1:8000/api/demo
curl -X POST http://127.0.0.1:8000/api/access -H "Content-Type: application/json" -d "{\"code\":\"你的访问码\"}"
```

带访问码调用其他接口：

```powershell
curl http://127.0.0.1:8000/api/charts -H "X-Access-Code: 你的访问码"
```

---

## 六、安全说明

- **Key 只在服务端**：前端拿不到 `DATAAGENT_API_KEY`；`/api/health` 只返回是否已配置。
- **访问码不是强认证**：它只防止链接被随意转发。Cookie 是 `HttpOnly` + `SameSite=Lax`，有效期 7 天；换访问码后旧 Cookie 立即失效。
- **代码沙箱**：模型生成的代码在受限命名空间里执行，禁用了文件、网络、模块导入等敏感操作。
- **建议同时开启限流**（默认已开）；公网不要设 `DATAAGENT_RATE_LIMIT=0`。
- **AI 额度独立计数**：`DATAAGENT_LLM_RATE_LIMIT` / `DATAAGENT_LLM_DAILY_LIMIT` 只作用于 `/api/chat`，超限返回 `LLM_QUOTA_EXCEEDED`，拖字段出图仍然可用。
- **用低额度 Key 部署**：即使访问码被转发，损失也封顶。不要用主账号 Key。
- **不要提交 `.env`**：`.gitignore` 和 `.dockerignore` 都已排除。

---

## 七、简历怎么写（诚实版）

> **DataAgent Pro**（个人项目）
> - 基于 FastAPI + Plotly 实现三栏式图表工作台：拖拽字段 → 确定性编译出图，复杂需求转自然语言 → 免费模型链生成代码 → 沙箱执行 → 失败自愈。
> - 设计模型降级链、结果缓存、多轮会话与历史记录；用受限执行环境隔离生成代码。
> - 提供在线演示：内置示例数据、访问码门禁、Docker 一键部署与公网资源限额。
>
> 参考并二次开发了 Microsoft Data Formulator（MIT）的交互思路；核心后端、沙箱、自愈与部署由本人实现。

不要写“从零开发 Data Formulator”或把微软的 prompt / agent 设计算成自己的成果。

---

## 八、常见问题

| 现象 | 处理 |
| --- | --- |
| 打开一直转圈 / 502 | Render 免费档在唤醒，等 30–60 秒再刷新 |
| 显示“需要访问码” | 输入分享者给的访问码；忘记就改 `DATAAGENT_ACCESS_CODE` 重新部署 |
| 上传提示超过 5 MB | 公网演示限制；在 Excel 里抽样或另存为 CSV |
| AI 生成报 429 | 免费模型限流，稍等几秒点“重新生成”，或换兜底模型 |
| 图表空白但无报错 | 检查 `/vendor/plotly.min.js` 是否 200；镜像里必须安装 `plotly` |
| 部署后数据没了 | 免费平台文件系统是临时的；演示数据每次启动自动重建，用户上传的数据会随实例重启丢失（符合演示定位） |
| 想关闭演示数据 | 设 `DATAAGENT_DEMO=0` |
| 想彻底公开（不要访问码） | 不设 `DATAAGENT_ACCESS_CODE`，并保留限流 |
