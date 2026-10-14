$ErrorActionPreference = "Stop"
$Layout = Get-Content -LiteralPath (Join-Path $PSScriptRoot "..\source-layout.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$Source = Join-Path (Split-Path $PSScriptRoot -Parent) $Layout.powershell_files.'start-chrome-desktop.ps1'
& $Source @args
exit $LASTEXITCODE
