# Start Babilious and open it in the browser.
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
Set-Location $PSScriptRoot
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Run .\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}
# Make sure Ollama is up (it usually runs in the system tray already).
try { Invoke-WebRequest "http://127.0.0.1:11434/api/version" -UseBasicParsing -TimeoutSec 2 | Out-Null }
catch {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Host "Starting Ollama..."
        Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 2
    } else {
        Write-Host "Ollama not found: Babilious will run in offline mode." -ForegroundColor Yellow
    }
}
Start-Job { Start-Sleep -Seconds 2; Start-Process "http://127.0.0.1:5000" } | Out-Null
& .\.venv\Scripts\python.exe app.py
