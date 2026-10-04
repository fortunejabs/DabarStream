# =============================================================================
# DabarStream voice-capture client launcher.
#
# Runs client.py inside the project virtualenv, so the correct interpreter and
# dependency set are always used no matter which Python is on PATH. Double-click
# or run:  powershell -ExecutionPolicy Bypass -File .\run_client.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv   = Join-Path $PSScriptRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtualenv found at $venv" -ForegroundColor Red
    Write-Host "Create one with the Python that has the prebuilt PyAudio wheels:" -ForegroundColor Yellow
    Write-Host "    py -3.12 -m venv .venv" -ForegroundColor Yellow
    Write-Host "    .\.venv\Scripts\python.exe -m pip install pyaudio faster-whisper numpy 'python-socketio[client]' sounddevice pynput" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# --- Shared stream key -------------------------------------------------------
# The VPS rejects every control event unless DABARSTREAM_KEY matches the value
# in its systemd unit. Load it from the environment, else from stream_key.txt
# (git-ignored), and export it so the child process inherits it.
if (-not $env:DABARSTREAM_KEY) {
    $keyFile = Join-Path $PSScriptRoot "stream_key.txt"
    if (Test-Path $keyFile) {
        $env:DABARSTREAM_KEY = (Get-Content $keyFile -Raw).Trim()
        Write-Host "Loaded DABARSTREAM_KEY from stream_key.txt" -ForegroundColor Green
    } else {
        Write-Host "WARNING: no stream key configured." -ForegroundColor Yellow
        Write-Host "  The VPS will reject every verse ('[Auth]: Rejected' in" -ForegroundColor Yellow
        Write-Host "  'sudo journalctl -u dabarstream'). Create stream_key.txt" -ForegroundColor Yellow
        Write-Host "  next to this script containing the DABARSTREAM_KEY value," -ForegroundColor Yellow
        Write-Host "  or set the DABARSTREAM_KEY environment variable." -ForegroundColor Yellow
    }
}

Write-Host "Starting DabarStream capture client with $python" -ForegroundColor Cyan
& $python client.py
Read-Host "Client stopped. Press Enter to exit"
