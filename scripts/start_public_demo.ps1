# 0 元公网演示：本机跑服务 + Cloudflare 免费隧道
# 用法：右键“使用 PowerShell 运行”，或双击根目录的 启动公网演示.bat
# 停止：在这个窗口按 Ctrl+C

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$port     = 8014
$gate     = "hrdemo2026"
$cfLog    = Join-Path $env:TEMP "dataagent_cf_tunnel.log"

# 公网演示参数：5 MB / 2 万行 / 5 数据集 / 1 小时 / 30 次每分 / AI 3 次每分 + 50 次每天
$env:DATAAGENT_PORT             = "$port"
$env:DATAAGENT_ACCESS_CODE      = $gate
$env:DATAAGENT_MAX_FILE_SIZE    = "5242880"
$env:DATAAGENT_MAX_ROWS         = "20000"
$env:DATAAGENT_MAX_DATASETS     = "5"
$env:DATAAGENT_DATASET_TTL      = "3600"
$env:DATAAGENT_RATE_LIMIT       = "30"
$env:DATAAGENT_LLM_RATE_LIMIT   = "3"
$env:DATAAGENT_LLM_DAILY_LIMIT  = "50"

function Test-Port([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

$appProc = $null
if (Test-Port $port) {
    Write-Host "[1/2] 本机 $port 端口已有服务，直接复用" -ForegroundColor DarkGray
} else {
    Write-Host "[1/2] 启动本地服务（$port）…" -ForegroundColor Cyan
    $appProc = Start-Process -FilePath "python" `
        -ArgumentList "-X", "utf8", "run.py" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Port $port) { $ok = $true; break }
    }
    if (-not $ok) {
        if ($appProc) { Stop-Process -Id $appProc.Id -Force -ErrorAction SilentlyContinue }
        throw "本地服务没能在 20 秒内起来，检查 .env 里的 API Key 与 8014 端口占用。"
    }
}

Write-Host "[2/2] 建立 Cloudflare 隧道…" -ForegroundColor Cyan
if (Test-Path $cfLog) { Remove-Item $cfLog -Force }
$cfProc = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$port", "--no-autoupdate" `
    -RedirectStandardOutput $cfLog -RedirectStandardError "$cfLog.err" `
    -PassThru -WindowStyle Hidden

$publicUrl = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    foreach ($f in @($cfLog, "$cfLog.err")) {
        if (Test-Path $f) {
            $hit = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
                   Select-Object -First 1
            if ($hit) { $publicUrl = ($hit.Matches[0].Value); break }
        }
    }
    if ($publicUrl) { break }
}

if (-not $publicUrl) {
    Write-Host "隧道建立失败。日志：$cfLog.err" -ForegroundColor Red
    if ($cfProc) { Stop-Process -Id $cfProc.Id -Force -ErrorAction SilentlyContinue }
    if ($appProc) { Stop-Process -Id $appProc.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

$publicUrl | Set-Content -Path (Join-Path $root "public_url.txt") -Encoding UTF8

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "   公网链接： $publicUrl" -ForegroundColor Green
Write-Host "   访问码：   $gate" -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  已写入 public_url.txt。链接在本次运行期间有效。" -ForegroundColor DarkGray
Write-Host "  关闭本窗口或按 Ctrl+C 即断开。电脑休眠 / 关机后链接失效。" -ForegroundColor DarkGray
Write-Host ""

try {
    Wait-Process -Id $cfProc.Id
} finally {
    if ($cfProc) { Stop-Process -Id $cfProc.Id -Force -ErrorAction SilentlyContinue }
    if ($appProc) { Stop-Process -Id $appProc.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "已停止隧道与本地服务。" -ForegroundColor DarkGray
}
