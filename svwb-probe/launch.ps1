$ErrorActionPreference = 'Stop'
$probe = $PSScriptRoot
$logs = Join-Path $probe 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'startup.log'
$progressWindow = $null
try {
    & (Join-Path $probe 'setup_env.ps1') -Quick *> $log
    if ($LASTEXITCODE -ne 0) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $progressWindow = New-Object System.Windows.Forms.Form
        $progressWindow.Text = '影之诗助手 · 准备运行环境'
        $progressWindow.Size = New-Object System.Drawing.Size(480,160)
        $progressWindow.StartPosition = 'CenterScreen'
        $progressWindow.FormBorderStyle = 'FixedDialog'
        $progressWindow.ControlBox = $false
        $label = New-Object System.Windows.Forms.Label
        $label.AutoSize = $false
        $label.Location = New-Object System.Drawing.Point(18,18)
        $label.Size = New-Object System.Drawing.Size(430,90)
        $label.Text = "首次启动或缺少依赖，正在准备运行环境。`r`n可能需要几分钟，完成后会自动打开助手。`r`n进度日志：svwb-probe\logs\startup-environment.log"
        $progressWindow.Controls.Add($label)
        $progressWindow.Show()
        [System.Windows.Forms.Application]::DoEvents()
        $setupLog = Join-Path $logs 'startup-environment.log'
        $setupError = Join-Path $logs 'startup-environment-error.log'
        $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Quiet' -f (Join-Path $probe 'setup_env.ps1')
        $setup = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput $setupLog -RedirectStandardError $setupError
        while (-not $setup.HasExited) {
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 150
            $setup.Refresh()
        }
        $setup.WaitForExit()
        $progressWindow.Close()
        $progressWindow.Dispose()
        $progressWindow = $null
        if ($setup.ExitCode -ne 0) { throw "环境准备失败，请运行修复环境.bat。详细日志：$setupLog；$setupError" }
    }
    $pythonPath = Join-Path $probe 'python-path.txt'
    $python = $null
    if (Test-Path -LiteralPath $pythonPath) {
        $candidate = (Get-Content -LiteralPath $pythonPath -Raw).Trim()
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            $python = $candidate
        }
    }
    if (-not $python) { throw '没有找到刚配置的 Python，请运行修复环境.bat。' }
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
    $child = Start-Process -FilePath $pythonw -ArgumentList ('"{0}" --verified' -f (Join-Path $probe 'startup_bootstrap.py')) -WorkingDirectory $probe -WindowStyle Hidden -PassThru
    if ($child.WaitForExit(3500) -and $child.ExitCode -ne 0) {
        throw '助手启动后异常退出。详细错误在 svwb-probe\logs\console-app.log。'
    }
} catch {
    if ($progressWindow) { $progressWindow.Close(); $progressWindow.Dispose() }
    $_ | Out-String | Add-Content -LiteralPath $log -Encoding UTF8
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(("助手未能启动：`r`n{0}`r`n`r`n日志：{1}" -f $_.Exception.Message,$log),'影之诗助手') | Out-Null
    exit 1
}
