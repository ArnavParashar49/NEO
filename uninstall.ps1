$ErrorActionPreference = 'SilentlyContinue'

$InstallDir = "$env:USERPROFILE\.aria"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "    ARIA Background AI Uninstaller    " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

Write-Host "Stopping ARIA processes..."
# Find any pythonw process running from the .aria folder
Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match "main\.py" -and $_.ExecutablePath -match "\.aria" } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
}

Write-Host "Removing ARIA installation from $InstallDir..."
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
}

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "        Uninstallation Complete.      " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
