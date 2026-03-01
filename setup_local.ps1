Write-Host "Setting up WatchTheFall Orchestrator v3 for local development..." -ForegroundColor Green

# Create virtual environment
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
python -m venv venv

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
.\venv\Scripts\Activate.ps1

# Upgrade pip
Write-Host "Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install dependencies
Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Yellow
pip install -r requirements.txt

# Create necessary directories if they don't exist
Write-Host "Creating required directories..." -ForegroundColor Yellow
if (!(Test-Path "portal\uploads")) { New-Item -ItemType Directory -Path "portal\uploads" }
if (!(Test-Path "portal\outputs")) { New-Item -ItemType Directory -Path "portal\outputs" }
if (!(Test-Path "portal\temp")) { New-Item -ItemType Directory -Path "portal\temp" }
if (!(Test-Path "portal\logs")) { New-Item -ItemType Directory -Path "portal\logs" }
if (!(Test-Path "portal\db")) { New-Item -ItemType Directory -Path "portal\db" }

Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "To run the application, activate your virtual environment and run: python run_portal.py" -ForegroundColor White
Write-Host ""
Write-Host "Portal will be available at: http://localhost:5000/portal/" -ForegroundColor White

Pause