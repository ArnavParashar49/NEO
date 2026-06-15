$ErrorActionPreference = 'Stop'

$RepoUrl = "https://github.com/ArnavParashar49/ARIA.git"
$InstallDir = "$env:USERPROFILE\.aria"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "    ARIA Background AI Installer      " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Installing to $InstallDir..."

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Host "Error: git is not installed. Please install Git for Windows first." -ForegroundColor Red
    exit 1
}

if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "Error: python is not installed. Please install Python 3.10+ first." -ForegroundColor Red
    exit 1
}

if (Test-Path $InstallDir) {
    Write-Host "Updating existing ARIA installation..."
    Set-Location $InstallDir
    git pull origin main
} else {
    Write-Host "Cloning ARIA repository..."
    git clone $RepoUrl $InstallDir
    Set-Location $InstallDir
}

Write-Host "Setting up Python environment..."
if (-not (Test-Path "$InstallDir\.venv")) {
    python -m venv .venv
}

$PipPath = "$InstallDir\.venv\Scripts\pip.exe"
$PythonPath = "$InstallDir\.venv\Scripts\python.exe"
$PythonwPath = "$InstallDir\.venv\Scripts\pythonw.exe"

& $PipPath install --upgrade pip setuptools wheel certifi
& $PipPath install -r requirements.txt

Write-Host "Downloading ChromaDB local embedding model (~80MB)..."
& $PythonPath -c "import chromadb.utils.embedding_functions as ef; ef.DefaultEmbeddingFunction()(['init'])"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "         Installation Complete!       " -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

Write-Host "Starting ARIA background process..."
# Start pythonw so no console window appears
Start-Process -FilePath $PythonwPath -ArgumentList "main.py" -WorkingDirectory $InstallDir

Write-Host "ARIA is now running invisibly in the background." -ForegroundColor Green
Write-Host "To stop ARIA, run the uninstall script or kill pythonw.exe in Task Manager."
