$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $scriptDir "start_widget.vbs"
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "BillingPeriodWidget.lnk"

$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($shortcut)
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = '"' + $target + '"'
$lnk.WorkingDirectory = $scriptDir
$lnk.WindowStyle = 7
$lnk.Description = "Billing Period Widget"
$lnk.Save()

Write-Host "Installed startup shortcut: $shortcut"
