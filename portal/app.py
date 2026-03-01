"""
WatchTheFall Portal - Flask Application
"""
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for, session, flash
import os
import json
import uuid
import time
from werkzeug.utils import secure_filename
import subprocess
import tempfile
import threading
from yt_dlp import YoutubeDL
import hashlib
import sqlite3
from functools import wraps

def ensure_video_stream(path):
    import subprocess, json, os

    probe_cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", path
    ]

    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    try:
        info = json.loads(result.stdout)
        video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        return len(video_streams) > 0
    except:
        return False

# Import video processing utilities
from .video_processor import VideoProcessor, normalize_video
from .brand_loader import get_available_brands

# Import configuration
from .config import (
    SECRET_KEY, PORTAL_AUTH_KEY, OUTPUT_DIR, RAW_DIR,
    MAX_UPLOAD_SIZE, BRANDS_DIR
)
from .database import log_event


# Authentication functions

def hash_password(password):
    """Hash a password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()


def init_users_db():
    """Initialize the users database table"""
    from .config import DB_PATH
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=30000')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        tier TEXT DEFAULT 'Explorer',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Migration: Add tier column if missing
    try:
        c.execute("SELECT tier FROM users LIMIT 1")
    except sqlite3.OperationalError:
        print("[DATABASE] Running migration: Adding tier column to users table")
        c.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'Explorer'")
        conn.commit()
        print("[DATABASE] Migration completed: tier column added with Explorer default")
    
    conn.commit()
    conn.close()


def authenticate_user(email, password):
    """Authenticate a user by email and password"""
    from .config import DB_PATH
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA busy_timeout=30000')
    c = conn.cursor()
    c.execute('SELECT id, password_hash FROM users WHERE email = ?', (email,))
    result = c.fetchone()
    conn.close()
    
    if result:
        user_id, stored_hash = result
        if stored_hash == hash_password(password):
            return user_id
    return None


def register_user(email, password):
    """Register a new user"""
    from .config import DB_PATH
    import os
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.execute('PRAGMA busy_timeout=30000')
        c = conn.cursor()
        password_hash = hash_password(password)
        
        # Determine tier based on ADMIN_EMAILS env var
        admin_emails = os.environ.get('ADMIN_EMAILS', '').split(',')
        admin_emails = [e.strip().lower() for e in admin_emails if e.strip()]
        
        tier = 'Studio' if email.lower() in admin_emails else 'Explorer'
        
        c.execute('INSERT INTO users (email, password_hash, tier) VALUES (?, ?, ?)', (email, password_hash, tier))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        
        print(f"[AUTH] Registered user: {email} with tier: {tier}")
        return user_id
    except sqlite3.IntegrityError:
        # Email already exists
        return None


def login_required(f):
    """Decorator to require login for certain routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


app = Flask(__name__, 
            template_folder='templates',
            static_folder='static',
            static_url_path='/static')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

# Initialize databases
from .database import init_db
init_db()
init_users_db()

