$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Virtual environment not found." -ForegroundColor Yellow
    exit 1
}

& $Python (Join-Path $ProjectDir "salesforce_oauth.py") --login
