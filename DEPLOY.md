# 公网部署（HR 点开即用）

目标：把 DataAgent Pro 部署成一个链接，HR 不需要装 Python / Node，手机或电脑打开就能拖字段出图。

推荐顺序：

1. **0 元：本机 + Cloudflare 快速隧道**（第零节）：不注册、不绑卡，`trycloudflare.com` 国内实测可达。代价是电脑必须开机。
2. **0 元：免费 VPS + cloudflared**（第二节，**推荐**）：Oracle Cloud Always Free 的常开 VM 跑 Docker，`cloudflared` 快速隧道对外。**电脑关机、休眠、断网都不影响**，真 24/7、不休眠、国内可达。代价是要注册账号并做一次服务器初始化。
3. **0 元：Render 免费容器**（第三节，备选）：连 GitHub 自动构建、不用绑卡，但 15 分钟无访问会休眠、首次打开等 30–60 秒，且 ⚠️ `onrender.com` 国内实测无法访问。
4. **Railway**：同样识别 `Dockerfile`，但免费额度只有 30 天 $5，之后 $1/月跑不住常驻服务。
5. **阿里云 / 腾讯云**：国内最稳、可长期放简历；8000 端口直连不需要备案。

> 硬性要求是「本机可以关机」→ 直接看**第二节**；硬性要求是「0 元 + 真 24/7 + 国内可达」→ 也只有第二节能满足，免费 PaaS 做不到。

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
| 电脑关机 / 休眠 / 断网 → 链接立刻失效 | HR 打开会看到错误页 | 电源计划里把“睡眠”设为“从不”；要彻底解决就改用第二节的免费 VPS，本机开关机都不影响 |
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
| GitHub 仓库 | VPS 从仓库 clone，Render / Railway 从仓库构建（本仓库公开，可匿名 clone） |

生成一个访问码（任选）：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(9))"
```

---

## 二、免费 VPS + cloudflared（推荐：电脑关机也能访问）

这是目前**唯一同时满足「完全免费 + 真 24/7 + 国内可达」**的组合：服务跑在一台永久免费的云服务器上，你的电脑只用来改代码，关机、休眠、断网都不影响链接。

```
访客 → trycloudflare.com → cloudflared 出站长连接 → VPS 127.0.0.1:8014 → Docker 容器
```

VPS 只放行 SSH（22），**8014 只绑在 127.0.0.1**，不对外暴露端口；隧道是出站连接，Oracle 安全列表默认允许出站即可。

### 2026 年 Oracle Always Free 的真实规格

| 项目 | 官方现行值 |
| --- | --- |
| ARM VM.Standard.A1.Flex | 1,500 OCPU·时 + 9,000 GB·时/月 ≈ **2 OCPU / 12 GB 常开**（不是老教程里的 4/24） |
| 块存储 / 出站流量 | 200 GB / 10 TB 每月 |
| 费用 | 永久免费；注册时信用卡仅做身份验证，$1 预授权会退回 |
| 主区域 | 注册时选定后**永久锁定**，Always Free 资源只能建在主区域 |
| 闲置回收 | 连续 7 天内 CPU（95 分位）、网络、内存**三者都**低于 20% 会被判闲置并可能回收 |

注册与开实例时注意：姓名 / 账单地址要和信用卡一致、不要挂 VPN、别用隐私邮箱，否则容易被风控拒绝；热门区域常报 `out of host capacity`，换可用域（AD）或稍后重试，**不要为了抢容量去升级付费**。

### 部署步骤

1. 注册 <https://www.oracle.com/cloud/free/>，选好主区域（如东京 / 新加坡）。
2. 控制台 → 计算 → 实例 → 创建实例：
   - 镜像：**Ubuntu 22.04 / 24.04（ARM64 / aarch64）**
   - 形状：`VM.Standard.A1.Flex`，**2 OCPU / 12 GB**（不要超过，超出会计费）
   - 引导卷 50 GB（免费额度内）、分配公网 IPv4、粘贴你的 SSH 公钥
   - VCN 安全列表 / NSG **只放行 TCP 22**
   - 创建时下载并保存私钥，丢了就连不上
3. SSH 登录（`ssh -i <私钥> ubuntu@<公网IP>`），然后执行：
   ```bash
   curl -fsSL -o /tmp/da-setup.sh https://raw.githubusercontent.com/0222zt/dataagent-pro/main/deploy/vps/bootstrap.sh
   sudo bash /tmp/da-setup.sh
   ```
   按提示输入 SiliconFlow API Key 与 8 位以上访问码。脚本会自动安装 Docker 与 `cloudflared`（自动识别 ARM64/x86_64）、clone 本仓库、生成 `/opt/dataagent/.env`（chmod 600）、构建镜像、启动容器、注册 4 个 systemd 服务，最后打印公网链接和访问码。首次构建约 3–8 分钟。
4. 把链接发给 HR。当前链接始终写在 `/opt/dataagent/public_url.txt`。

脚本都在 `deploy/vps/`：`bootstrap.sh`（幂等安装，重跑即更新）、`start-container.sh`（构建 + 起容器 + 健康检查）、`update.sh`（拉 main 新版并重启）、`publish-url.sh`（抓隧道域名 + 可选 webhook 通知）、`keepalive.py`（轻度保活）、`env.template`（变量参考，不含密钥）。

### 部署后自检

| 检查 | 期望 |
| --- | --- |
| VPS 上 `curl http://127.0.0.1:8014/api/health` | 200，不需要访问码 |
| 打开 `public_url.txt` 里的链接 | 先出现访问码页 |
| 输入访问码 | 三栏工作台 + 示例数据自动出图 |
| 上传 CSV、拖字段 | 确定性出图 |
| 本机保持关机，用手机打开链接 | 依然可用 |
| `systemctl status dataagent-tunnel dataagent-keepalive` | 两者都是 active |

