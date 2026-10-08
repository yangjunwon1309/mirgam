param([switch]$Launch)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourcePackage = Join-Path $projectRoot 'build\customer-selection-release\Mirgam'
$targetPackage = Join-Path $projectRoot 'dist\Mirgam'
$targetExe = Join-Path $targetPackage 'Mirgam.exe'

try {
    if (!(Test-Path -LiteralPath (Join-Path $sourcePackage 'Mirgam.exe')) -or
        !(Test-Path -LiteralPath (Join-Path $sourcePackage '_internal'))) {
        throw 'The prepared update is missing. Rebuild the update package first.'
    }
    $runningApp = Get-Process -Name 'Mirgam' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $targetExe }
    if ($runningApp) {
        throw 'Save any pending orders and close the Mirgam program window, then run Update-Mirgam.cmd again.'
    }

    # Replace program files only. Each installation keeps its writable data.
    $dataPath = Join-Path $targetPackage 'data'
    $dataHashes = @(Get-ChildItem -LiteralPath $dataPath -Recurse -File |
        Sort-Object FullName | Get-FileHash -Algorithm SHA256)
    Copy-Item -LiteralPath (Join-Path $sourcePackage '_internal') -Destination $targetPackage -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $sourcePackage 'Mirgam.exe') -Destination $targetExe -Force
    foreach ($saved in $dataHashes) {
        if ((Get-FileHash -LiteralPath $saved.Path -Algorithm SHA256).Hash -ne $saved.Hash) {
            throw "Data verification failed: $($saved.Path)"
        }
    }
    Write-Host 'Updated dist\Mirgam\Mirgam.exe. All existing data files were preserved.'
    if ($Launch) {
        Start-Process -FilePath $targetExe -WorkingDirectory $targetPackage
    }
} catch {
    Write-Error $_
    exit 1
}
