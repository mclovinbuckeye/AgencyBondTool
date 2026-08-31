$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectDir "bond_processor.py"

if (-not (Test-Path $Python)) {
    Write-Host "Virtual environment not found." -ForegroundColor Yellow
    Write-Host "Create it with: python -m venv .venv"
    Write-Host "Then install dependencies with: .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

& $Python $Script @args
