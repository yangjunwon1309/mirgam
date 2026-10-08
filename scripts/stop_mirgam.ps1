[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5000
)

$ErrorActionPreference = 'Stop'

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -eq $Port })

if ($listeners.Count -eq 0) {
    Write-Host "Port $Port is already free."
    exit 0
}

$targets = @()
foreach ($listener in $listeners) {
    $processId = [int]$listener.OwningProcess
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if (-not $processInfo) { continue }

    $exeName = [System.IO.Path]::GetFileName($processInfo.ExecutablePath)
    $commandLine = [string]$processInfo.CommandLine
    $isMirgam = ($exeName -ieq 'Mirgam.exe') -or
        ($commandLine -match '(?i)(desktop_launcher\.py|scripts[\\/]run_(client|local|mirgam)\.py)')

    if ($isMirgam) {
        $targets += [pscustomobject]@{
            ProcessId = $processId
            Executable = $processInfo.ExecutablePath
            CommandLine = $commandLine
        }
    } else {
        Write-Warning "Port $Port is owned by a process that does not look like Mirgam (PID $processId, $exeName); leaving it running."
    }
}

$targets = @($targets | Sort-Object ProcessId -Unique)
if ($targets.Count -eq 0) {
    Write-Host "No Mirgam process was found listening on port $Port."
    exit 2
}

Write-Host "Mirgam listener(s) on port ${Port}:"
$targets | ForEach-Object {
    Write-Host "  PID $($_.ProcessId): $($_.Executable)"
}

$answer = Read-Host 'Stop these Mirgam process(es)? (y/N)'
if ($answer -notmatch '^(y|yes)$') {
    Write-Host 'Cancelled; nothing was stopped.'
    exit 1
}

foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -ErrorAction Stop
    Write-Host "Stopped PID $($target.ProcessId)."
}

Start-Sleep -Milliseconds 500
$stillListening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($stillListening.Count -eq 0) {
    Write-Host "Port $Port is now free."
    exit 0
}

Write-Warning "Port $Port is still in use. Check the remaining listener PID(s): $($stillListening.OwningProcess -join ', ')"
exit 3
