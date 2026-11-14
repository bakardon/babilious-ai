# Babilious one-time setup for Windows PowerShell.
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n== Babilious setup ==" -ForegroundColor Magenta

# 1. Python 3.10+
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { throw "Python not found. Install Python 3.10+ from python.org and tick 'Add to PATH'." }
$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.10") { throw "Python $ver found; 3.10 or newer is required." }
Write-Host "Python $ver OK"

# 2. Virtual environment + packages
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
Write-Host "Python packages installed"

# 3. Ollama (local AI). Free; not installed by this script.
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "`nOllama is not installed. Get it free from https://ollama.com/download" -ForegroundColor Yellow
    Write-Host "Babilious still starts without it, but answers only from local notes."
} else {
    Write-Host "Ollama found: $(ollama --version)"
    $models = (ollama list) -join "`n"
    if ($models -notmatch "llama3\.2") {
        $answer = Read-Host "Download the default model llama3.2 (about 2 GB)? [y/N]"
        if ($answer -match "^[yY]") { ollama pull llama3.2 }
    } else {
        Write-Host "Default model llama3.2 is installed"
    }
}

# 4. Voice models (optional; they also download on first use)
$hasVoice = (Test-Path "data\models\piper\en_US-ryan-medium.onnx")
if (-not $hasVoice) {
    $answer = Read-Host "Download the voice models now: speech recognition + Babilious' voice (about 210 MB)? [y/N]"
    if ($answer -match "^[yY]") {
        & .\.venv\Scripts\python.exe -c @'
import pathlib
import piper.download_voices as dv
from faster_whisper import WhisperModel
from openwakeword.utils import download_models
root = pathlib.Path("data/models")
for d in ("piper", "whisper", "openwakeword"):
    (root / d).mkdir(parents=True, exist_ok=True)
dv.download_voice("en_US-ryan-medium", root / "piper")
WhisperModel("base.en", device="cpu", compute_type="int8", download_root=str(root / "whisper"))
download_models(["hey_jarvis"], target_directory=str(root / "openwakeword"))
print("Voice models ready.")
'@
    }
} else {
    Write-Host "Voice models are installed"
}

# 5. Optional .env
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example" }

Write-Host "`nSetup complete. Start Babilious with:  .\start.ps1`n" -ForegroundColor Green
