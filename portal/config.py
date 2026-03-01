"""
Portal Configuration
"""
import os

# Portal paths
PORTAL_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PORTAL_ROOT)

# Database - supports persistent disk via env var
# Set DB_PATH=/var/data/wtf_studio.db on Render for persistence
DB_PATH = os.environ.get('DB_PATH', os.path.join(PORTAL_ROOT, 'private', 'db', 'wtf_studio.db'))

# Storage root - supports persistent disk via env var
# Set STORAGE_ROOT=/var/data/storage on Render for persistence
STORAGE_ROOT = os.environ.get('STORAGE_ROOT', os.path.join(PORTAL_ROOT, 'private', 'storage'))
RAW_DIR = os.path.join(STORAGE_ROOT, 'raw')  # Downloaded original videos
OUTPUT_DIR = os.path.join(STORAGE_ROOT, 'outputs')  # Branded videos
BRANDS_DIR = os.path.join(STORAGE_ROOT, 'brands')  # User brand assets

# Legacy directories (local dev only)
UPLOAD_DIR = os.path.join(PORTAL_ROOT, 'uploads')  # Legacy uploads
TEMP_DIR = os.path.join(PORTAL_ROOT, 'temp')

# Logs
LOG_DIR = os.path.join(PORTAL_ROOT, 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'portal.log')

# Brand assets (legacy system brands - not user brands)
LEGACY_BRANDS_DIR = os.path.join(PORTAL_ROOT, 'imports', 'brands')
TEMPLATE_DIR = os.path.join(LEGACY_BRANDS_DIR, 'wtf_orchestrator')

# FFmpeg
FFMPEG_BIN = os.environ.get('FFMPEG_PATH', 'ffmpeg')
FFPROBE_BIN = os.environ.get('FFPROBE_PATH', 'ffprobe')

# Security
SECRET_KEY = os.environ.get('WTF_SECRET_KEY', 'dev-secret-key-change-in-production')
PORTAL_AUTH_KEY = os.environ.get('WTF_PORTAL_KEY', 'WTF_PORTAL_TEST')

# Job settings
MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500MB
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi'}
CLEANUP_TEMP_AFTER_HOURS = 24

# Ensure directories exist
for directory in [STORAGE_ROOT, RAW_DIR, OUTPUT_DIR, BRANDS_DIR, UPLOAD_DIR, TEMP_DIR, LOG_DIR, os.path.dirname(DB_PATH)]:
    os.makedirs(directory, exist_ok=True)

# Log resolved paths (helps debug persistence issues)
print(f"[CONFIG] DB_PATH: {DB_PATH}")
print(f"[CONFIG] STORAGE_ROOT: {STORAGE_ROOT}")
print(f"[CONFIG] RAW_DIR: {RAW_DIR}")
print(f"[CONFIG] OUTPUT_DIR: {OUTPUT_DIR}")
print(f"[CONFIG] BRANDS_DIR: {BRANDS_DIR}")
