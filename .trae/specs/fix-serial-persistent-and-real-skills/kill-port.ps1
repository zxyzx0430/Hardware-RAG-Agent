$conns = Get-NetTCPConnection -LocalPort 58080 -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    try {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop
        Write-Host ("Killed PID " + $c.OwningProcess)
    } catch {
        Write-Host ("Failed to kill PID " + $c.OwningProcess + ": " + $_.Exception.Message)
    }
}
Start-Sleep -Seconds 2
$still = Get-NetTCPConnection -LocalPort 58080 -ErrorAction SilentlyContinue
if ($still) {
    Write-Host "PORT STILL OCCUPIED"
} else {
    Write-Host "PORT FREE"
}
