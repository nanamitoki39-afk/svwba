$ErrorActionPreference = 'Stop'
$probe = $PSScriptRoot
$python = (Get-Content -LiteralPath (Join-Path $probe 'python-path.txt') -Raw).Trim()
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (!(Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f (Join-Path $probe 'subtitle_preview.py')) -WorkingDirectory $probe -WindowStyle Hidden
