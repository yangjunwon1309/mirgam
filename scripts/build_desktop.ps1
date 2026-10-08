param(
    [switch]$Clean,
    [string]$OutputDirectory = 'build\sqlite-release'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
$buildRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'build'))
if (!$releaseRoot.StartsWith($buildRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must be a staging directory below the project build folder.'
}
if ($Clean) { Write-Host 'Clean rebuild requested. Live dist data will not be touched.' }

# Staged program files only: no passwords, farm CSVs, SQLite or live dist data.
python -m PyInstaller --noconfirm --clean --onedir --name Mirgam `
    --distpath $releaseRoot --workpath 'build\sqlite-client-work' --specpath 'build' `
    --add-data 'apps/templates;apps/templates' --copy-metadata werkzeug `
    --exclude-module pandas --exclude-module sqlalchemy --exclude-module alembic `
    --exclude-module IPython --exclude-module matplotlib --exclude-module scipy `
    --exclude-module torch desktop_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Client build failed.' }

python -m PyInstaller --noconfirm --clean --onedir --name MirgamServer `
    --distpath $releaseRoot --workpath 'build\sqlite-server-work' --specpath 'build' `
    --add-data 'server/migrations;server/migrations' --copy-metadata werkzeug `
    --hidden-import sqlalchemy.dialects.sqlite --collect-submodules alembic `
    --exclude-module pandas --exclude-module IPython --exclude-module matplotlib `
    --exclude-module scipy --exclude-module torch server_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Server build failed.' }

Copy-Item -LiteralPath 'client.example.json' -Destination (Join-Path $releaseRoot 'Mirgam\client.example.json') -Force
foreach ($package in @('Mirgam', 'MirgamServer')) {
    Copy-Item -LiteralPath 'docs\CENTRAL_SQLITE_IMPLEMENTATION.md' `
        -Destination (Join-Path $releaseRoot "$package\README-SQLITE.md") -Force
}
Write-Host "Staged client and server packages: $releaseRoot"
Write-Host 'Existing dist\Mirgam and its CSVs were not updated. See README-SQLITE.md before deployment.'
