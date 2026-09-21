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

Write-Host "Starting DabarStream capture client with $python" -ForegroundColor Cyan
& $python client.py
Read-Host "Client stopped. Press Enter to exit"
