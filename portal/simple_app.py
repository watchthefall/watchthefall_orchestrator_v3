"""
Simplified WatchTheFall Portal - Flask Application

This is a simplified version that focuses only on core download functionality
without the branding/orchestration features.
"""

from flask import Flask, request, jsonify, render_template, send_from_directory
import os
import uuid
from werkzeug.utils import secure_filename
import subprocess
import tempfile
import threading
from yt_dlp import YoutubeDL

# Import configuration
try:
    from .config import (
        SECRET_KEY, PORTAL_AUTH_KEY, OUTPUT_DIR,
        MAX_UPLOAD_SIZE, BRANDS_DIR
    )
    from .database import log_event
except ImportError:
    # Fallback when run standalone
    SECRET_KEY = os.environ.get('SECRET_KEY', 'fallback-secret-key')
    PORTAL_AUTH_KEY = os.environ.get('PORTAL_AUTH_KEY', 'fallback-auth-key')
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
    MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
    BRANDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imports', 'brands')
    
    def log_event(event_type, data, message):
        print(f"[EVENT] {event_type}: {message}")

app = Flask(__name__, 
            template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
            static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

@app.route('/')
def home():
    """Serve the main dashboard."""
    return render_template('clean_dashboard.html')

@app.route('/portal/')
def portal_home():
    """Serve the main dashboard at /portal/ path."""
    return render_template('clean_dashboard.html')

# ... rest of the existing functionality would go here

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)