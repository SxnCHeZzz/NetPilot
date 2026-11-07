# Read-only diagnostics for an ambiguous service named zapret.
# Does NOT stop, delete, install or reconfigure any service.
$ErrorActionPreference = 'Continue'
Write-Host 'NetPilot Zapret service (read only)' -ForegroundColor Green
try {
    $service = Get-CimInstance Win32_Service -Filter "Name='zapret'" -ErrorAction Stop
    if ($null -eq $service) {
        Write-Host 'Service zapret: absent'
    } else {
        Write-Host ('Service name: ' + $service.Name)
        Write-Host ('State: ' + $service.State)
        Write-Host ('Start mode: ' + $service.StartMode)
        Write-Host ('Service process ID: ' + $service.ProcessId)
        Write-Host 'Executable command: see Windows Services > zapret > Properties > Path to executable'
        Write-Host '(Do not post full paths/arguments until you review them for private data.)'
    }
} catch {
    Write-Host ('Service read failed: ' + $_.Exception.Message) -ForegroundColor Yellow
}
foreach ($name in @('winws.exe', 'winws2.exe')) {
    try {
        $found = @(Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction Stop)
        foreach ($p in $found) {
            Write-Host ("Process $name PID=" + $p.ProcessId + ' ParentPID=' + $p.ParentProcessId)
            Write-Host ('  Executable path available: ' + [bool]$p.ExecutablePath)
            Write-Host ('  Command line available: ' + [bool]$p.CommandLine)
        }
    } catch {
        Write-Host ("Process query $name failed: " + $_.Exception.Message) -ForegroundColor Yellow
    }
}
