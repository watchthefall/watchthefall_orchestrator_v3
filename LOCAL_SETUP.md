# WatchTheFall Orchestrator v3 - Local Setup Guide

## Python Version Compatibility

The repository has been updated to support Python 3.14. The following changes were made to ensure compatibility:

1. Updated `runtime.txt` to specify `python-3.14`
2. Modified `requirements.txt` to use flexible version constraint for `psutil>=5.9.8` instead of fixed version
3. Verified all code components are compatible with Python 3.14

## Prerequisites

- Python 3.14 installed on your system
- FFmpeg installed and available in your system PATH
- Git for Windows (if cloning from Git)

## Quick Setup (Windows)

### Using PowerShell Script:
```powershell
# Run the setup script
.\setup_local.ps1
```

### Using Batch Script:
```batch
# Run the setup script
setup_local.bat
```

## Manual Setup

### 1. Create Virtual Environment
```bash
python -m venv venv
```

### 2. Activate Virtual Environment
- **Windows (Command Prompt)**: `venv\Scripts\activate`
- **Windows (PowerShell)**: `venv\Scripts\Activate.ps1`

### 3. Upgrade Pip
```bash
python -m pip install --upgrade pip
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Create Required Directories
```bash
mkdir portal\uploads
mkdir portal\outputs  
mkdir portal\temp
mkdir portal\logs
mkdir portal\db
```

## Running the Application

### Start the Server
```bash
python run_portal.py
```

### Access the Application
- **Portal Dashboard**: http://localhost:5000/portal/
- **Test Endpoint**: http://localhost:5000/portal/test
- **API Root**: http://localhost:5000/

## Troubleshooting

### Common Issues

1. **FFmpeg not found**:
   - Install FFmpeg from https://ffmpeg.org/download.html
   - Add FFmpeg to your system PATH
   - Restart your command prompt/terminal after adding to PATH

2. **Permission errors**:
   - Make sure you're running in a virtual environment
   - On Windows, you may need to enable script execution for PowerShell: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

3. **Package installation errors**:
   - Make sure you have the latest pip version: `python -m pip install --upgrade pip`
   - Try installing packages individually if needed

### Verification Commands
```bash
# Check Python version
python --version

# Check pip version
pip --version

# Test key imports
python -c "import flask, requests, sqlite3, json, PIL, numpy, yaml, mutagen, yt_dlp, jwt, cryptography, psutil"
```

## Dependencies Compatibility

All dependencies in `requirements.txt` are compatible with Python 3.14:
- Flask: Latest version supports Python 3.14
- yt-dlp: Latest version supports Python 3.14
- Pillow: Latest version supports Python 3.14
- numpy: Latest version supports Python 3.14
- psutil: Updated to flexible version constraint to ensure Python 3.14 compatibility
- Other packages: All use standard APIs that are forward-compatible

## Notes

- The application will create a SQLite database automatically at `portal/db/portal.db`
- Processed videos will be stored in the `portal/outputs/` directory
- Log files will be created in the `portal/logs/` directory
- The application is designed to work with video files from social media platforms