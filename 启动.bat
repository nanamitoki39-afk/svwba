@echo off
if exist "%~dp0svwb-probe\launch.ps1" goto ready
powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Please extract the complete ZIP to a folder before running the BAT. Required launcher files are missing.','Shadowverse Assistant') | Out-Null"
exit /b 1
:ready
if not exist "%SystemRoot%\System32\wscript.exe" goto fallback
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0svwb-probe\launch.vbs"
exit /b 0
:fallback
start "" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0svwb-probe\launch.ps1"
exit /b 0
