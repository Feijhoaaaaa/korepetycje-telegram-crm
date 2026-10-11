$ErrorActionPreference = "Stop"
$AppDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$Executable = Join-Path $AppDirectory "KorepetycjeCRM.exe"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Korepetycje CRM.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Executable
$Shortcut.WorkingDirectory = $AppDirectory
$Shortcut.IconLocation = "$Executable,0"
$Shortcut.Description = "Uruchom Korepetycje CRM i Telegram Mini App"
$Shortcut.Save()
Write-Host "Utworzono skrot: $ShortcutPath" -ForegroundColor Green
