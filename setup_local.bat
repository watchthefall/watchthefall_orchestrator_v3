@echo off
echo Setting up WatchTheFall Orchestrator v3 for local development...

REM Create virtual environment
echo Creating virtual environment...
python -m venv venv

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip

REM Install dependencies
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

REM Create necessary directories if they don't exist
echo Creating required directories...
if not exist "portal\uploads" mkdir portal\uploads
if not exist "portal\outputs" mkdir portal\outputs
if not exist "portal\temp" mkdir portal\temp
if not exist "portal\logs" mkdir portal\logs
if not exist "portal\db" mkdir portal\db

echo Setup complete!
echo To run the application, activate your virtual environment and run: python run_portal.py
echo.
echo Portal will be available at: http://localhost:5000/portal/
pause