### 必须知道的限制

| 限制 | 影响 | 对策 |
| --- | --- | --- |
| Quick Tunnel 每次重建都会换域名 | 简历里写死的链接会过期 | `dataagent-publish.timer` 每 30 秒抓一次新链接写入 `public_url.txt`；可选在 `/opt/dataagent/notify.env` 写 `NOTIFY_WEBHOOK=https://...`，域名变化时自动推送。**要永久固定域名只能用 Named Tunnel + 自有域名（域名要钱）** |
| Quick Tunnel 无 SLA、最多 200 并发请求、**不转发 SSE** | AI 问答没有流式进度条 | 前端已加 15 秒首包超时降级：自动改用 `/api/chat` 普通接口，结果照常返回；确定性出图、上传、推荐图表完全不受影响 |
| Oracle 7 天闲置回收 | 长期无人访问的 VM 可能被回收 | 默认装了 `dataagent-keepalive.service`（每 10 分钟一轮：外部 HTTPS 请求 + 90 秒轻 CPU）。内存项若也要保，在 `/opt/dataagent/keepalive.env` 写 `KEEPALIVE_MEM_MB=2800` 后 `systemctl restart dataagent-keepalive`。**保活只能降低风险、不能保证**；最稳妥是让 VM 跑真实负载，或升级 PAYG（Always Free 资源仍 0 元，但账号变成可计费状态，务必设预算告警）。留意 Oracle 的回收预警邮件 |
| A1 容量紧张 | 创建实例报 out of capacity | 换可用域 / 换主区域 / 隔几小时重试 |
| 容器数据 | 上传数据在 Docker volume 里，1 小时 TTL 后清理 | 比 Render 好（VM 有持久盘），但仍不是正式存储 |

### 常用运维

| 操作 | 命令（VPS 上，需要 sudo） |
| --- | --- |
| 看当前链接 | `cat /opt/dataagent/public_url.txt` |
| 看隧道日志 | `journalctl -u dataagent-tunnel -n 60 --no-pager` |
| 看应用日志 | `docker logs -f dataagent-pro` |
| 更新到 GitHub 最新版 | `sudo bash /opt/dataagent/repo/deploy/vps/update.sh` |
| 改 API Key / 访问码 | 编辑 `/opt/dataagent/.env` → `sudo bash /opt/dataagent/repo/deploy/vps/start-container.sh` |
| 重启隧道（会换域名） | `sudo systemctl restart dataagent-tunnel` |
| 关掉保活（不推荐） | `sudo systemctl disable --now dataagent-keepalive` |

> 安全：`.env` 权限 600 且不进仓库；8014 只监听 127.0.0.1，Oracle 安全列表与 Ubuntu iptables 都不要放行 8014 入站；访问码 + 限流（`DATAAGENT_RATE_LIMIT=30`、AI 每 IP 每分钟 3 次 / 每天 50 次）保持开启。

---

## 三、Render 免费部署（备选）

这条是**备选**：连 GitHub 自动构建最省事，但会休眠、且 `onrender.com` 国内打不开。要「真 24/7 + 国内可达」请用第二节的免费 VPS。服务跑在 Render 容器里，你的电脑只用来改代码和 `git push`。

**前提**：一个 GitHub 账号（仓库已就绪）和一个 Render 账号（可用 GitHub 登录，免费档不需要信用卡）。API Key 与访问码只填在 Render 控制台，不进仓库。

### 部署步骤

1. 确认代码已推到 GitHub 的 `main`，`Dockerfile` 与 `render.yaml` 都在仓库根目录。
2. 打开 <https://dashboard.render.com/blueprints> → **New Blueprint Instance** → 用 GitHub 登录并授权 → 选择 `0222zt/dataagent-pro`、分支 `main`。
3. Render 读取 `render.yaml`：服务名 `dataagent-pro`、免费档、新加坡区、Docker 运行时、健康检查 `/api/health`（该路径已免访问码）。
4. 在 **Environment Variables** 里填两个 `sync: false` 的密钥：
   - `DATAAGENT_API_KEY` = SiliconFlow 的 Key（建议单独申请低额度 Key，不要用主 Key）
   - `DATAAGENT_ACCESS_CODE` = 访问码，8 位以上随机串（生成命令见第一节）