# Authentication routes
@app.route('/portal/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user_id = register_user(email, password)
        if user_id:
            session['user_id'] = user_id
            session['email'] = email
            flash('Registration successful!', 'success')
            return redirect(url_for('download'))
        else:
            flash('Email already exists or registration failed', 'error')
    
    return render_template('register.html')

@app.route('/portal/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user_id = authenticate_user(email, password)
        if user_id:
            session['user_id'] = user_id
            session['email'] = email
            flash('Login successful!', 'success')
            return redirect(url_for('download'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/portal/logout')
def logout():
    session.pop('user_id', None)
    session.pop('email', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))


# Default routing based on login status
@app.route('/portal/')
def portal_home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    else:
        return redirect(url_for('download'))


# Debug endpoint to verify app is loading routes correctly
@app.route("/__debug_alive")
def debug_alive():
    return "alive", 200

@app.route("/__debug_routes")
def debug_routes():
    """List all registered routes"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': str(rule)
        })
    return jsonify({
        'status': 'ok',
        'routes': routes
    })

@app.route("/__debug_env")
def debug_env():
    """Show environment variables"""
    import os
    env_vars = {}
    for key in os.environ:
        # Hide sensitive values
        if any(sensitive in key.lower() for sensitive in ['key', 'secret', 'password', 'token']):
            env_vars[key] = '***HIDDEN***'
        else:
            env_vars[key] = os.environ[key]
    
    return jsonify({
        'status': 'ok',
        'environment': env_vars
    })

@app.route("/__debug_ffmpeg")
def debug_ffmpeg():
    """Show FFmpeg configuration"""
    from .config import FFMPEG_BIN, FFPROBE_BIN
    import subprocess
    import os
    
    ffmpeg_info = {
        'ffmpeg_bin': FFMPEG_BIN,
        'ffprobe_bin': FFPROBE_BIN,
        'ffmpeg_exists': os.path.exists(FFMPEG_BIN) or subprocess.run(['which', FFMPEG_BIN], capture_output=True).returncode == 0 if os.name != 'nt' else True,
        'ffprobe_exists': os.path.exists(FFPROBE_BIN) or subprocess.run(['which', FFPROBE_BIN], capture_output=True).returncode == 0 if os.name != 'nt' else True
    }
    
    # Try to get FFmpeg version
    try:
        result = subprocess.run([FFMPEG_BIN, '-version'], capture_output=True, text=True, timeout=5)
        ffmpeg_info['version'] = result.stdout.split('\n')[0] if result.stdout else 'Unknown'
    except Exception as e:
        ffmpeg_info['version_error'] = str(e)
    
    return jsonify({
        'status': 'ok',
        'ffmpeg': ffmpeg_info
    })

@app.route("/__debug_storage")
def debug_storage():
    """Show storage information"""
    import os
    from .config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, LOG_DIR, DB_PATH, BRANDS_DIR
    
    def get_dir_info(path):
        try:
            if os.path.exists(path):
                size = sum(os.path.getsize(os.path.join(dirpath, filename)) 
                          for dirpath, dirnames, filenames in os.walk(path) 
                          for filename in filenames)
                count = sum(len(files) for _, _, files in os.walk(path))
                return {
                    'exists': True,
                    'size_bytes': size,
                    'file_count': count,
                    'writable': os.access(path, os.W_OK)
                }
            else:
                return {
                    'exists': False,
                    'size_bytes': 0,
                    'file_count': 0,
                    'writable': False
                }
        except Exception as e:
            return {
                'exists': os.path.exists(path),
                'error': str(e)
            }
    
    storage_info = {
        'upload_dir': get_dir_info(UPLOAD_DIR),
        'output_dir': get_dir_info(OUTPUT_DIR),
        'temp_dir': get_dir_info(TEMP_DIR),
        'log_dir': get_dir_info(LOG_DIR),
        'db_path': get_dir_info(os.path.dirname(DB_PATH)),
        'brands_dir': get_dir_info(BRANDS_DIR)
    }
    
    return jsonify({
        'status': 'ok',
        'storage': storage_info
    })

@app.route("/__debug_brands")
def debug_brands():
    """Show brand information"""
    from .brand_loader import get_available_brands
    import os
    
    portal_dir = os.path.dirname(os.path.abspath(__file__))
    brands = get_available_brands(portal_dir)
    
    brand_info = []
    for brand in brands:
        brand_info.append({
            'name': brand.get('name'),
            'display_name': brand.get('display_name'),
            'assets': brand.get('assets', {}),
            'options': brand.get('options', {})
        })
    
    return jsonify({
        'status': 'ok',
        'brands': brand_info,
        'count': len(brand_info)
    })

@app.route("/__debug_health")
def debug_health():
    """Health check endpoint"""
    import os
    from .config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR
    
    # Check if essential directories are writable
    dirs = [UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR]
    health_checks = {}
    
    for directory in dirs:
        try:
            test_file = os.path.join(directory, '.health_check')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            health_checks[directory] = 'OK'
        except Exception as e:
            health_checks[directory] = f'ERROR: {str(e)}'
    
    # Overall health
    all_healthy = all(status == 'OK' for status in health_checks.values())
    
    return jsonify({
        'status': 'healthy' if all_healthy else 'unhealthy',
        'checks': health_checks
    })

# Global conversion lock - only one FFmpeg process at a time (Render free tier 512MB RAM)
conversion_lock = threading.Lock()
conversion_in_progress = {'active': False, 'start_time': None}

# Job status dictionary for async watermark conversions
watermark_jobs = {}

# ============================================================================

@app.route('/')
def index():
    return jsonify({
        "message": "WTF Portal running",
        "status": "ok",
        "api_endpoints": [
            "POST /api/videos/process_brands",
            "POST /api/videos/fetch",
            "GET /api/videos/download/<filename>",
            "GET /api/brands/list",
            "POST /api/videos/convert-watermark",
            "GET /api/videos/convert-status/<job_id>",
            "GET /api/debug/brand-integrity",
            "GET /api/debug/build-filter/<brand_name>"
        ]
    })

@app.route('/api')
def api_root():
    """API root endpoint listing available API routes"""
    return jsonify({
        "status": "ok",
        "message": "WatchTheFall Portal API",
        "endpoints": [
            {
                "route": "/api/videos/process_brands",
                "method": "POST",
                "description": "Process video with selected brands"
            },
            {
                "route": "/api/videos/fetch",
                "method": "POST",
                "description": "Fetch video from URL"
            },
            {
                "route": "/api/videos/download/<filename>",
                "method": "GET",
                "description": "Download processed video"
            },
            {
                "route": "/api/brands/list",
                "method": "GET",
                "description": "List available brands"
            },
            {
                "route": "/api/videos/convert-watermark",
                "method": "POST",
                "description": "Convert video with watermark"
            },
            {
                "route": "/api/videos/convert-status/<job_id>",
                "method": "GET",
                "description": "Get conversion status"
            },
            {
                "route": "/api/debug/brand-integrity",
                "method": "GET",
                "description": "Check brand asset integrity"
            },
            {
                "route": "/api/debug/build-filter/<brand_name>",
                "method": "GET",
                "description": "Dry-run FFmpeg filter generation"
            }
        ]
    })

# ============================================================================
# FRONTEND ROUTES
# ============================================================================

@app.route('/portal/download')
@login_required
def download():
    """Downloader page - main entry point"""
    return render_template('downloader.html')

@app.route('/portal/brand')
@login_required
def brand_video():
    """Brand a video page"""
    try:
        return render_template('clean_dashboard.html')
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[BRANDR ERROR] Failed to render clean_dashboard.html: {error_trace}")
        return jsonify({
            'error': 'Failed to load Brandr page',
            'details': str(e),
            'trace': error_trace
        }), 500

@app.route('/portal/brands')
@login_required
def brands_page():
    """Brand management page"""
    return render_template('brands.html')

@app.route('/portal/profile')
@login_required
def profile_page():
    """User profile page"""
    from .database import get_db, get_all_brands
    from datetime import datetime
    
    user_id = session.get('user_id')
    email = session.get('email', 'User')
    
    # Get user info from database
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT created_at, tier FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    
    # Format created date
    created_at = 'Recently'
    if user and user['created_at']:
        try:
            created_dt = datetime.fromisoformat(user['created_at'])
            created_at = created_dt.strftime('%B %Y')
        except:
            pass
    
    # Get actual tier from database (or default to Explorer)
    tier = user['tier'] if user and user.get('tier') else 'Explorer'
    
    # Get actual brand count
    user_brands = get_all_brands(user_id=user_id, include_system=False)
    brand_configs = len(user_brands)
    
    # TODO: Get actual usage stats when usage tracking is implemented
    downloads_used = 0
    brands_used = 0
    
    # Tier limits
    tier_limits = {
        'Explorer': {'downloads': 50, 'brands': 50, 'configs': 1},
        'Creator': {'downloads': 500, 'brands': 500, 'configs': 5},
        'Studio': {'downloads': 'Unlimited', 'brands': 'Unlimited', 'configs': 'Unlimited'}
    }
    
    limits = tier_limits.get(tier, tier_limits['Explorer'])
    
    return render_template('profile.html',
        email=email,
        created_at=created_at,
        tier=tier,
        downloads_used=downloads_used,
        downloads_limit=limits['downloads'],
        brands_used=brands_used,
        brands_limit=limits['brands'],
        brand_configs=brand_configs,
        brand_configs_limit=limits['configs']
    )

@app.route('/portal/shipr')
@login_required
def shipr_page():
    """Coming soon page"""
    return render_template('shipr.html')

@app.route('/portal/test')
@login_required
def test_page():
    """Test page to verify portal is online"""
    return jsonify({
        'status': 'online',
        'message': 'WatchTheFall Portal is running',
        'endpoints': [
            '/portal/',
            '/api/videos/fetch',
            '/api/videos/download/<filename>',
            '/api/videos/convert-watermark',
            '/api/videos/convert-status/<job_id>',

        ]
    })

@app.route('/health')
def health_check():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy', 'message': 'WTF Studio is running'}), 200

@app.route('/portal/downloader_dashboard')
@login_required
def downloader_dashboard():
    return render_template("downloader_dashboard.html")

# ============================================================================
# API: VIDEO PROCESSING
# ============================================================================

@app.route('/api/videos/process_brands', methods=['POST'])
@login_required
def process_branded_videos():
    """Process video with selected brand overlays (brand_id-first)"""
    try:
        data = request.get_json(force=True) or {}
        url = data.get('url')
        
        # Accept brand_ids (NEW) or brands (DEPRECATED)
        brand_ids = data.get('brand_ids', [])
        selected_brands = data.get('brands', [])  # Deprecated
        
        if brand_ids:
            print(f"[PROCESS BRANDS] Using brand_ids: {brand_ids}")
        elif selected_brands:
            print(f"[PROCESS BRANDS] WARNING: Using deprecated 'brands' parameter (names). Migrate to 'brand_ids'.")
            print(f"[PROCESS BRANDS] Received brand names: {selected_brands}")
        else:
            print(f"[PROCESS BRANDS] ERROR: No brands selected")
            return jsonify({'success': False, 'error': 'brand_ids or brands is required'}), 400
        
        # Optional branding configuration overrides
        watermark_scale = data.get('watermark_scale', 1.15)
        watermark_opacity = data.get('watermark_opacity', 0.4)
        logo_scale = data.get('logo_scale', 0.15)
        logo_padding = data.get('logo_padding', 40)

        # NEW: accept source_path for local downloaded files
        source_path = data.get("source_path")
        if source_path and not url:
            url = source_path

        print(f"[PROCESS BRANDS] ========== NEW REQUEST ==========")
        print(f"[PROCESS BRANDS] Raw request data: {data}")
        print(f"[PROCESS BRANDS] URL: {url}")
        print(f"[PROCESS BRANDS] Brand IDs: {brand_ids}")
        print(f"[PROCESS BRANDS] Brand Names (deprecated): {selected_brands}")
        print(f"[PROCESS BRANDS] ========================================")

        if not url:
            print(f"[PROCESS BRANDS] ERROR: No URL provided")
            return jsonify({'success': False, 'error': 'URL or source_path is required'}), 400
        
        # Check if url is actually a local file path (doesn't start with http)
        if url.startswith('http'):
            # 1. Download the video from URL
            def download_video(url_input):
                try:
                    # Configure yt_dlp with enhanced fallback options for Instagram
                    ydl_opts = {
                        'outtmpl': os.path.join(RAW_DIR, '%(id)s.%(ext)s'),
                        'merge_output_format': 'mp4',
                        'format': 'bv*+ba/best',  # Better fallback for Instagram
                        'prefer_ffmpeg': True,
                        'retries': 5,
                        'fragment_retries': 5,
                        'socket_timeout': 300,
                        'http_headers': {
                            'User-Agent': 'Instagram 271.1.0.21.84 Android',
                            'X-IG-App-ID': '567067343352427',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        }
                    }
                    
                    # Only add cookiefile if the file exists and is readable
                    cookie_file = './portal/data/cookies.txt'
                    try:
                        if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
                            # Test if file is readable and has valid content
                            with open(cookie_file, 'r', encoding='utf-8') as f:
                                content = f.read().strip()
                                # Check if file has actual cookie data (not just comments)
                                # File is valid if it has content and either:
                                # 1. Doesn't start with the header (unlikely but possible), OR
                                # 2. Has more than one line (indicating actual cookie data beyond header)
                                # Additional check: look for actual cookie data patterns
                                has_cookie_data = False
                                if content:
                                    lines = content.split('\n')
                                    # Check if we have more than just header lines
                                    # Look for lines that contain actual cookie data (domain, flag, path, etc.)
                                    for line in lines:
                                        line = line.strip()
                                        # Skip empty lines and comments
                                        if line and not line.startswith('#'):
                                            # Check if this looks like a cookie line (has tab-separated values)
                                            if '\t' in line:
                                                has_cookie_data = True
                                                break
                            
                            if has_cookie_data:
                                ydl_opts['cookiefile'] = cookie_file
                                print(f"[PROCESS BRANDS] Using cookie file: {cookie_file}")
                            else:
                                print(f"[PROCESS BRANDS] Cookie file exists but appears to be empty or only contains header: {cookie_file}")
                        else:
                            print(f"[PROCESS BRANDS] Cookie file not found or not readable: {cookie_file}")
                    except Exception as cookie_error:
                        print(f"[PROCESS BRANDS] Warning: Could not use cookie file {cookie_file}: {cookie_error}")
                        # Continue without cookies
                    
                    with YoutubeDL(ydl_opts) as ydl:
                        print(f"[PROCESS BRANDS] Downloading: {url_input[:50]}...")
                        try:
                            info = ydl.extract_info(url_input, download=True)
                            filename = ydl.prepare_filename(info)
                        except Exception as download_error:
                            print(f"[PROCESS BRANDS ERROR] Download failed for {url_input}: {str(download_error)}")
                            import traceback
                            traceback.print_exc()
                            return {
                                'error': str(download_error),
                                'success': False,
                                'details': traceback.format_exc()
                            }
                    
                    # Ensure .mp4 extension
                    if not filename.endswith('.mp4'):
                        base, _ = os.path.splitext(filename)
                        filename = base + '.mp4'
                    
                    name = os.path.basename(filename)
                    file_exists = os.path.exists(filename)
                    file_size_mb = os.path.getsize(filename) / (1024 * 1024) if file_exists else 0
                    
                    if not file_exists or file_size_mb == 0:
                        print(f"[PROCESS BRANDS WARNING] File may not have downloaded properly: {filename} (exists: {file_exists}, size: {file_size_mb:.2f}MB)")
                        # Check if we have error information in the info dict
                        if info and 'error' in info:
                            print(f"[PROCESS BRANDS ERROR DETAIL] yt-dlp error: {info['error']}")
                        # Also check for other error fields
                        elif info and 'errors' in info:
                            print(f"[PROCESS BRANDS ERROR DETAIL] yt-dlp errors: {info['errors']}")
                    
                    print(f"[PROCESS BRANDS] Success: {name} ({file_size_mb:.2f}MB)")
                    return {
                        'filename': name,
                        'filepath': filename,
                        'size_mb': round(file_size_mb, 2),
                        'success': file_exists and file_size_mb > 0
                    }
                except Exception as e:
                    print(f"[PROCESS BRANDS ERROR] {url_input}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    # Try to get more detailed error information
                    error_details = str(e)
                    if hasattr(e, 'msg'):
                        error_details += f"; msg: {e.msg}"
                    if hasattr(e, 'reason'):
                        error_details += f"; reason: {e.reason}"
                    return {
                        'error': error_details,
                        'success': False,
                        'details': traceback.format_exc()
                    }
            # Download the video
            download_result = download_video(url)
            if not download_result.get('success'):
                return jsonify({
                    'success': False,
                    'error': 'Failed to download video',
                    'details': download_result.get('error')
                }), 500
            
            video_filepath = download_result['filepath']
            video_id = os.path.splitext(download_result['filename'])[0]
        else:
            # URL is actually a local file path
            print(f"[PROCESS BRANDS] Processing local file: {url}")
            
            # First check if it's in RAW_DIR
            video_filepath = os.path.join(RAW_DIR, url)
            
            # If not in the new location, check the old OUTPUT_DIR
            if not os.path.exists(video_filepath):
                video_filepath = os.path.join(OUTPUT_DIR, url)
            
            video_id = os.path.splitext(url)[0]
            
            # Check if file exists
            if not os.path.exists(video_filepath):
                return jsonify({
                    'success': False,
                    'error': f'File not found: {video_filepath}'
                }), 404
            
            print(f"[PROCESS BRANDS] Found local file: {video_filepath}")
        
        # 2. Load and validate brands (brand_id-first with backward compat)
        from .database import get_brand, get_all_brands
        from .config import STORAGE_ROOT
        user_id = session.get('user_id')
        
        # Resolve brands: prioritize brand_ids, fallback to names
        resolved_brands = []
        
        if brand_ids:
            # NEW: Resolve by brand_id (secure, immutable)
            print(f"[PROCESS BRANDS] Resolving {len(brand_ids)} brands by ID")
            for brand_id in brand_ids:
                db_brand = get_brand(brand_id=brand_id, user_id=user_id)
                if db_brand:
                    print(f"[PROCESS BRANDS] ✓ Brand #{brand_id}: {db_brand.get('display_name')}")
                    resolved_brands.append(db_brand)
                else:
                    print(f"[PROCESS BRANDS] ✗ Brand #{brand_id} not found or not owned by user #{user_id}")
                    return jsonify({
                        'success': False,
                        'code': 'BRAND_VALIDATION_FAILED',
                        'error': f'Brand #{brand_id} not found or access denied',
                        'brand_id': brand_id,
                        'fix': 'Brand may have been deleted. Refresh brand list.',
                        'fix_url': '/portal/brands'
                    }), 404
        elif selected_brands:
            # DEPRECATED: Resolve by name (for backward compat only)
            print(f"[PROCESS BRANDS] Resolving {len(selected_brands)} brands by NAME (deprecated)")
            all_user_brands = get_all_brands(user_id=user_id, include_system=False)
            for brand_name in selected_brands:
                db_brand = next((b for b in all_user_brands if b['name'] == brand_name), None)
                if db_brand:
                    print(f"[PROCESS BRANDS] ✓ Brand '{brand_name}': #{db_brand['id']}")
                    resolved_brands.append(db_brand)
                else:
                    print(f"[PROCESS BRANDS] ✗ Brand '{brand_name}' not found for user #{user_id}")
                    return jsonify({
                        'success': False,
                        'code': 'BRAND_VALIDATION_FAILED',
                        'error': f"Brand '{brand_name}' not found",
                        'brand': brand_name,
                        'fix': 'Recreate this brand in Manage Brands',
                        'fix_url': '/portal/brands'
                    }), 404
        
        # Validate brand readiness with HARD FAIL (no defaults)
        # resolved_brands are already fetched from DB with ownership verified
        validation_errors = []
        
        print(f"[PROCESS BRANDS] Validating {len(resolved_brands)} brands")
        
        for db_brand in resolved_brands:
            brand_id = db_brand.get('id')
            brand_name = db_brand.get('display_name') or db_brand.get('name')
            print(f"[PROCESS BRANDS] Validating Brand #{brand_id} ({brand_name})")
            
            # Check is_ready flag first
            if not db_brand.get('is_ready'):
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Brand is incomplete',
                    'fix': 'Upload logo or watermark in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            # Validate watermark exists (required for ALL brands)
            wm_path = db_brand.get('watermark_path') or db_brand.get('watermark_vertical')
            if not wm_path:
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Watermark missing',
                    'fix': 'Upload watermark in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            # Validate watermark file exists on disk
            wm_full_path = os.path.join(STORAGE_ROOT, wm_path)
            if not os.path.exists(wm_full_path):
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': f'Watermark file not found on disk: {wm_path}',
                    'fix': 'Re-upload watermark in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            # Validate logo if logo overlay is being used
            logo_path = db_brand.get('logo_path')
            logo_scale = db_brand.get('logo_scale', 0)
            
            # If logo is configured (non-zero scale) but path missing → fail
            if logo_scale > 0 and not logo_path:
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Logo overlay enabled but logo file missing',
                    'fix': 'Upload logo in Manage Brands or set logo scale to 0',
                    'fix_url': '/portal/brands'
                })
                continue
            
            # If logo path exists, validate file on disk
            if logo_path:
                logo_full_path = os.path.join(STORAGE_ROOT, logo_path)
                if not os.path.exists(logo_full_path):
                    validation_errors.append({
                        'brand_id': brand_id,
                        'brand': brand_name,
                        'error': f'Logo file not found on disk: {logo_path}',
                        'fix': 'Re-upload logo in Manage Brands',
                        'fix_url': '/portal/brands'
                    })
                    continue
            
            # Validate critical config values
            wm_scale = db_brand.get('wm_scale')
            wm_opacity = db_brand.get('wm_opacity')
            
            if wm_scale is None or wm_scale <= 0:
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Invalid watermark scale (must be > 0)',
                    'fix': 'Edit brand settings in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            if wm_opacity is None or wm_opacity < 0 or wm_opacity > 1:
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Invalid watermark opacity (must be 0-1)',
                    'fix': 'Edit brand settings in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            print(f"[PROCESS BRANDS] ✓ Brand #{brand_id} ({brand_name}) validation passed")
        
        # Hard fail if any validation errors
        if validation_errors:
            first_error = validation_errors[0]
            print(f"[PROCESS BRANDS] Validation failed: {validation_errors}")
            return jsonify({
                'success': False,
                'error': f"{first_error['brand']}: {first_error['error']}",
                'code': 'BRAND_VALIDATION_FAILED',
                'brand': first_error['brand'],
                'brand_id': first_error.get('brand_id'),
                'fix': first_error['fix'],
                'fix_url': first_error.get('fix_url'),
                'all_errors': validation_errors
            }), 400
        
        print(f"[PROCESS BRANDS] Processing {len(resolved_brands)} brands sequentially")
        
        # Normalize video timestamps to fix corrupted Instagram videos
        print(f"[PROCESS BRANDS] Normalizing video timestamps: {video_filepath}")
        normalized_video_path = normalize_video(video_filepath)
        print(f"[PROCESS BRANDS] Using normalized video: {normalized_video_path}")
        
        # 3. Process video with selected brands ONE AT A TIME
        processor = VideoProcessor(normalized_video_path, OUTPUT_DIR)
        
        output_paths = []
        
        total_brands = len(resolved_brands)
        for i, db_brand in enumerate(resolved_brands, 1):
            brand_id = db_brand.get('id')
            brand_name = db_brand.get('display_name') or db_brand.get('name')
            print(f"[PROCESS BRANDS] PROCESSING BRAND {i} of {total_brands}: #{brand_id} ({brand_name})")
            
            # Use DB brand config (resolved_brands are already DB records)
            print(f"[PROCESS BRANDS] Using brand config from database (brand_id: {brand_id})")
            merged_config = db_brand
            
            try:
                output_path = processor.process_brand(merged_config, video_id=video_id)
                output_paths.append(output_path)
                print(f"[PROCESS BRANDS] FINISHED BRAND {i}: {brand_name}")
            except Exception as e:
                error_message = str(e)
                if "audio-only" in error_message or "no valid video stream" in error_message:
                    # Handle audio-only video error specifically
                    print(f"[PROCESS BRANDS] AUDIO-ONLY VIDEO DETECTED FOR BRAND {i}: {brand_name}")
                    return jsonify({
                        'success': False,
                        'error': 'The downloaded file contains no video stream (audio-only). Instagram served audio-only content. Try again or use a different video.',
                        'details': error_message
                    }), 400
                else:
                    print(f"[PROCESS BRANDS] FAILED BRAND {i}: {brand_name} - {str(e)}")
                    import traceback
                    traceback.print_exc()
        
        print(f"[PROCESS BRANDS] ALL BRANDS COMPLETED: {len(output_paths)} successful")
        
        # 4. Generate download URLs
        download_urls = []
        for output_path in output_paths:
            filename = os.path.basename(output_path)
            # Extract brand name from filename (format: {video_id}_{brand_name}.mp4)
            # Split by underscore and take the last part before .mp4
            name_parts = filename.replace('.mp4', '').split('_')
            brand_name = name_parts[-1] if len(name_parts) > 1 else 'unknown'
            download_urls.append({
                'brand': brand_name,
                'filename': filename,
                'download_url': f'/api/videos/download/{filename}'
            })
        
        # Clean up original video (only if we downloaded it, not if it was local)
        if url.startswith('http'):
            try:
                os.remove(video_filepath)
            except Exception as e:
                print(f"[PROCESS BRANDS] Warning: Could not remove original video: {e}")
        
        print(f"[PROCESS BRANDS] ========== RESPONSE ==========")
        print(f"[PROCESS BRANDS] Success: True")
        print(f"[PROCESS BRANDS] Output count: {len(output_paths)}")
        print(f"[PROCESS BRANDS] Output files:")
        for i, dl in enumerate(download_urls, 1):
            print(f"[PROCESS BRANDS]   {i}. {dl['filename']} (brand: {dl['brand']})")
        print(f"[PROCESS BRANDS] ========================================")
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed video for {len(output_paths)} brands',
            'outputs': download_urls
        })
        
    except Exception as e:
        import traceback
        print(f"[PROCESS BRANDS EXCEPTION]:")
        traceback.print_exc()
        log_event('error', None, f'Brand processing failed: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/videos/fetch', methods=['POST'])
@login_required
def fetch_videos_from_urls():
    """Download videos from URLs (TikTok, Instagram, X) - up to 5 at a time"""
    try:
        if not YoutubeDL:
            return jsonify({'success': False, 'error': 'yt-dlp not installed'}), 500
        
        data = request.get_json(force=True) or {}
        urls = data.get('urls') or []
        
        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({'success': False, 'error': 'Provide JSON: {"urls": ["url1", "url2", ...]}'}), 400
        
        if len(urls) > 5:
            return jsonify({'success': False, 'error': 'Maximum 5 URLs at a time (Render free tier limit)'}), 400
        
        print(f"[FETCH] Downloading {len(urls)} videos from URLs")
        log_event('info', None, f'Fetching {len(urls)} URLs')
        
        def download_one(url_input):
            try:
                # Configure yt_dlp with enhanced fallback options for Instagram
                ydl_opts = {
                    'outtmpl': os.path.join(RAW_DIR, '%(id)s.%(ext)s'),
                    'merge_output_format': 'mp4',
                    'format': 'bv*+ba/best',  # Better fallback for Instagram
                    'prefer_ffmpeg': True,
                    'retries': 5,
                    'fragment_retries': 5,
                    'socket_timeout': 300,
                    'http_headers': {
                        'User-Agent': 'Instagram 271.1.0.21.84 Android',
                        'X-IG-App-ID': '567067343352427',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Accept-Encoding': 'gzip, deflate',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                }
                
                # Only add cookiefile if the file exists and is readable
                cookie_file = './portal/data/cookies.txt'
                try:
                    if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
                        # Test if file is readable and has valid content
                        with open(cookie_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            # Check if file has actual cookie data (not just comments)
                            # File is valid if it has content and either:
                            # 1. Doesn't start with the header (unlikely but possible), OR
                            # 2. Has more than one line (indicating actual cookie data beyond header)
                            # Additional check: look for actual cookie data patterns
                            has_cookie_data = False
                            if content:
                                lines = content.split('\n')
                                # Check if we have more than just header lines
                                # Look for lines that contain actual cookie data (domain, flag, path, etc.)
                                for line in lines:
                                    line = line.strip()
                                    # Skip empty lines and comments
                                    if line and not line.startswith('#'):
                                        # Check if this looks like a cookie line (has tab-separated values)
                                        if '\t' in line:
                                            has_cookie_data = True
                                            break
                            
                            if has_cookie_data:
                                ydl_opts['cookiefile'] = cookie_file
                                print(f"[FETCH] Using cookie file: {cookie_file}")
                            else:
                                print(f"[FETCH] Cookie file exists but appears to be empty or only contains header: {cookie_file}")
                    else:
                        print(f"[FETCH] Cookie file not found or not readable: {cookie_file}")
                except Exception as cookie_error:
                    print(f"[FETCH] Warning: Could not use cookie file {cookie_file}: {cookie_error}")
                    # Continue without cookies
                
                with YoutubeDL(ydl_opts) as ydl:
                    print(f"[FETCH] Downloading: {url_input[:50]}...")
                    try:
                        info = ydl.extract_info(url_input, download=True)
                        filename = ydl.prepare_filename(info)
                    except Exception as download_error:
                        print(f"[FETCH ERROR] Download failed for {url_input}: {str(download_error)}")
                        import traceback
                        traceback.print_exc()
                        return {
                            'url': url_input,
                            'error': str(download_error),
                            'success': False,
                            'details': traceback.format_exc()
                        }
                
                # Ensure .mp4 extension
                if not filename.endswith('.mp4'):
                    base, _ = os.path.splitext(filename)
                    filename = base + '.mp4'
                
                # Check if downloaded file has valid video stream, if not try fallback
                if not ensure_video_stream(filename):
                    print(f"[FETCH] No valid video stream found in {filename}, attempting fallback extraction...")
                    # fallback extraction using yt-dlp (bundled)
                    fixed_path = filename.replace(".mp4", "_fixed.mp4")
                    ytdlp_cmd = [
                        "yt-dlp",
                        url_input,
                        "-f", "mp4",
                        "-o", fixed_path
                    ]
                    subprocess.run(ytdlp_cmd)
                    if os.path.exists(fixed_path):
                        filename = fixed_path
                        print(f"[FETCH] Fallback extraction successful: {filename}")
                    else:
                        print(f"[FETCH] Fallback extraction failed for {url_input}")
                
                name = os.path.basename(filename)
                file_exists = os.path.exists(filename)
                file_size_mb = os.path.getsize(filename) / (1024 * 1024) if file_exists else 0                
                if not file_exists or file_size_mb == 0:
                    print(f"[FETCH WARNING] File may not have downloaded properly: {filename} (exists: {file_exists}, size: {file_size_mb:.2f}MB)")
                    # Check if we have error information in the info dict
                    if info and 'error' in info:
                        print(f"[FETCH ERROR DETAIL] yt-dlp error: {info['error']}")
                    # Also check for other error fields
                    elif info and 'errors' in info:
                        print(f"[FETCH ERROR DETAIL] yt-dlp errors: {info['errors']}")
                
                print(f"[FETCH] Success: {name} ({file_size_mb:.2f}MB)")
                return {
                    'url': url_input,
                    'filename': name,
                    'local_path': filename,  # Return full path
                    'download_url': f'/api/videos/download/{name}',
                    'size_mb': round(file_size_mb, 2),
                    'success': file_exists and file_size_mb > 0
                }
            except Exception as e:
                print(f"[FETCH ERROR] {url_input}: {str(e)}")
                import traceback
                traceback.print_exc()
            # Try to get more detailed error information
            error_details = str(e)
            if hasattr(e, 'msg'):
                error_details += f"; msg: {e.msg}"
            if hasattr(e, 'reason'):
                error_details += f"; reason: {e.reason}"
            return {
                'url': url_input,
                'error': error_details,
                'success': False,
                'details': traceback.format_exc()
            }
        
        # Download sequentially to keep memory low
        results = []
        for url in urls:
            results.append(download_one(url))
        
        success_count = sum(1 for r in results if r.get('success'))
        log_event('info', None, f'Fetch complete: {success_count}/{len(urls)} successful')
        
        return jsonify({
            'success': True,
            'total': len(urls),
            'successful': success_count,
            'results': results
        })
        
    except Exception as e:
        import traceback
        print(f"[FETCH EXCEPTION]:")
        traceback.print_exc()
        log_event('error', None, f'Fetch failed: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

# Process endpoint removed - using client-side Canvas watermarking only

# Status endpoint removed - no server-side job queue

@app.route('/api/videos/download/<filename>', methods=['GET'])
@login_required
def download_video(filename):
    """Download processed video"""
    try:
        # Sanitize filename to prevent path traversal
        filename = os.path.basename(filename)
        
        # First check RAW_DIR
        filepath = os.path.join(RAW_DIR, filename)
        
        # If not in the new location, check the old OUTPUT_DIR
        if not os.path.exists(filepath):
            filepath = os.path.join(OUTPUT_DIR, filename)
        
        print(f"[DEBUG] Looking for file at: {filepath}")
        
        # Check if file exists
        if not os.path.exists(filepath):
            print(f"[DOWNLOAD ERROR] File not found: {filepath}")
            # List contents of both directories for debugging
            try:
                if os.path.exists(RAW_DIR):
                    raw_contents = os.listdir(RAW_DIR)
                    print(f"[DOWNLOAD DEBUG] Contents of RAW_DIR ({RAW_DIR}): {raw_contents[:10]}...")  # First 10 files
                else:
                    print(f"[DOWNLOAD DEBUG] RAW_DIR does not exist: {RAW_DIR}")
                
                if os.path.exists(OUTPUT_DIR):
                    output_contents = os.listdir(OUTPUT_DIR)
                    print(f"[DOWNLOAD DEBUG] Contents of OUTPUT_DIR ({OUTPUT_DIR}): {output_contents[:10]}...")  # First 10 files
                else:
                    print(f"[DOWNLOAD DEBUG] OUTPUT_DIR does not exist: {OUTPUT_DIR}")
            except Exception as e:
                print(f"[DOWNLOAD DEBUG] Could not list contents of directories: {e}")
            return jsonify({'error': 'File not found', 'path': filepath, 'filename': filename}), 404
        
        file_size = os.path.getsize(filepath)
        print(f"[DOWNLOAD] Serving file: {filename} ({file_size} bytes)")
        
        # Send file with proper headers for downloads folder
        response = send_from_directory(os.path.dirname(filepath), filename, as_attachment=True)
        
        # Add Content-Disposition header to suggest Downloads folder
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Mobile-friendly headers
        response.headers['Content-Type'] = 'video/mp4'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        print(f"[DOWNLOAD] Headers set for {filename}")
        return response
    except Exception as e:
        print(f"[DOWNLOAD EXCEPTION] {filename}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'File not found', 'details': str(e), 'filename': filename, 'filepath': filepath}), 404

# Recent videos endpoint removed - using localStorage history only

# ============================================================================
# API: PREVIEW - Frame extraction and asset serving for canvas preview
# ============================================================================

@app.route('/api/preview/extract-frame', methods=['POST'])
def extract_frame():
    """Extract first frame from video for canvas preview"""
    import base64
    from .config import FFMPEG_BIN, FFPROBE_BIN
    
    try:
        data = request.get_json(force=True) or {}
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided'}), 400
        
        # Sanitize filename to prevent path traversal
        filename = os.path.basename(filename)
        
        # Find the video file - check RAW_DIR first, then OUTPUT_DIR
        video_path = os.path.join(RAW_DIR, filename)
        if not os.path.exists(video_path):
            video_path = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(video_path):
            return jsonify({'success': False, 'error': f'File not found: {filename}'}), 404
        
        # Get video dimensions with ffprobe
        probe_cmd = [
            FFPROBE_BIN, '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams', video_path
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)
        
        width, height = 720, 1280  # Default
        for stream in probe_data.get('streams', []):
            if stream.get('codec_type') == 'video':
                width = stream.get('width', 720)
                height = stream.get('height', 1280)
                break
        
        # Extract first frame as JPEG (small, fast)
        temp_frame = os.path.join(tempfile.gettempdir(), f'frame_{uuid.uuid4().hex}.jpg')
        
        extract_cmd = [
            FFMPEG_BIN, '-y',
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '5',  # Quality 2-31 (lower = better, 5 is good balance)
            temp_frame
        ]
        
        subprocess.run(extract_cmd, capture_output=True)
        
        if not os.path.exists(temp_frame):
            return jsonify({'success': False, 'error': 'Failed to extract frame'}), 500
        
        # Read frame and encode as base64
        with open(temp_frame, 'rb') as f:
            frame_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Clean up temp file
        os.remove(temp_frame)
        
        return jsonify({
            'success': True,
            'frame_data': f'data:image/jpeg;base64,{frame_data}',
            'width': width,
            'height': height,
            'aspect_ratio': width / height
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/preview/watermark/<brand_name>')
def get_watermark_preview(brand_name):
    """Serve watermark PNG for canvas preview"""
    from .video_processor import VideoProcessor
    
    # Get orientation from query param (default Vertical_HD)
    orientation = request.args.get('orientation', 'Vertical_HD')
    
    # Clean brand name
    clean_brand = brand_name.replace('WTF', '').strip()
    
    # Build watermark path
    watermark_dir = os.path.join(VideoProcessor.WATERMARKS_DIR, orientation)
    
    patterns = [
        f"{clean_brand}_watermark.png",
        f"{clean_brand.lower()}_watermark.png",
        f"{clean_brand.capitalize()}_watermark.png",
        f"{brand_name}_watermark.png",
    ]
    
    for pattern in patterns:
        path = os.path.join(watermark_dir, pattern)
        if os.path.exists(path):
            return send_from_directory(os.path.dirname(path), os.path.basename(path))
    
    return jsonify({'error': 'Watermark not found', 'brand': brand_name}), 404


@app.route('/api/preview/logo/<brand_name>')
def get_logo_preview(brand_name):
    """Serve logo PNG for canvas preview"""
    from .video_processor import VideoProcessor
    
    logo_filename = f"{brand_name}_logo.png"
    logo_path = os.path.join(VideoProcessor.LOGOS_DIR, logo_filename)
    
    if os.path.exists(logo_path):
        return send_from_directory(os.path.dirname(logo_path), logo_filename)
    
    return jsonify({'error': 'Logo not found', 'brand': brand_name}), 404


@app.route('/api/preview/brand-asset/<int:brand_id>/<asset_type>')
@login_required
def get_brand_asset_preview(brand_id, asset_type):
    """
    Serve normalized brand assets (logo/watermark) for UI previews
    Secure: only serves assets owned by logged-in user
    Uses brand_id (NOT name) for lookup
    """
    try:
        from .database import get_brand
        from .config import STORAGE_ROOT
        
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Get brand by ID with ownership verification
        brand = get_brand(brand_id=brand_id, user_id=user_id)
        
        if not brand:
            print(f"[PREVIEW] Brand #{brand_id} not found or not owned by user #{user_id}")
            return jsonify({'error': 'Brand not found or access denied'}), 404
        
        # Determine which asset to serve
        if asset_type == 'logo':
            asset_path = brand.get('logo_path')
        elif asset_type == 'watermark':
            asset_path = brand.get('watermark_path') or brand.get('watermark_vertical')
        else:
            return jsonify({'error': 'Invalid asset type'}), 400
        
        if not asset_path:
            return jsonify({'error': f'{asset_type.capitalize()} not found'}), 404
        
        # Construct full path from storage root
        full_path = os.path.join(STORAGE_ROOT, asset_path)
        
        # Security: prevent path traversal
        full_path = os.path.abspath(full_path)
        if not full_path.startswith(os.path.abspath(STORAGE_ROOT)):
            return jsonify({'error': 'Invalid path'}), 403
        
        if not os.path.exists(full_path):
            return jsonify({'error': f'{asset_type.capitalize()} file not found on disk'}), 404
        
        # Serve the file
        return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))
        
    except Exception as e:
        import traceback
        print(f"[PREVIEW ERROR] {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# API: BRANDS (Static JSON)
# ============================================================================

@app.route('/api/brands/list', methods=['GET'])
@login_required
def list_brands():
    """Get list of user-owned brands with COMPLETE config (brand_id-first)"""
    try:
        from .database import get_all_brands
        
        user_id = session.get('user_id')
        
        # Get only user-owned brands (exclude system brands)
        brands = get_all_brands(user_id=user_id, include_system=False)
        
        # Format for frontend with ALL config fields (brand_id-first)
        brand_list = [{
            # Core Identity (brand_id is primary key)
            'id': brand['id'],
            'name': brand['name'],  # Keep for backward compat only
            'display_name': brand.get('display_name', brand['name']),
            'user_id': brand['user_id'],
            'is_ready': brand.get('is_ready', False),
            'is_system': brand.get('is_system', False),
            
            # Asset Paths
            'logo_path': brand.get('logo_path'),
            'watermark_path': brand.get('watermark_path') or brand.get('watermark_vertical'),
            'watermark_vertical': brand.get('watermark_vertical'),
            'watermark_square': brand.get('watermark_square'),
            'watermark_landscape': brand.get('watermark_landscape'),
            
            # Watermark Config (wm_* keys are canonical)
            'wm_mode': brand.get('wm_mode', 'fullscreen'),
            'wm_scale': brand.get('wm_scale', 1.0),
            'wm_opacity': brand.get('wm_opacity', 0.10),
            'wm_x': brand.get('wm_x', 0.5),
            'wm_y': brand.get('wm_y', 0.5),
            
            # Logo Config
            'logo_scale': brand.get('logo_scale', 0.15),
            'logo_opacity': brand.get('logo_opacity', 1.0),
            'logo_x': brand.get('logo_x', 0.85),
            'logo_y': brand.get('logo_y', 0.85),
            'logo_padding': brand.get('logo_padding', 40),
            'logo_shape': brand.get('logo_shape'),
            
            # Legacy fields (for backward compat with old code)
            'watermark_scale': brand.get('watermark_scale', brand.get('wm_scale', 1.15)),
            'watermark_opacity': brand.get('watermark_opacity', brand.get('wm_opacity', 0.4)),
            
            # Text Layer Config
            'text_enabled': brand.get('text_enabled', False),
            'text_content': brand.get('text_content', ''),
            'text_x': brand.get('text_x', 0),
            'text_y': brand.get('text_y', 0),
            'text_size': brand.get('text_size', 48),
            'text_color': brand.get('text_color', '#FFFFFF'),
            'text_bg_enabled': brand.get('text_bg_enabled', True),
            'text_bg_opacity': brand.get('text_bg_opacity', 0.6)
        } for brand in brands]
        
        return jsonify({
            'success': True,
            'brands': brand_list
        })
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] List brands: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/brands/<brand_name>/config', methods=['GET'])
def get_brand_config_api(brand_name):
    """Get saved configuration for a brand"""
    try:
        from .database import get_brand_config
        config = get_brand_config(brand_name)
        return jsonify({
            'success': True,
            'config': config
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/brands/<brand_name>/config', methods=['POST'])
@login_required
def save_brand_config_api(brand_name):
    """Save configuration for a brand"""
    try:
        from .database import save_brand_config
        data = request.get_json(force=True) or {}
        
        # Build config object from request
        config = {
            'watermark_scale': float(data.get('watermark_scale', 1.15)),
            'watermark_opacity': float(data.get('watermark_opacity', 0.4)),
            'logo_scale': float(data.get('logo_scale', 0.15)),
            'logo_padding': int(data.get('logo_padding', 40)),
            'text_enabled': bool(data.get('text_enabled', False)),
            'text_content': str(data.get('text_content', '')),
            'text_position': str(data.get('text_position', 'bottom')),
            'text_size': int(data.get('text_size', 48)),
            'text_color': str(data.get('text_color', '#FFFFFF')),
            'text_font': str(data.get('text_font', 'Arial')),
            'text_bg_enabled': bool(data.get('text_bg_enabled', True)),
            'text_bg_color': str(data.get('text_bg_color', '#000000')),
            'text_bg_opacity': float(data.get('text_bg_opacity', 0.6)),
            'text_margin': int(data.get('text_margin', 40))
        }
        
        save_brand_config(brand_name, config)
        
        print(f"[BRAND CONFIG] Saved config for {brand_name}: scale={config['watermark_scale']}, text={config['text_enabled']}")
        
        return jsonify({
            'success': True,
            'message': f'Configuration saved for {brand_name}',
            'config': config
        })
    except Exception as e:
        import traceback
        print(f"[BRAND CONFIG ERROR] {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ============================================================================
# API: BRANDS CRUD (Unified brand management)
# ============================================================================

@app.route('/api/brands', methods=['GET'])
@login_required
def get_all_brands_api():
    """Get user-owned brands ONLY (no system brands for SaaS)"""
    try:
        from .database import get_all_brands
        
        # IMPORTANT: user_id comes from session, NOT from query params (security)
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        # Parse include_system from query (default false for SaaS)
        include_system = request.args.get('include_system', 'false').lower() == 'true'
        
        # Get user's brands (exclude system brands by default)
        brands = get_all_brands(user_id=user_id, include_system=include_system)
        
        return jsonify({
            'success': True,
            'brands': brands,
            'count': len(brands)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands/<int:brand_id>', methods=['GET'])
@login_required
def get_single_brand_api(brand_id):
    """Get a single brand by ID"""
    try:
        from .database import get_brand
        brand = get_brand(brand_id=brand_id)
        
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        return jsonify({
            'success': True,
            'brand': brand
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands', methods=['POST'])
@login_required
def create_brand_api():
    """Create a new brand"""
    try:
        from .database import create_brand
        data = request.get_json(force=True) or {}
        
        # Required fields
        name = data.get('name')
        display_name = data.get('display_name', name)
        
        if not name:
            return jsonify({'success': False, 'error': 'name is required'}), 400
        
        # IMPORTANT: user_id comes from session, NOT from request (security)
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        print(f"[BRANDS] Creating brand '{name}' for user #{user_id}")
        
        # Create brand for logged-in user
        brand_id = create_brand(
            name=name,
            display_name=display_name,
            user_id=user_id,  # Use session user_id, not request data
            is_system=False,  # Never allow users to create system brands
            is_locked=False,
            watermark_vertical=data.get('watermark_vertical'),
            watermark_square=data.get('watermark_square'),
            watermark_landscape=data.get('watermark_landscape'),
            logo_path=data.get('logo_path'),
            watermark_scale=data.get('watermark_scale', 1.15),
            watermark_opacity=data.get('watermark_opacity', 0.4),
            logo_scale=data.get('logo_scale', 0.15),
            logo_padding=data.get('logo_padding', 40),
            text_enabled=data.get('text_enabled', False),
            text_content=data.get('text_content', ''),
            text_position=data.get('text_position', 'bottom'),
            text_x=data.get('text_x', 0),
            text_y=data.get('text_y', 0),
            text_size=data.get('text_size', 48),
            text_color=data.get('text_color', '#FFFFFF'),
            text_font=data.get('text_font', 'Arial'),
            text_bg_enabled=data.get('text_bg_enabled', True),
            text_bg_color=data.get('text_bg_color', '#000000'),
            text_bg_opacity=data.get('text_bg_opacity', 0.6),
            text_margin=data.get('text_margin', 40),
            # Visual positioning fields
            logo_x=data.get('logo_x', 0.85),
            logo_y=data.get('logo_y', 0.85),
            logo_opacity=data.get('logo_opacity', 1.0),
            wm_mode=data.get('wm_mode', 'fullscreen'),
            wm_x=data.get('wm_x', 0.5),
            wm_y=data.get('wm_y', 0.5),
            wm_scale=data.get('wm_scale', 1.0),
            wm_opacity=data.get('wm_opacity', 0.10),
            text_x_percent=data.get('text_x_percent', 0.5),
            text_y_percent=data.get('text_y_percent', 0.2)
        )
        
        print(f"[BRANDS] Created brand: {name} (id={brand_id})")
        
        return jsonify({
            'success': True,
            'brand_id': brand_id,
            'message': f'Brand {name} created'
        }), 201
    except sqlite3.IntegrityError as e:
        if 'UNIQUE constraint failed' in str(e):
            print(f"[BRANDS ERROR] Brand name already exists: {e}")
            return jsonify({
                'success': False, 
                'error': 'A brand with this name already exists for your account.',
                'code': 'DUPLICATE_NAME'
            }), 409
        else:
            raise
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower():
            print(f"[BRANDS ERROR] DATABASE LOCKED while creating brand: {e}")
            print(f"[BRANDS ERROR] This indicates SQLite contention. Check WAL mode and busy_timeout settings.")
            return jsonify({
                'success': False, 
                'error': 'Database is locked. Please try again in a moment.',
                'code': 'DATABASE_LOCKED'
            }), 503
        else:
            raise
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] Create: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands/<int:brand_id>', methods=['PUT'])
@login_required
def update_brand_api(brand_id):
    """Update a brand's settings and/or assets"""
    try:
        from .database import get_brand, update_brand
        
        # Check brand exists
        brand = get_brand(brand_id=brand_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        # Check if locked (system templates)
        if brand['is_locked']:
            return jsonify({'success': False, 'error': 'This brand is locked and cannot be modified'}), 403
        
        data = request.get_json(force=True) or {}
        
        print(f"[BRANDS] Updating brand #{brand_id} ({brand['name']}) with fields: {list(data.keys())}")
        
        # Update brand
        update_brand(brand_id, **data)
        
        print(f"[BRANDS] Updated brand: {brand['name']} (id={brand_id})")
        
        return jsonify({
            'success': True,
            'message': f'Brand {brand["name"]} updated'
        })
    except sqlite3.OperationalError as e:
        if 'locked' in str(e).lower():
            print(f"[BRANDS ERROR] DATABASE LOCKED while updating brand #{brand_id}: {e}")
            print(f"[BRANDS ERROR] This indicates SQLite contention. Check WAL mode and busy_timeout settings.")
            return jsonify({
                'success': False, 
                'error': 'Database is locked. Please try again in a moment.',
                'code': 'DATABASE_LOCKED'
            }), 503
        else:
            raise
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] Update: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands/<int:brand_id>', methods=['DELETE'])
@login_required
def delete_brand_api(brand_id):
    """Soft delete a brand (set is_active = 0)"""
    try:
        from .database import get_brand, delete_brand
        
        # Check brand exists
        brand = get_brand(brand_id=brand_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        # Check if system brand
        if brand['is_system']:
            return jsonify({'success': False, 'error': 'System brands cannot be deleted'}), 403
        
        delete_brand(brand_id)
        
        print(f"[BRANDS] Deleted brand: {brand['name']} (id={brand_id})")
        
        return jsonify({
            'success': True,
            'message': f'Brand {brand["name"]} deleted'
        })
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] Delete: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands/<int:brand_id>/upload_logo', methods=['POST'])
@login_required
def upload_brand_logo(brand_id):
    """Upload logo for a brand"""
    try:
        from .database import get_brand, update_brand
        from .config import BRANDS_DIR, STORAGE_ROOT
        from werkzeug.utils import secure_filename
        
        user_id = session.get('user_id')
        
        # Check brand exists and belongs to user
        brand = get_brand(brand_id=brand_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        if brand['user_id'] != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Check file uploaded
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        # Validate file type
        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if ext not in allowed_extensions:
            return jsonify({'success': False, 'error': f'Invalid file type. Allowed: {allowed_extensions}'}), 400
        
        # Create brand directory: /var/data/storage/brands/{user_id}/{brand_id}/
        brand_dir = os.path.join(BRANDS_DIR, str(user_id), str(brand_id))
        os.makedirs(brand_dir, exist_ok=True)
        
        # Save original file first
        original_filename = f'logo_original.{ext}'
        original_path = os.path.join(brand_dir, original_filename)
        file.save(original_path)
        
        # Normalize image: convert to PNG, resize, clean alpha
        from .image_utils import normalize_logo
        normalized_filename = 'logo_normalized.png'
        normalized_path = os.path.join(brand_dir, normalized_filename)
        
        # Get background removal preferences from request
        remove_bg = request.form.get('remove_bg')  # 'dark', 'light', or None
        bg_threshold = int(request.form.get('bg_threshold', 30))  # 0-255
        
        norm_result = normalize_logo(
            original_path, 
            normalized_path,
            max_dimension=1024,
            remove_bg=remove_bg,
            bg_threshold=bg_threshold
        )
        
        if not norm_result['success']:
            return jsonify({
                'success': False, 
                'error': f'Failed to normalize image: {norm_result.get("error")}'
            }), 500
        
        # Store relative path of NORMALIZED version (this is what VideoProcessor will use)
        relative_path = os.path.relpath(normalized_path, STORAGE_ROOT)
        
        # Update database
        update_brand(brand_id, logo_path=relative_path)
        
        print(f"[BRANDS] Uploaded & normalized logo for brand {brand_id}: {relative_path}")
        print(f"[BRANDS] Original: {norm_result.get('original_format')} {norm_result.get('original_size')}")
        print(f"[BRANDS] Normalized: PNG {norm_result.get('normalized_size')}")
        
        return jsonify({
            'success': True,
            'logo_path': relative_path,
            'message': 'Logo uploaded and normalized successfully',
            'metadata': norm_result
        })
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] Upload logo: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/brands/<int:brand_id>/upload_watermark', methods=['POST'])
@login_required
def upload_brand_watermark(brand_id):
    """Upload watermark for a brand"""
    try:
        from .database import get_brand, update_brand
        from .config import BRANDS_DIR, STORAGE_ROOT
        from werkzeug.utils import secure_filename
        
        user_id = session.get('user_id')
        
        # Check brand exists and belongs to user
        brand = get_brand(brand_id=brand_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        if brand['user_id'] != user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        
        # Check file uploaded
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        # Validate file type
        allowed_extensions = {'png', 'jpg', 'jpeg', 'webp'}
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if ext not in allowed_extensions:
            return jsonify({'success': False, 'error': f'Invalid file type. Allowed: {allowed_extensions}'}), 400
        
        # Create brand directory: /var/data/storage/brands/{user_id}/{brand_id}/
        brand_dir = os.path.join(BRANDS_DIR, str(user_id), str(brand_id))
        os.makedirs(brand_dir, exist_ok=True)
        
        # Save original file first
        original_filename = f'watermark_original.{ext}'
        original_path = os.path.join(brand_dir, original_filename)
        file.save(original_path)
        
        # Normalize image: convert to PNG, resize, clean alpha
        from .image_utils import normalize_logo
        normalized_filename = 'watermark_normalized.png'
        normalized_path = os.path.join(brand_dir, normalized_filename)
        
        # Get background removal preferences from request
        remove_bg = request.form.get('remove_bg')  # 'dark', 'light', or None
        bg_threshold = int(request.form.get('bg_threshold', 30))  # 0-255
        
        norm_result = normalize_logo(
            original_path, 
            normalized_path,
            max_dimension=2048,  # Watermarks can be larger
            remove_bg=remove_bg,
            bg_threshold=bg_threshold
        )
        
        if not norm_result['success']:
            return jsonify({
                'success': False, 
                'error': f'Failed to normalize image: {norm_result.get("error")}'
            }), 500
        
        # Store relative path of NORMALIZED version
        relative_path = os.path.relpath(normalized_path, STORAGE_ROOT)
        
        # Update database
        update_brand(brand_id, watermark_path=relative_path)
        
        print(f"[BRANDS] Uploaded & normalized watermark for brand {brand_id}: {relative_path}")
        print(f"[BRANDS] Original: {norm_result.get('original_format')} {norm_result.get('original_size')}")
        print(f"[BRANDS] Normalized: PNG {norm_result.get('normalized_size')}")
        
        return jsonify({
            'success': True,
            'watermark_path': relative_path,
            'message': 'Watermark uploaded and normalized successfully',
            'metadata': norm_result
        })
    except Exception as e:
        import traceback
        print(f"[BRANDS ERROR] Upload watermark: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# API: WATERMARK CONVERSION (WebM to MP4)
# ============================================================================

@app.route('/api/videos/convert-watermark', methods=['POST'])
@login_required
def convert_watermark():
    """Queue a watermark conversion job (async, non-blocking)"""
    try:
        if 'video' not in request.files:
            return jsonify({'error': 'No video file provided', 'reason': 'no_file'}), 400
        
        file = request.files['video']
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename', 'reason': 'empty_filename'}), 400
        
        # Generate job ID
        job_id = uuid.uuid4().hex
        
        # Save WebM file temporarily
        webm_filename = secure_filename(file.filename)
        temp_webm = os.path.join(tempfile.gettempdir(), f"{job_id}_{webm_filename}")
        file.save(temp_webm)
        
        # Generate output MP4 filename
        mp4_filename = webm_filename.replace('.webm', '.mp4')
        if not mp4_filename.endswith('.mp4'):
            mp4_filename = os.path.splitext(mp4_filename)[0] + '.mp4'
        
        output_path = os.path.join(OUTPUT_DIR, mp4_filename)
        
        # Initialize job status
        watermark_jobs[job_id] = {
            'status': 'queued',
            'filename': mp4_filename,
            'webm_path': temp_webm,
            'output_path': output_path,
            'created_at': time.time(),
            'message': 'Waiting for conversion worker...'
        }
        
        print(f"[CONVERT] Job {job_id[:8]} queued: {webm_filename} → {mp4_filename}")
        log_event('info', None, f'Watermark conversion queued: {job_id[:8]} - {webm_filename}')
        
        # Start background conversion thread
        thread = threading.Thread(
            target=_watermark_conversion_worker,
            args=(job_id, temp_webm, output_path, mp4_filename),
            daemon=True
        )
        thread.start()
        
        # Return immediately with job ID
        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': 'queued',
            'filename': mp4_filename,
            'message': 'Conversion job queued. Poll /api/videos/convert-status/<job_id> for progress.'
        })
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[CONVERT QUEUE EXCEPTION]: {error_trace}")
        log_event('error', None, f'Conversion queue error: {str(e)}')
        return jsonify({
            'error': str(e),
            'reason': 'exception',
            'message': f'Failed to queue conversion: {str(e)}'
        }), 500


def _watermark_conversion_worker(job_id, temp_webm, output_path, mp4_filename):
    """Background worker for FFmpeg watermark conversion (runs in separate thread)"""
    try:
        # Update status to processing
        watermark_jobs[job_id]['status'] = 'processing'
        watermark_jobs[job_id]['message'] = 'Converting WebM to MP4...'
        watermark_jobs[job_id]['started_at'] = time.time()
        
        print(f"[CONVERT] Job {job_id[:8]} started: {mp4_filename}")
        
        # FFmpeg command: MAXIMUM SPEED for Render free tier
        # Sacrificing quality for speed to avoid timeouts
        cmd = [
            'ffmpeg',
            '-analyzeduration', '500000',    # Reduced analysis time
            '-probesize', '500000',          # Reduced probe size
            '-i', temp_webm,
            '-map', '0:v:0',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',          # FASTEST preset (was veryfast)
            '-tune', 'fastdecode',           # Optimize for fast decode
            '-threads', '0',                 # Use all available threads (was 1)
            '-crf', '28',                    # Higher = lower quality but MUCH faster (was 23)
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '96k',                   # Lower audio bitrate (was 128k)
            '-ar', '44100',
            '-shortest',
            '-fflags', '+genpts',
            '-movflags', '+faststart',
            '-max_muxing_queue_size', '512', # Reduced queue (was 1024)
            '-y',
            output_path
        ]
        
        # Run FFmpeg conversion (background thread won't block Gunicorn worker)
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300  # 5 minute timeout
        )
        
        # Clean up temp WebM
        try:
            os.remove(temp_webm)
        except:
            pass
        
        if result.returncode != 0:
            stderr_output = result.stderr.decode('utf-8', errors='ignore')
            error_preview = stderr_output[:500] if len(stderr_output) > 500 else stderr_output
            print(f"[CONVERT] Job {job_id[:8]} FAILED (exit {result.returncode}): {error_preview}")
            
            watermark_jobs[job_id]['status'] = 'failed'
            watermark_jobs[job_id]['error'] = 'FFmpeg conversion failed'
            watermark_jobs[job_id]['stderr_preview'] = error_preview
            watermark_jobs[job_id]['exit_code'] = result.returncode
            watermark_jobs[job_id]['message'] = 'Video conversion failed. Try a shorter video.'
            log_event('error', None, f'Conversion {job_id[:8]} failed: {error_preview[:100]}')
            return
        
        # Success
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        elapsed = time.time() - watermark_jobs[job_id]['started_at']
        
        watermark_jobs[job_id]['status'] = 'completed'
        watermark_jobs[job_id]['download_url'] = f'/api/videos/download/{mp4_filename}'
        watermark_jobs[job_id]['size_mb'] = round(file_size_mb, 2)
        watermark_jobs[job_id]['conversion_time'] = round(elapsed, 1)
        watermark_jobs[job_id]['message'] = 'Video converted to MP4 successfully'
        watermark_jobs[job_id]['completed_at'] = time.time()
        
        print(f"[CONVERT] Job {job_id[:8]} SUCCESS: {mp4_filename} ({file_size_mb:.2f}MB) in {elapsed:.1f}s")
        log_event('info', None, f'Conversion {job_id[:8]} complete: {mp4_filename} ({file_size_mb:.2f}MB, {elapsed:.1f}s)')
        
    except subprocess.TimeoutExpired:
        print(f"[CONVERT] Job {job_id[:8]} TIMEOUT (>5min)")
        watermark_jobs[job_id]['status'] = 'failed'
        watermark_jobs[job_id]['error'] = 'Conversion timeout'
        watermark_jobs[job_id]['message'] = 'Video took too long to convert (>5min). Try a shorter video.'
        log_event('error', None, f'Conversion {job_id[:8]} timeout')
        
        # Clean up
        try:
            os.remove(temp_webm)
        except:
            pass
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[CONVERT] Job {job_id[:8]} EXCEPTION: {error_trace}")
        
        watermark_jobs[job_id]['status'] = 'failed'
        watermark_jobs[job_id]['error'] = str(e)
        watermark_jobs[job_id]['message'] = f'Unexpected error: {str(e)}'
        log_event('error', None, f'Conversion {job_id[:8]} exception: {str(e)}')
        
        # Clean up
        try:
            os.remove(temp_webm)
        except:
            pass


@app.route('/api/videos/convert-status/<job_id>', methods=['GET'])
@login_required
def get_conversion_status(job_id):
    """Poll conversion job status (non-blocking)"""
    if job_id not in watermark_jobs:
        return jsonify({
            'error': 'Job not found',
            'job_id': job_id,
            'message': 'Invalid job ID or job expired.'
        }), 404
    
    job = watermark_jobs[job_id]
    
    # Build response based on status
    response = {
        'job_id': job_id,
        'status': job['status'],
        'filename': job['filename'],
        'message': job.get('message', '')
    }
    
    if job['status'] == 'completed':
        response['download_url'] = job['download_url']
        response['size_mb'] = job['size_mb']
        response['conversion_time'] = job['conversion_time']
    elif job['status'] == 'failed':
        response['error'] = job.get('error', 'Unknown error')
        if 'stderr_preview' in job:
            response['stderr_preview'] = job['stderr_preview']
        if 'exit_code' in job:
            response['exit_code'] = job['exit_code']
    
    return jsonify(response)

# Stub endpoints removed - focus on core watermarking functionality


# ================================================================
# DEBUG ENDPOINT: BRAND INTEGRITY CHECK
# ================================================================
@app.route('/api/debug/brand-integrity', methods=['GET'])
def debug_brand_integrity():
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imports", "brands")

    if not os.path.exists(base):
        return jsonify({
            "success": False,
            "error": "Brand directory not found",
            "path": base
        }), 500

    brands = {}
    for brand in os.listdir(base):
        folder = os.path.join(base, brand)
        if os.path.isdir(folder):
            brands[brand] = {
                "template.png": os.path.exists(os.path.join(folder, "template.png")),
                "logo.png": os.path.exists(os.path.join(folder, "logo.png")),
                "watermark.png": os.path.exists(os.path.join(folder, "watermark.png")),
                "path": folder
            }

    return jsonify({
        "success": True,
        "brands": brands
    })

# ================================================================
# DEBUG ENDPOINT: FFmpeg FILTER COMPLEX DRY-RUN
# ================================================================
@app.route('/api/debug/build-filter/<brand_name>', methods=['GET'])
def debug_build_filter(brand_name):
    from .video_processor import VideoProcessor
    from .brand_loader import get_available_brands
    import os

    portal_dir = os.path.dirname(os.path.abspath(__file__))
    brands = get_available_brands(portal_dir)

    # Find the brand config by name (case-insensitive)
    brand = next((b for b in brands if b["name"].lower() == brand_name.lower()), None)

    if not brand:
        return jsonify({
            "success": False,
            "error": f"Brand '{brand_name}' not found",
            "brands_available": [b['name'] for b in brands]
        }), 404

    # Generate a dry-run filter without needing a real video
    # Use os.devnull to avoid FFprobe errors
    vp = VideoProcessor(video_path=os.devnull)
    filter_complex = vp.build_filter_complex(brand)

    return jsonify({
        "success": True,
        "brand": brand_name,
        "filter_complex": filter_complex
    })


# ================================================================
# WTF DOWNLOADER ENDPOINTS
# ================================================================

@app.route('/api/videos/download-original/<int:download_id>')
@login_required
def download_original_file(download_id):
    """Download original video file from downloads table"""
    from .database import get_download
    import os
    from flask import send_from_directory
    
    user_id = session['user_id']
    
    # Get download record for this user
    download_record = get_download(download_id, user_id)
    if not download_record:
        return jsonify({'error': 'Download not found or unauthorized'}), 404
    
    file_path = download_record['file_path']
    filename = download_record['filename']
    
    # Verify file exists
    if not os.path.exists(file_path):
        # Check if file exists in alternative locations
        # First check RAW_DIR
        alt_path = os.path.join(RAW_DIR, filename)
        
        if os.path.exists(alt_path):
            file_path = alt_path
        else:
            # Also check the old OUTPUT_DIR
            alt_path = os.path.join(OUTPUT_DIR, filename)
            if os.path.exists(alt_path):
                file_path = alt_path
            else:
                return jsonify({'error': 'File has expired or been deleted'}), 404
    
    # Send file
    directory = os.path.dirname(file_path)
    return send_from_directory(directory, os.path.basename(file_path), as_attachment=True)


@app.route('/api/videos/save-download', methods=['POST'])
@login_required
def save_video_download():
    """Save a video download record to the database"""
    from .database import save_download
    import os
    
    data = request.get_json(force=True) or {}
    source_url = data.get('source_url')
    filename = data.get('filename')
    file_path = data.get('file_path')
    
    if not all([source_url, filename, file_path]):
        return jsonify({'error': 'source_url, filename, and file_path are required'}), 400
    
    user_id = session['user_id']
    
    # Verify file exists
    if not os.path.exists(file_path):
        return jsonify({'error': 'File does not exist at specified path'}), 400
    
    download_id = save_download(user_id, source_url, filename, file_path)
    
    return jsonify({
        'success': True,
        'download_id': download_id,
        'message': 'Download saved successfully'
    })


@app.route('/api/downloads/recent', methods=['GET'])
@login_required
def get_recent_downloads():
    """Get recent downloads for the current user"""
    from .database import get_user_downloads
    
    user_id = session['user_id']
    limit = request.args.get('limit', 10, type=int)
    
    downloads = get_user_downloads(user_id, limit)
    
    # Check if files exist
    for download in downloads:
        import os
        download['file_exists'] = os.path.exists(download['file_path'])
    
    return jsonify({
        'success': True,
        'downloads': downloads,
        'count': len(downloads)
    })


@app.route('/api/downloads/cleanup', methods=['POST'])
@login_required
def cleanup_downloads():
    """Cleanup old downloads"""
    from .database import cleanup_old_downloads
    
    data = request.get_json(force=True) or {}
    max_age_hours = data.get('max_age_hours', 24)
    
    deleted_count = cleanup_old_downloads(max_age_hours)
    
    return jsonify({
        'success': True,
        'deleted_count': deleted_count,
        'message': f'Deleted {deleted_count} old downloads'
    })

@app.route('/api/detect-platform', methods=['POST'])
def api_detect_platform():
    """Detect the platform from a URL."""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        from downloader.platform_detector import detect_platform
        platform = detect_platform(url)
        return jsonify({"url": url, "platform": platform})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download', methods=['POST'])
def api_download_video():
    """Download a single video."""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Import and run the async download function
        import asyncio
        from downloader.batch_downloader import download_single_video
        result = asyncio.run(download_single_video(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/batch', methods=['POST'])
def api_download_batch():
    """Download multiple videos."""
    try:
        data = request.get_json()
        urls = data.get('urls', [])
        
        if not urls:
            return jsonify({"error": "At least one URL is required"}), 400
        
        # Import and run the async batch download function
        import asyncio
        from downloader.batch_downloader import download_batch
        results = asyncio.run(download_batch(urls))
        return jsonify({"downloads": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



def schedule_cleanup():
    """Schedule periodic cleanup of old files"""
    import time
    import threading
    from .database import cleanup_old_downloads
    
    def cleanup_worker():
        while True:
            try:
                # Run cleanup every 6 hours
                deleted_count = cleanup_old_downloads(24)  # Delete files older than 24 hours
                print(f"[CLEANUP] Deleted {deleted_count} old downloads and files")
                time.sleep(6 * 60 * 60)  # 6 hours
            except Exception as e:
                print(f"[CLEANUP] Error during cleanup: {e}")
                time.sleep(60 * 60)  # Wait 1 hour before trying again
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()


# Schedule periodic cleanup
schedule_cleanup()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
