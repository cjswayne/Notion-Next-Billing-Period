$startup = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "BillingPeriodWidget.lnk"
if (Test-Path $shortcut) {
    Remove-Item $shortcut
    Write-Host "Removed: $shortcut"
} else {
    Write-Host "No startup shortcut found."
}