5. 点 **Apply**，首次 Docker 构建约 3–6 分钟。健康检查变绿即成功。
6. 打开控制台给出的 `https://<服务名>.onrender.com`，输入访问码；`DATAAGENT_DEMO=1` 会自动载入示例数据。

`render.yaml` 已内置演示限额：5 MB / 2 万行 / 5 数据集 / 1 小时过期 / 每 IP 每分钟 30 次 / AI 每 IP 每分钟 3 次、每天 50 次。

### 部署后自检

| 检查 | 期望 |
| --- | --- |
| `https://<服务名>.onrender.com/api/health` | 直接返回 200，不需要访问码 |
| 首页 | 先出现访问码页 |
| 输入访问码后 | 三栏工作台 + 示例数据自动出图 |
| 上传 CSV 并拖字段 | 确定性出图 |
| 本机 `git push` 后 | `autoDeploy: true` 自动重新构建部署 |

### 免费档的真实限制（2026 年）

| 限制 | 影响 | 建议 |
| --- | --- | --- |
| 15 分钟无入站流量即缩容 | 首次打开要等 30–60 秒 | 投递 / 演示前自己点一次预热；**不要**配 24/7 定时 ping，会吃光 750 小时额度且可能违反平台可接受使用政策 |
| 750 实例小时/月、5 GB 出站/月 | 一个常驻服务勉强够，超出暂停到次月 | 只在演示窗口期开放，不要挂公开爬虫 |
| 512 MB / 0.1 CPU | 大表会慢 | 已在 `render.yaml` 限到 5 MB / 2 万行 / 5 数据集 |
| 文件系统不持久、实例随时重启 | 上传数据与对话历史会丢 | 演示数据本来就是 1 小时 TTL，且启动时自动重建，**符合演示定位**；不要当正式存储 |
| `onrender.com` 国内实测不可达 | 国内 HR 打不开 | 已有自己的域名时在 Render 加 Custom Domain（免费档支持 + 免费 TLS）；否则改用第二节的免费 VPS（同样 0 元且国内可达）或腾讯云轻量（约 5.7 元/月） |
| 免费 Postgres 30 天过期 | 无影响 | 本项目用本地 SQLite + 临时目录，不依赖托管数据库 |

> 想要「真 24/7 不休眠 + 国内可达 + 仍然免费」，免费 PaaS 做不到，请回到**第二节**：Oracle Cloud Always Free 的常开 VPS + `cloudflared` 快速隧道。

---

## 四、Railway 部署

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

## 五、阿里云 / 腾讯云（国内最稳）

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

## 六、本地验证（部署前）

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

## 七、安全说明

- **Key 只在服务端**：前端拿不到 `DATAAGENT_API_KEY`；`/api/health` 只返回是否已配置。
- **访问码不是强认证**：它只防止链接被随意转发。Cookie 是 `HttpOnly` + `SameSite=Lax`，有效期 7 天；换访问码后旧 Cookie 立即失效。
- **代码沙箱**：模型生成的代码在受限命名空间里执行，禁用了文件、网络、模块导入等敏感操作。
- **建议同时开启限流**（默认已开）；公网不要设 `DATAAGENT_RATE_LIMIT=0`。
- **AI 额度独立计数**：`DATAAGENT_LLM_RATE_LIMIT` / `DATAAGENT_LLM_DAILY_LIMIT` 只作用于 `/api/chat`，超限返回 `LLM_QUOTA_EXCEEDED`，拖字段出图仍然可用。
- **用低额度 Key 部署**：即使访问码被转发，损失也封顶。不要用主账号 Key。
- **不要提交 `.env`**：`.gitignore` 和 `.dockerignore` 都已排除。

---

## 八、简历怎么写（诚实版）

> **DataAgent Pro**（个人项目）
> - 基于 FastAPI + Plotly 实现三栏式图表工作台：拖拽字段 → 确定性编译出图，复杂需求转自然语言 → 免费模型链生成代码 → 沙箱执行 → 失败自愈。
> - 设计模型降级链、结果缓存、多轮会话与历史记录；用受限执行环境隔离生成代码。
> - 提供在线演示：内置示例数据、访问码门禁、Docker 一键部署与公网资源限额。
>
> 参考并二次开发了 Microsoft Data Formulator（MIT）的交互思路；核心后端、沙箱、自愈与部署由本人实现。

不要写“从零开发 Data Formulator”或把微软的 prompt / agent 设计算成自己的成果。

---

## 九、常见问题

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
