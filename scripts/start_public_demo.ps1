# 0 元公网演示：本机跑服务 + Cloudflare 免费隧道
# 用法：右键“使用 PowerShell 运行”，或双击根目录的 启动公网演示.bat
# 停止：在这个窗口按 Ctrl+C
# 指定端口：powershell -ExecutionPolicy Bypass -File scripts\start_public_demo.ps1 -Port 8016

param(
    [int]$Port = 8014
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 某些终端会同时注入 http_proxy / HTTP_PROXY 这类仅大小写不同、值相同的变量。
# PowerShell 5.1 的 Start-Process 在带输出重定向时会因此抛 ArgumentException，先去重。
$allVars = [System.Environment]::GetEnvironmentVariables()
$seenVars = @{}
foreach ($key in @($allVars.Keys)) {
    $name = [string]$key
    $lower = $name.ToLowerInvariant()
    if ($seenVars.ContainsKey($lower)) {
        if ($allVars[$key] -eq $allVars[$seenVars[$lower]]) {
            [System.Environment]::SetEnvironmentVariable($name, $null, [System.EnvironmentVariableTarget]::Process)
        }
    } else {
        $seenVars[$lower] = $name
    }
}

$gate = "hrdemo2026"

# 每次运行用独立日志：多个演示窗口 / 不同项目文件夹各写各的，不再抢同一个文件句柄
$folderTag = (Split-Path $root -Leaf) -replace '[^A-Za-z0-9_-]', '_'
$runTag    = '{0}_{1}_{2}' -f $folderTag, (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
$cfLog     = Join-Path $env:TEMP "dataagent_cf_tunnel_$runTag.log"
$cfErr     = "$cfLog.err"
$appLog    = Join-Path $env:TEMP "dataagent_app_$runTag.log"
$appErr    = "$appLog.err"
$urlFile   = Join-Path $root "public_url.txt"

# 先清空，免得上一次（或另一个文件夹）留下的链接被当成这次的结果
Set-Content -Path $urlFile -Value '' -Encoding UTF8

# 真正试绑一次端口：只看进程表会漏掉「进程已退出、socket 被子进程继承」的残留监听
function Test-PortBusy([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($conn) { return $true }
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener -ArgumentList ([System.Net.IPAddress]::Loopback), $p
        $listener.ExclusiveAddressUse = $true
        $listener.Start()
        return $false
    } catch {
        return $true
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Get-PortOwner([int]$p) {
    $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if (-not $conn) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
}

# 判断占用端口的是不是本文件夹自己启动的服务（往上找启动它的 PowerShell）
function Test-SameFolderServer($proc) {
    if (-not $proc -or $proc.Name -notmatch 'python') { return $false }
    $cursor = $proc
    for ($i = 0; $i -lt 3 -and $cursor; $i++) {
        if ($cursor.CommandLine -and $cursor.CommandLine -like "*$PSScriptRoot*") { return $true }
        $cursor = Get-CimInstance Win32_Process -Filter "ProcessId=$($cursor.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    return $false
}

function Find-FreePort([int]$start) {
    for ($p = $start; $p -le ($start + 30); $p++) {
        if (-not (Test-PortBusy $p)) { return $p }
    }
    throw "$start ~ $($start + 30) 之间找不到空闲端口。"
}

# reload 模式会派生子进程，停服务时连子进程一起停
function Stop-AppTree($procId) {
    if (-not $procId) { return }
    $kids = Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue
    foreach ($kid in $kids) { Stop-Process -Id $kid.ProcessId -Force -ErrorAction SilentlyContinue }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

$appProc = $null
$busy    = Test-PortBusy $Port
$owner   = Get-PortOwner $Port

if ($busy -and $owner -and (Test-SameFolderServer $owner)) {
    $port = $Port
    Write-Host "[1/2] 本机 $port 端口已有本文件夹的服务，直接复用" -ForegroundColor DarkGray
} else {
    if ($busy) {
        $who = if ($owner) { "PID $($owner.ProcessId) $($owner.Name)" } else { "残留监听（进程已退出）" }
        Write-Host "[1/2] 端口 $Port 已被占用（$who），可能是另一个演示窗口。" -ForegroundColor Yellow
        Write-Host "      为保证公网链接指向本文件夹的界面，本次另起一个服务。" -ForegroundColor Yellow
    }
    $port = if ($busy) { Find-FreePort ($Port + 1) } else { $Port }
    Write-Host "[1/2] 启动本地服务（$port）…" -ForegroundColor Cyan

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

    $appProc = Start-Process -FilePath "python" `
        -ArgumentList "-X", "utf8", "run.py" `
        -WorkingDirectory $root -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $appLog -RedirectStandardError $appErr
    $ok = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-PortBusy $port) -and (Get-Process -Id $appProc.Id -ErrorAction SilentlyContinue)) {
            $ok = $true; break
        }
    }
    if (-not $ok) {
        Stop-AppTree $appProc.Id
        Write-Host "本地服务日志：$appErr" -ForegroundColor DarkGray
        throw "本地服务没能在 20 秒内起来：检查 .env 里的 API Key、$port 端口占用，或看上面的日志。"
    }
}

Write-Host "[2/2] 建立 Cloudflare 隧道…" -ForegroundColor Cyan
$cfProc = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel", "--url", "http://127.0.0.1:$port", "--no-autoupdate" `
    -RedirectStandardOutput $cfLog -RedirectStandardError $cfErr `
    -PassThru -WindowStyle Hidden

$publicUrl = $null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    foreach ($f in @($cfLog, $cfErr)) {
        if (Test-Path $f) {
            $hit = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
                   Select-Object -First 1
            if ($hit) { $publicUrl = $hit.Matches[0].Value; break }
        }
    }
    if ($publicUrl) { break }
}

if (-not $publicUrl) {
    Write-Host "隧道建立失败。日志：$cfErr" -ForegroundColor Red
    if ($cfProc) { Stop-Process -Id $cfProc.Id -Force -ErrorAction SilentlyContinue }
    Stop-AppTree $appProc.Id
    exit 1
}

$publicUrl | Set-Content -Path $urlFile -Encoding UTF8

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
    Stop-AppTree $appProc.Id
    Write-Host "已停止隧道与本地服务。" -ForegroundColor DarkGray
}
