# Creates a Desktop shortcut to the packaged Windows executable.
# Run from the project root after build_windows.bat succeeds.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$exeDir = Join-Path $root "dist\AI Race Engineer"
$exePath = Join-Path $exeDir "AI Race Engineer.exe"

if (-not (Test-Path $exePath)) {
    Write-Error "Executable not found: $exePath`nRun build_windows.bat first."
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "AI Race Engineer.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $exeDir
$shortcut.IconLocation = "$exePath,0"
$shortcut.Description = "AI Race Engineer overlay for iRacing"
$shortcut.Save()

Write-Host "Desktop shortcut created:"
Write-Host "  $shortcutPath"
