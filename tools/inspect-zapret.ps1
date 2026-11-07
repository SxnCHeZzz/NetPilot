# NetPilot R5: read-only, safe diagnostics. Does not change any service or process.
$ErrorActionPreference = 'Continue'
Write-Host '--- Zapret winws processes (read-only) ---'
try {
    Get-CimInstance Win32_Process -Filter "Name = 'winws.exe' OR Name = 'winws2.exe'" |
        Select-Object ProcessId, Name, ExecutablePath, CommandLine | Format-List
} catch { Write-Warning $_.Exception.Message }
Write-Host '--- Zapret Windows services (read-only) ---'
try {
    Get-CimInstance Win32_Service |
        Where-Object { $_.Name -match 'zapret' -or $_.PathName -match 'winws(2)?\.exe' } |
        Select-Object Name, State, StartMode, ProcessId, PathName | Format-List
} catch { Write-Warning $_.Exception.Message }
Write-Host '--- UAC elevation ---'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
Write-Host ('Elevated: ' + $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
Write-Host 'Read-only scan complete. Do not share sensitive proxy arguments publicly.'
