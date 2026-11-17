# -*- coding: utf-8 -*-
<#
  影之幻境助手 · 运行环境自动检查 / 安装

  做什么
  ------
  1. 找 Python（3.11+）：python-path.txt 指定的 → py 启动器 → PATH → 常见安装目录 → 随包 python\
  2. 找 Node.js（18+）：随包 node\ → node-path.txt → PATH → 常见安装目录
  3. 找依赖库：Pillow / NumPy / Frida / pygame / PyOpenGL / live2d-py /
     requests / edge-tts / keyboard（缺了就 pip install，官方源失败自动换清华镜像）
  4. 缺什么装什么：Python 用 winget 或官方安装包；Node 用官方 zip（免管理员）
  5. 全部就绪 → 写 .env-ready.json 记录（只做记录，不靠它跳过检查）

  用法
  ----
    powershell -NoProfile -ExecutionPolicy Bypass -File setup_env.ps1
        检查 + 缺什么装什么（启动.bat 在 -Quick 探测到缺东西后调这个）
    ... -Quick
        只做**最快探测**：齐全就静默 exit 0（约 0.5~1 秒），缺任何一样 exit 1，不打印、不安装。
        启动.bat 每次启动都先跑它 —— 环境齐全直接跳过，缺了才走安装流程。
    ... -Force
        强制重新检查一遍（修复环境.bat 用），同样"缺了才装"
    ... -Check
        只检查、不安装（排查用，退出码 0=就绪 / 1=缺东西）

  退出码：0 就绪 / 1 一般错误 / 2 缺 Python / 3 缺 Node.js / 4 缺依赖库
