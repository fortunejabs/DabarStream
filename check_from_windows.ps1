# =============================================================================
# DabarStream - external reachability test. RUN THIS ON THE WINDOWS PC.
#
# The check_vps.sh script runs ON the VPS, so its curl only proves the LOCAL
# firewall path. This script proves that traffic from the INTERNET reaches the
# app, which is the only thing OBS in the church actually depends on.
#
# Usage:  powershell -ExecutionPolicy Bypass -File .\check_from_windows.ps1
# =============================================================================

$ip   = "193.123.179.93"
$port = 5000
$base = "http://${ip}:${port}"

Write-Host "=== DabarStream external reachability test ===" -ForegroundColor Cyan
Write-Host "Target: $base"
Write-Host ""

# --- Step 1: raw TCP connect (Layer 1 + Layer 2 from the outside) -----------
Write-Host "--- Step 1: TCP connect to ${ip}:${port} ---" -ForegroundColor Yellow
# -InformationLevel Quiet => just $true/$false
$tcpOk = Test-NetConnection -ComputerName $ip -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
if ($tcpOk) {
    Write-Host "PASS: TCP $port is reachable from this PC." -ForegroundColor Green
    Write-Host "      => OCI Security List/NSG AND the OS firewall both allow it."
} else {
    Write-Host "FAIL: TCP $port is NOT reachable from this PC." -ForegroundColor Red
    Write-Host "      => A firewall layer is blocking. Which one?"
    Write-Host "         - Connection REFUSED  => nothing listening, or local REJECT"
    Write-Host "         - Connection TIMED OUT => silently dropped (Security List/NSG"
    Write-Host "           missing the ingress rule, or firewalld target is DROP)"
}
Write-Host ""

# --- Step 2: HTTP layer (proves the Flask app answers) ---------------------
Write-Host "--- Step 2: HTTP probes ---" -ForegroundColor Yellow

function Probe-Url([string]$label, [string]$url) {
    $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 10 $url 2>$null
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        Write-Host ("  {0,-28} -> curl exit {1} (no HTTP response)" -f $label, $exit) -ForegroundColor Red
    } else {
        $color = if ($code -eq "200") { "Green" } elseif ($code -eq "400") { "Yellow" } else { "Gray" }
        Write-Host ("  {0,-28} -> HTTP {1}" -f $label, $code) -ForegroundColor $color
    }
    return $code
}

Probe-Url "GET /"                   "$base/"                                    | Out-Null
Probe-Url "GET /overlay"            "$base/overlay"                             | Out-Null

Write-Host ""
Write-Host "--- Step 3: Engine.IO version the SERVER speaks ---" -ForegroundColor Yellow
foreach ($v in 4, 3, 2) {
    $url  = "$base/socket.io/?EIO=$v&transport=polling&t=$(Get-Random)"
    $code = & curl.exe -s -o NUL -w "%{http_code}" --max-time 10 $url 2>$null
    $body = & curl.exe -s --max-time 10 $url 2>$null
    if ($body) { $body = $body.Substring(0, [Math]::Min(110, $body.Length)) }
    Write-Host ("  EIO={0} -> HTTP {1}   {2}" -f $v, $code, $body)
}
Write-Host ""
Write-Host "The EIO value that returns 200 is the one the CDN <script> tag in the" -ForegroundColor Cyan
Write-Host "OBS overlay must match:  4 -> socket.io 4.0.1   3 -> socket.io 2.5.0" -ForegroundColor Cyan
Write-Host ""
Write-Host "=== done ===" -ForegroundColor Cyan