#>
param(
    [switch]$Check,
    [switch]$Force,
    [switch]$Quiet,
    [switch]$Quick,
    [switch]$RepairFrida
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # 下载进度条会拖慢 Invoke-WebRequest

$Probe    = $PSScriptRoot                          # …\svwb-probe
$Root     = Split-Path -Parent $Probe              # 包根
$Marker   = Join-Path $Probe '.env-ready.json'
$PyPath   = Join-Path $Probe 'python-path.txt'
$NodePath = Join-Path $Probe 'node-path.txt'
$LogDir   = Join-Path $Probe 'logs'
$LogFile  = Join-Path $LogDir '环境检查.log'
$NodeDir  = Join-Path $Probe 'node'                # 随包 node（zip 解压到这里）

$NodeZipVersion = 'v20.18.0'
$PyInstallerUrl = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
# 国内镜像（实测：本机 nodejs.org 直接超时拿不到一个字节，npmmirror 秒开）。
# 顺序：官网 → 镜像；每个都设了超时，卡住不会无限等。
$PyMirrorUrl = 'https://mirrors.huaweicloud.com/python/3.12.7/python-3.12.7-amd64.exe'
$NodeZipMirror = 'https://npmmirror.com/mirrors/node'
$DepsCheck = Join-Path $Probe 'deps_check.py'
# Explicit repair invalidates the fast-launch cache before installing dependencies.
if ($Force -or $RepairFrida) {
    Remove-Item -LiteralPath (Join-Path $Probe '.startup-ready.json') -Force -ErrorAction SilentlyContinue
}

# 运行依赖：mod = Python 导入名，pkg = pip 包名，core = 缺了主程序不能跑。
# 注意：frida 是记牌器实时读取的核心，pygame/OpenGL/live2d 是 AI 陪玩的核心；
# 但 pip 偶尔会因为网络/杀毒软件失败，所以只有 Pillow/NumPy 硬性阻止启动，
# 其余缺失会明确告警并写日志，下次启动/「修复环境.bat」会自动重试。
$Deps = @(
    @{ mod = 'PIL';      pkg = 'pillow';    name = 'Pillow';    core = $true  },
    @{ mod = 'numpy';    pkg = 'numpy';     name = 'NumPy';     core = $true  },
    @{ mod = 'frida';    pkg = 'frida==17.26.2'; name = 'Frida'; core = $true },
    @{ mod = 'requests'; pkg = 'requests';  name = 'requests';  core = $false },
    @{ mod = 'pygame';   pkg = 'pygame';    name = 'pygame';    core = $false },
    @{ mod = 'OpenGL';   pkg = 'PyOpenGL';  name = 'PyOpenGL';  core = $false },
    @{ mod = 'live2d';   pkg = 'live2d-py>=1.0.0'; name = 'live2d-py'; core = $false },
    @{ mod = 'edge_tts'; pkg = 'edge-tts';  name = 'edge-tts';  core = $false },
    @{ mod = 'keyboard'; pkg = 'keyboard';  name = 'keyboard';  core = $false }
)

function Say($msg, $color) {
    if (-not $Quiet) {
        if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
    }
}
function Log($msg) {
    try {
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
        $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}
function Fatal($code, $msg) {
    Say '' $null
    Say $msg 'Red'
    Log ("失败：{0}" -f $msg)
    exit $code
}

# ── 网络：强制 TLS 1.2（Python/Node 官网都不接受 TLS 1.0）────────────────
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function URLOrder($primary, $mirror) {
    # 中文系统先走国内镜像：官网在国内容易"连得上但极慢"
    # （实测 nodejs.org 90 秒只下了 1.2MB，npmmirror 一分钟就拿完）。
    try {
        if ((Get-Culture).Name -like 'zh-*') { return @($mirror, $primary) }
    } catch { }
    return @($primary, $mirror)
}

function Download($urls, $out) {
    # $urls：按顺序尝试（中文系统：镜像 → 官网；其它：官网 → 镜像）。
    # ⚠️ 优先用 **curl.exe**（Win10 1803+ 自带）：
    #    Invoke-WebRequest 的 -TimeoutSec 在"连上了但一个字节都收不到"时不生效，
    #    实测卡了 15 分钟还没返回；curl 的 --connect-timeout 能立刻失败切镜像。
    $curl = $null
    $cc = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($cc) { $curl = $cc.Source }
    if (-not $curl) {
        $cc = Get-Command curl -ErrorAction SilentlyContinue
        if ($cc -and $cc.CommandType -eq 'Application') { $curl = $cc.Source }
    }
    foreach ($u in $urls) {
        $maxTime = 600
        if ($u -like 'https://nodejs.org/*' -or $u -like 'https://www.python.org/*') { $maxTime = 60 }
        Say ("    下载（最多 {0}s）：{1}" -f $maxTime, $u) 'DarkGray'
        Say '      （约 25~30MB，网慢的话要 1~2 分钟，请等它跑完）' 'DarkGray'
        Log ("下载 {0} -> {1}（最多 {2}s）" -f $u, $out, $maxTime)
        if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
        $okUrl = $false
        if ($curl) {
            # ⚠️ 脚本顶部是 $ErrorActionPreference='Stop'，而原生程序写 stderr 在 PS 5.1 里
            #    会被当成 ErrorRecord -> 直接终止脚本（实测：curl 超时写了 stderr，
            #    整个安装器立刻退出，镜像都没来得及试）。所以这里临时放宽。
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'SilentlyContinue'
            try {
                & $curl -L --silent --show-error --fail --connect-timeout 15 `
                    --max-time $maxTime -o $out $u 2>$null | Out-Null
                $rc = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $prevEAP
            }
            if ($rc -eq 0) {
                $okUrl = $true
            } else {
                Say ("    这个地址不行（curl 退出码 {0}）" -f $rc) 'Yellow'
                Log ("下载失败：curl 退出码 {0}，地址 {1}" -f $rc, $u)
            }
        } else {
            try {
                Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing -TimeoutSec $maxTime
                $okUrl = $true
            } catch {
                Say ("    这个地址不行：{0}" -f $_.Exception.Message) 'Yellow'
                Log ("下载失败：{0}" -f $_.Exception.Message)
            }
        }
        if ($okUrl -and (Test-Path -LiteralPath $out) -and (Get-Item -LiteralPath $out).Length -gt 0) {
            Say '    下载完成' 'Green'
            return $true
        }
    }
    return $false
}

# ── Python ──────────────────────────────────────────────────────────────
function Read-RuntimePath($file) {
    # Accept both historical ANSI files and user-supplied UTF-8 paths.
    foreach ($encoding in @('UTF8', 'Default')) {
        try {
            $candidate = (Get-Content -LiteralPath $file -TotalCount 1 -Encoding $encoding).Trim().Trim('"')
            if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
        } catch { }
    }
    return $null
}

function Get-PyVersion($exe, $pre) {
    # ⚠️ 不用 `-c "import sys;print('%d.%d.%d'%...)"`：
    #    PowerShell 5.1 往原生 exe 传参时会把 -c 里的**双引号吃掉**，
    #    Python 直接 SyntaxError 退出 1 → 被误判成"没有 Python"。
    #    改成 `-V`（输出 "Python 3.12.14"），不涉及任何引号。
    if (-not $exe) { return $null }
    # Do not install application packages into another tool's private runtime.
    if ($exe -match '[\\/]\.dsh[\\/]dsh-runtimes[\\/]') { return $null }
    if (-not (Test-Path -LiteralPath $exe)) { return $null }
    $ver = $null
    try {
        $args2 = @()
        if ($pre) { $args2 += $pre }
        $args2 += @('-V')
        $out = & $exe @args2 2>&1
        if ($LASTEXITCODE -eq 0 -and $out) {
            $txt = ($out | Select-Object -Last 1).ToString().Trim()
            if ($txt -match 'Python\s+(\d+)\.(\d+)\.(\d+)') {
                $ver = '{0}.{1}.{2}' -f $Matches[1], $Matches[2], $Matches[3]
            }
        }
    } catch { $ver = $null }
    if (-not $ver) { return $null }
    $parts = $ver.Split('.')
    if ($parts.Length -lt 2) { return $null }
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) { return $null }
    return $ver
}

function Find-Python {
    $bundled = Join-Path $Probe 'python\python.exe'
    if (Test-Path -LiteralPath $bundled) {
        $version = Get-PyVersion $bundled $null
        if ($version) { return [pscustomobject]@{ exe = $bundled; pre = @(); version = $version; from = '随包 Python' } }
    }
    # ① python-path.txt（用户手动指定过，或上次自动装好后写进去的）
    if (Test-Path -LiteralPath $PyPath) {
        try {
            $c = (Read-RuntimePath $PyPath)
            if ($c) {
                $v = Get-PyVersion $c $null
                if ($v) { return [pscustomobject]@{ exe = $c; pre = @(); version = $v; from = 'python-path.txt' } }
            }
        } catch { }
    }
    # ② py 启动器（Windows 官方安装器带的，能绕开商店占位程序）
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $v = Get-PyVersion $py.Source '-3'
        if ($v) { return [pscustomobject]@{ exe = $py.Source; pre = @('-3'); version = $v; from = 'py -3' } }
    }
    # ③ PATH 里的 python（商店占位程序会失败，Get-PyVersion 自己会剔掉）
    foreach ($c in @('python', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) {
            $v = Get-PyVersion $cmd.Source $null
            if ($v) { return [pscustomobject]@{ exe = $cmd.Source; pre = @(); version = $v; from = 'PATH' } }
        }
    }
    # ④ 随包 python\（如果有便携版）
    $localPy = Join-Path $Probe 'python\python.exe'
    if (Test-Path -LiteralPath $localPy) {
        $v = Get-PyVersion $localPy $null
        if ($v) { return [pscustomobject]@{ exe = $localPy; pre = @(); version = $v; from = '随包 python\' } }
    }
    # ⑤ 常见安装位置
    $known = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        'C:\Python313\python.exe', 'C:\Python312\python.exe',
        'C:\Python311\python.exe'
    )
    foreach ($c in $known) {
        $v = Get-PyVersion $c $null
        if ($v) { return [pscustomobject]@{ exe = $c; pre = @(); version = $v; from = '常见目录' } }
    }
    return $null
}

function Install-Python {
    Say '  没有找到可用的 Python，尝试自动安装……' 'Yellow'
    Log 'Python 缺失，开始自动安装'
    # ① 先试 winget（有就用，装的是官方包）
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say '  ① 用 winget 安装 Python 3.12（可能会弹一次权限确认）……' 'Cyan'
        foreach ($id in @('Python.Python.3.12', 'Python.Python.3.11')) {
            try {
                & winget install --id $id -e --source winget --silent `
                    --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
            } catch { }
            $p = Find-Python
            if ($p) { return $p }
        }
    }
    # ② 官方安装包（按用户安装，不需要管理员）
    Say '  ② 用 Python 官网安装包（按当前用户安装，不需要管理员）……' 'Cyan'
    $out = Join-Path $env:TEMP 'python-setup.exe'
    if (Download (URLOrder $PyInstallerUrl $PyMirrorUrl) $out) {
        try {
            $p = Start-Process -FilePath $out -Wait -PassThru -ArgumentList @(
                '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
                'Include_pip=1', 'Include_test=0', 'Include_launcher=1')
            Log ("Python 安装器退出码 {0}" -f $p.ExitCode)
        } catch {
            Log ("Python 安装失败：{0}" -f $_.Exception.Message)
        }
    }
    return (Find-Python)
}

# ── Node.js ─────────────────────────────────────────────────────────────
function Get-NodeVersion($exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    try {
        $out = & $exe -v 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $v = $out.ToString().Trim()          # 形如 v20.18.0
        if ($v -notmatch '^v(\d+)\.') { return $null }
        if ([int]$Matches[1] -lt 18) { return $null }
        return $v
    } catch { return $null }
}

function Find-Node {
    # ① 随包 node\node.exe（安装器解压到这）
    foreach ($localNode in @((Join-Path $NodeDir 'node.exe'), (Join-Path $Root 'node\node.exe'))) {
        $v = Get-NodeVersion $localNode
        if ($v) { return [pscustomobject]@{ exe = $localNode; version = $v; from = '随包 node\' } }
    }
    # ② node-path.txt
    if (Test-Path -LiteralPath $NodePath) {
        try {
            $c = (Read-RuntimePath $NodePath)
            $v = Get-NodeVersion $c
            if ($v) { return [pscustomobject]@{ exe = $c; version = $v; from = 'node-path.txt' } }
        } catch { }
    }
    # ③ PATH
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) {
        $v = Get-NodeVersion $cmd.Source
        if ($v) { return [pscustomobject]@{ exe = $cmd.Source; version = $v; from = 'PATH' } }
    }
    # ④ 常见安装位置
    $pf86 = ${env:ProgramFiles(x86)}
    $knownNode = @((Join-Path $env:ProgramFiles 'nodejs\node.exe'))
    if ($pf86) { $knownNode += (Join-Path $pf86 'nodejs\node.exe') }
    foreach ($c in $knownNode) {
        $v = Get-NodeVersion $c
        if ($v) { return [pscustomobject]@{ exe = $c; version = $v; from = '常见目录' } }
    }
    return $null
}

function Install-Node {
    Say '  没有找到 Node.js，尝试自动安装……' 'Yellow'
    Log 'Node.js 缺失，开始自动安装'
    # ① 官方 zip（免管理员、免改 PATH，解压到 svwb-probe\node\）
    #    官网在国内经常直接超时（实测 12 秒拿不到一个字节），所以带 npmmirror 镜像
    $zipUrl = "https://nodejs.org/dist/$NodeZipVersion/node-$NodeZipVersion-win-x64.zip"
    $zipMirror = "$NodeZipMirror/$NodeZipVersion/node-$NodeZipVersion-win-x64.zip"
    $zip = Join-Path $env:TEMP ("node-{0}-win-x64.zip" -f $NodeZipVersion)
    if (Download (URLOrder $zipUrl $zipMirror) $zip) {
        $tmp = Join-Path $env:TEMP ('node-unzip-' + [guid]::NewGuid().ToString('N'))
        try {
            Say '    解压……' 'DarkGray'
            Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
            $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
            if ($inner) {
                if (Test-Path -LiteralPath $NodeDir) { Remove-Item -LiteralPath $NodeDir -Recurse -Force }
                Move-Item -LiteralPath $inner.FullName -Destination $NodeDir -Force
                Log ("Node 已解压到 {0}" -f $NodeDir)
            }
        } catch {
            Log ("解压 Node 失败：{0}" -f $_.Exception.Message)
        } finally {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        }
        $n = Find-Node
        if ($n) { return $n }
    }
    # ② 退而求其次：winget
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say '  ② 用 winget 安装 Node.js LTS……' 'Cyan'
        try {
            & winget install --id OpenJS.NodeJS.LTS -e --source winget --silent `
                --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
        } catch { }
    }
    return (Find-Node)
}

# ── Python 依赖库 ───────────────────────────────────────────────────────
function Test-PyModule($py, $mod) {
    try {
        $a = @()
        if ($py.pre) { $a += $py.pre }
        $a += @('-c', "import $mod")
        & $py.exe @a 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-MissingDeps($py, [string[]]$extra) {
    # 用 deps_check.py 一次性检查（避免 PowerShell 5.1 传 -c 时吃掉引号）。
    # 老版本升级上来可能没有这个文件，直接退回逐模块 import，避免误判为"齐全"。
    if (-not (Test-Path -LiteralPath $DepsCheck)) {
        $names = @()
        foreach ($d in $Deps) {
            if (-not (Test-PyModule $py $d.mod)) { $names += $d.mod }
        }
        return $names
    }
    $a = @()
    if ($py.pre) { $a += $py.pre }
    $a += @('-X', 'utf8', $DepsCheck)
    if ($extra) { $a += $extra }
    foreach ($d in $Deps) { $a += $d.mod }
    $out = ''
    try {
        $out = (& $py.exe @a 2>$null) -join ' '
    } catch {
        $out = ''
    }
    if ($LASTEXITCODE -eq 0) { return @() }
    $out = $out -replace '^\s*MISSING:', ''
    $names = @($out -split '\s+' | Where-Object { $_ })
    if ($names.Count -eq 0) {
        if ($extra -contains "--spec") { return @("frida") }
        # 检查脚本自身失败（被杀毒拦截/编码问题）时，退回逐模块 import。
        $names = @()
        foreach ($d in $Deps) {
            if (-not (Test-PyModule $py $d.mod)) { $names += $d.mod }
        }
    }
    return $names
}

function Install-Pip($py, [string[]]$pkgs, [switch]$Mirror) {
    if (-not $pkgs -or $pkgs.Count -eq 0) { return }
    $a = @()
    if ($py.pre) { $a += $py.pre }
    # --no-cache-dir：pip 的 HTTP 缓存偶尔会留下半截包，表现为 install 卡住
    # 十分钟没有任何输出（实测 live2d-py）；每次重下更稳，也避免缓存目录权限问题。
    # --timeout/--retries：国内直连 PyPI 卡住时尽快失败，好切清华镜像。
    $a += @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
            '--no-cache-dir', '--prefer-binary', '--timeout', '30', '--retries', '1')
    if ($RepairFrida -and $pkgs -contains 'frida==17.26.2') { $a += '--force-reinstall' }
    if ($Mirror) {
        $a += @('--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple')
    } else {
        $a += @('--index-url', 'https://pypi.org/simple')
    }
    $a += $pkgs
    try { & $py.exe @a 2>$null | Out-Null } catch { }
}

function Test-FridaLocal($py) {
    $a = @($py.pre | Where-Object { $_ }) + @('-I', $DepsCheck, '--frida-test')
    & $py.exe @a | Out-Host
    return ($LASTEXITCODE -eq 0)
}

function Save-PythonPath($py) {
    $a = @($py.pre | Where-Object { $_ }) + @('-c', 'import sys;print(sys.executable)')
    $exePath = (& $py.exe @a | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exePath)) { Fatal 2 '无法确定已检查的 Python 路径。' }
    Set-Content -LiteralPath $PyPath -Value $exePath -Encoding Default
}

function Ensure-PyDeps($py) {
    $missing = @(Get-MissingDeps $py @())
    if ($missing.Count -eq 0) { return $true }

    $pkgs = @()
    foreach ($m in $missing) {
        $d = $Deps | Where-Object { $_.mod -eq $m } | Select-Object -First 1
        if ($d) { $pkgs += $d.pkg }
    }
    Say ("  缺依赖库：{0}，开始安装……" -f ($pkgs -join ' / ')) 'Yellow'
    Log ("缺依赖：{0}" -f ($missing -join ','))

    # 中文系统优先走清华镜像：frida 的 wheel 约 61MB，直连 PyPI 在国内容易
    # 只有几百 KB/s，看起来像卡住五六分钟；镜像通常几十秒。
    $mirrorFirst = $false
    try { $mirrorFirst = (Get-Culture).Name -like 'zh-*' } catch { }

    # 逐个安装：pip 一次性解析全部包时，在部分网络/缓存环境下会卡住
    # （实测 live2d-py 与其它包一起装时 10 分钟没有任何输出）。逐个装
    # 还能让某个包失败不拖累其它包。
    foreach ($pkg in $pkgs) {
        Say ("    安装 {0}……" -f $pkg) 'DarkGray'
        if ($pkg -like 'frida*') {
            Say '    frida 安装包约 61MB，首次安装通常 1~3 分钟，请不要关闭窗口……' 'Yellow'
        }
        if ($mirrorFirst) {
            Install-Pip $py @($pkg) -Mirror
        } else {
            Install-Pip $py @($pkg)
        }
    }
    $missing = @(Get-MissingDeps $py @())

    # 第二次：换另一个源逐个重试；某个包失败不会拖累其它包。
    if ($missing.Count -gt 0) {
        if ($mirrorFirst) {
            Say '    镜像源没有装全，换官方源逐个重试……' 'Cyan'
        } else {
            Say '    官方源没有装全，换清华镜像逐个重试……' 'Cyan'
        }
        Log ("第一轮后仍缺：{0}" -f ($missing -join ','))
        foreach ($m in $missing) {
            $d = $Deps | Where-Object { $_.mod -eq $m } | Select-Object -First 1
            if (-not $d) { continue }
            if ($mirrorFirst) {
                Install-Pip $py @($d.pkg)
            } else {
                Install-Pip $py @($d.pkg) -Mirror
            }
        }
        $missing = @(Get-MissingDeps $py @())
    }
    if ($missing.Count -eq 0) { return $true }

    $coreMissing = @()
    foreach ($m in $missing) {
        $d = $Deps | Where-Object { $_.mod -eq $m } | Select-Object -First 1
        if ($d -and $d.core) { $coreMissing += $d.pkg }
    }
    if ($coreMissing.Count -gt 0) {
        Log ("核心依赖安装失败：{0}" -f ($coreMissing -join ','))
        return $false
    }

    # 非核心依赖（frida/pygame/live2d/语音等）没装好时不阻止主程序；
    # 但一定要让用户看到，否则表现就是「记牌器不动 / AI 陪玩不显示」。
    Say ("  以下依赖没装好：{0}" -f ($missing -join ' / ')) 'Yellow'
    Say '  主程序仍可启动；记牌器的实时读取、AI 陪玩的模型/语音会受影响。' 'Yellow'
    Say '  请联网后双击「修复环境.bat」再试一次。' 'Yellow'
    Log ("非核心依赖未装好：{0}" -f ($missing -join ','))
    try {
        $missFile = Join-Path $LogDir '依赖缺失.txt'
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
        Set-Content -LiteralPath $missFile -Value ($missing -join "`r`n") -Encoding UTF8
    } catch { }
    return $true
}

function Get-PipVersion($py, $pkg) {
    # 用 `pip show` 取版本（同样避开 -c 里的引号问题）
    try {
        $a = @()
        if ($py.pre) { $a += $py.pre }
        $a += @('-m', 'pip', 'show', $pkg)
        $out = & $py.exe @a 2>$null
        foreach ($line in $out) {
            if ($line -match '^Version:\s*(.+)$') { return $Matches[1].Trim() }
        }
    } catch { }
    return '?'
}

# ── 主流程 ──────────────────────────────────────────────────────────────
if ($Quick) {
    # 启动.bat 每次启动都会先跑这个：**静默**、只探测、不安装。
    #   齐全 → exit 0（约 0.5~1 秒，用户感觉就是"秒开"）
    #   缺任何一样 → exit 1，启动.bat 再去跑完整的安装流程（那时才有输出）
    $qpy = Find-Python
    if (-not $qpy) { exit 1 }
    $qnode = Find-Node
    if (-not $qnode) { exit 1 }
    # 依赖库合并成一次检查（--spec 只查是否安装，不 import，约几十毫秒）。
    if (-not (Test-Path -LiteralPath $DepsCheck)) { exit 1 }
    $qmissing = @(Get-MissingDeps $qpy @('--spec', '--startup'))
    if ($qmissing.Count -gt 0) { exit 1 }
    Save-PythonPath $qpy
    [IO.File]::WriteAllText($NodePath, $qnode.exe, (New-Object Text.UTF8Encoding($false)))
    exit 0
}

Say '============================================================' 'DarkGray'
Say '  影之幻境助手 · 运行环境检查' 'Cyan'
Say '============================================================' 'DarkGray'
Log '==== 环境检查开始 ===='

$pairRequested = $Force -or $RepairFrida
$pairReport = Join-Path $Root 'Frida配对说明.txt'
if ($pairRequested) {
    $osArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    @("影之诗助手 v1.1.4 · Frida 配对说明", "Windows：$([Environment]::OSVersion.VersionString)；架构：$osArch",
      "尚未完成 Python 检查。若自动安装失败，请在支持 x64 的 Windows 上安装 CPython 3.12 (64-bit)。",
      "下载：https://www.python.org/downloads/windows/", "装好后再次双击「修复环境.bat」。") |
        Set-Content -LiteralPath $pairReport -Encoding UTF8
}
$py = Find-Python
if (-not $py) {
    if ($Check) { Fatal 2 '缺少 Python 3.11+（只检查模式，未安装）' }
    $py = Install-Python
    if (-not $py) {
        Fatal 2 ("没能自动安装 Python。请手动安装：{0}`n安装时勾选 Add python.exe to PATH，装好后再双击「修复环境.bat」。" -f 'https://www.python.org/downloads/')
    }
}
Say ("  Python：{0}  {1}  [{2}]" -f $py.exe, $py.version, $py.from) 'Green'

if ($pairRequested) {
    $a = @($py.pre | Where-Object { $_ }) + @('-X','utf8',(Join-Path $Probe 'frida_pair.py'))
    & $py.exe @a
    $rc = $LASTEXITCODE
    Say ("Frida 检查说明已保存：{0}" -f $pairReport) 'Cyan'
    if ($rc -ne 0) { exit $rc }
    Save-PythonPath $py
}
$node = Find-Node
if (-not $node) {
    if ($Check) { Fatal 3 '缺少 Node.js 18+（只检查模式，未安装）' }
    $node = Install-Node
    if (-not $node) {
        Fatal 3 ("没能自动安装 Node.js。请手动安装 LTS 版：{0}，装好后再双击「修复环境.bat」。" -f 'https://nodejs.org/zh-cn/download')
    }
}
Say ("  Node.js：{0}  {1}  [{2}]" -f $node.exe, $node.version, $node.from) 'Green'

if ($Check) {
    $dmiss = @(Get-MissingDeps $py @())
    $bad = $false
    foreach ($d in $Deps) {
        if ($dmiss -contains $d.mod) {
            Say ("  {0}：缺失" -f $d.name) 'Red'
            $bad = $true
        } else {
            Say ("  {0}：已安装" -f $d.name) 'Green'
        }
    }
    if ($bad) { exit 4 }
    if (-not (Test-FridaLocal $py)) { Fatal 5 "Frida 本机读取自检失败。" }
    Say '环境就绪（只检查模式，未做任何安装）' 'Cyan'
    Log '只检查模式：环境就绪'
    exit 0
}

if (-not (Ensure-PyDeps $py)) {
    if (@(Get-MissingDeps $py @()) -contains "frida") { Fatal 5 "Frida 在线安装失败。请双击同目录的「修复环境.bat」，按生成的 Frida配对说明.txt 操作。" }
    Fatal 4 ("依赖库没装好。请手动执行：`n  `"{0}`" -m pip install pillow numpy frida pygame PyOpenGL live2d-py requests edge-tts" -f $py.exe)
}
if (-not (Test-FridaLocal $py)) {
    Fatal 5 'Frida 未能读取本机测试进程。请检查安全软件的拦截记录，再双击同目录的「修复环境.bat」查看配对说明。'
}
Say '  运行依赖：已检查（缺的已尝试自动安装）' 'Green'

# 写回 python-path.txt / node-path.txt，让 启动.bat 和 Python 侧都能找到
#
# ⚠️ 编码有讲究：
#   · python-path.txt 是**批处理**用 `set /p` 读的 → 必须用系统 ANSI（无 BOM），
#     否则 BOM 会被当成路径的一部分；中文路径在中文系统上也能正常工作。
#   · node-path.txt 是 **Python** 读的 → UTF-8 无 BOM。
try {
    Save-PythonPath $py
    [IO.File]::WriteAllText($NodePath, $node.exe, (New-Object Text.UTF8Encoding($false)))
} catch {
    Log ("写 python-path.txt / node-path.txt 失败：{0}" -f $_.Exception.Message)
}

# 就绪标记：下次启动直接跳过检查
try {
    $info = [ordered]@{
        ready          = $true
        at             = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        python         = $py.exe
        python_version = $py.version
        node           = $node.exe
        node_version   = $node.version
        pillow         = (Get-PipVersion $py 'pillow')
        numpy          = (Get-PipVersion $py 'numpy')
        frida          = (Get-PipVersion $py 'frida')
        pygame         = (Get-PipVersion $py 'pygame')
        pyopengl       = (Get-PipVersion $py 'PyOpenGL')
        live2d         = (Get-PipVersion $py 'live2d-py')
        requests       = (Get-PipVersion $py 'requests')
        edge_tts       = (Get-PipVersion $py 'edge-tts')
        keyboard       = (Get-PipVersion $py 'keyboard')
        script         = $MyInvocation.MyCommand.Path
    }
    ($info | ConvertTo-Json) | Set-Content -LiteralPath $Marker -Encoding UTF8
    Log ("已写就绪标记 {0}" -f $Marker)
} catch {
    Log ("写就绪标记失败：{0}" -f $_.Exception.Message)
}

Say '' $null
Say '  环境全部就绪，正在启动……' 'Cyan'
Log '环境就绪'
exit 0
