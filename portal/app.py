"""
Brandr - Flask Application
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
    MAX_UPLOAD_SIZE, BRANDS_DIR,
    TIER_CONFIG, DEFAULT_TIER, get_tier_limits, get_effective_limits,
    get_payment_link, get_badge_info, get_next_visible_tier,
    get_tier_features, TIER_FEATURES,
    ADMIN_EMAILS, SPECIAL_STATUSES, VISIBLE_TIERS,
    calculate_output_contract,
)
from .database import (
    log_event, get_daily_usage, increment_branding_jobs, increment_downloads,
    get_user_special_status, set_user_special_status
)


# Authentication functions

def hash_password(password):
    """Hash a password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()


def init_users_db():
    """Initialize the users database table"""
    from .database import get_connection
    with get_connection() as conn:
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


def authenticate_user(email, password):
    """Authenticate a user by email and password. Returns (user_id, status) tuple.
    status is None on success, or a string describing why login was blocked."""
    from .database import get_connection
    with get_connection() as conn:
        c = conn.cursor()
        # Defensive: use COALESCE to handle missing columns in older DBs
        c.execute('SELECT id, password_hash, COALESCE(account_status, ?) as account_status, COALESCE(must_change_password, ?) as must_change_password FROM users WHERE email = ?', ('active', 0, email))
        result = c.fetchone()
    
    if result:
        user_id = result['id']
        stored_hash = result['password_hash']
        account_status = result['account_status']
        must_change = result['must_change_password']
        
        if account_status == 'deactivated':
            return None, 'deactivated'
        if account_status == 'suspended':
            return None, 'suspended'
        if stored_hash == hash_password(password):
            if must_change:
                return user_id, 'must_change_password'
            return user_id, None
    return None, None


def register_user(email, password):
    """Register a new user. Admin emails default to Platinum tier, no special_status."""
    from .database import get_connection
    try:
        is_admin_email = email.lower() in [e.lower() for e in ADMIN_EMAILS]
        tier = 'Platinum' if is_admin_email else 'Explorer'
        special_status = None  # Never auto-assign beta_tester
        
        with get_connection() as conn:
            c = conn.cursor()
            password_hash = hash_password(password)
            c.execute(
                'INSERT INTO users (email, password_hash, tier, special_status) VALUES (?, ?, ?, ?)',
                (email, password_hash, tier, special_status)
            )
            conn.commit()
            user_id = c.lastrowid
        
        print(f"[AUTH] Registered user: {email} tier={tier} special_status={special_status}")
        return user_id
    except sqlite3.IntegrityError:
        return None


def get_user_tier(user_id):
    """Get a user's tier from the database. Returns tier name string."""
    from .database import get_connection
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT tier FROM users WHERE id = ?', (user_id,))
        row = c.fetchone()
    if row and row['tier']:
        return row['tier']
    return DEFAULT_TIER


def login_required(f):
    """Decorator to require login for certain routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('force_password_change') and f.__name__ != 'change_password':
            return redirect(url_for('change_password'))
        return f(*args, **kwargs)
    return decorated_function


def is_admin(email=None):
    """Check if the given email (or session email) is an admin."""
    email = email or session.get('email', '')
    return email.lower() in [e.lower() for e in ADMIN_EMAILS]


def admin_required(f):
    """Decorator: requires login + admin email"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not is_admin():
            return jsonify({'error': 'Forbidden'}), 403
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


@app.context_processor
def inject_global_context():
    """Inject admin flag, tier, badge info, and feature gates into all templates."""
    ctx = {'is_admin_user': is_admin(), 'tier': DEFAULT_TIER,
           'tier_features': get_tier_features(DEFAULT_TIER),
           'all_tier_features': TIER_FEATURES,
           'all_tier_config': TIER_CONFIG}
    user_id = session.get('user_id')
    if user_id:
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        badge = get_badge_info(tier, special_status)
        ctx['tier'] = tier
        ctx['user_badge'] = badge
        ctx['user_special_status'] = special_status
        ctx['tier_features'] = get_tier_features(tier)
    return ctx


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
            return redirect(url_for('dashboard'))
        else:
            flash('Email already exists or registration failed', 'error')
    
    return render_template('register.html')

@app.route('/portal/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user_id, status = authenticate_user(email, password)
        if status == 'deactivated':
            flash('This account has been deactivated.', 'error')
        elif status == 'suspended':
            flash('This account has been suspended. Contact support.', 'error')
        elif user_id and status == 'must_change_password':
            session['user_id'] = user_id
            session['email'] = email
            session['force_password_change'] = True
            return redirect(url_for('change_password'))
        elif user_id:
            session['user_id'] = user_id
            session['email'] = email
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/portal/logout')
def logout():
    session.pop('user_id', None)
    session.pop('email', None)
    session.pop('force_password_change', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))


@app.route('/portal/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Force password change page (after admin reset)."""
    from .database import get_connection
    if request.method == 'POST':
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        if len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        elif new_password != confirm_password:
            flash('Passwords do not match.', 'error')
        else:
            with get_connection() as conn:
                c = conn.cursor()
                c.execute('UPDATE users SET password_hash = ?, must_change_password = 0, account_status = ? WHERE id = ?',
                          (hash_password(new_password), 'active', session['user_id']))
                conn.commit()
            session.pop('force_password_change', None)
            flash('Password changed successfully.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html')


# ── Admin API: Password Reset ──
@app.route('/api/admin/reset-password', methods=['POST'])
@admin_required
def admin_reset_password():
    """Generate a temporary password for a user. Returns it ONCE."""
    import secrets
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'user_id required'}), 400

    # Generate secure 12-char temp password
    temp_password = secrets.token_urlsafe(9)  # 12 chars
    hashed = hash_password(temp_password)

    with get_connection() as conn:
        c = conn.cursor()
        # Defensive: COALESCE for missing column
        c.execute('SELECT email, COALESCE(account_status, ?) as account_status FROM users WHERE id = ?', ('active', user_id))
        user = c.fetchone()
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        if user['account_status'] == 'deactivated':
            return jsonify({'success': False, 'error': 'Cannot reset password for deactivated user'}), 400

        c.execute('UPDATE users SET password_hash = ?, must_change_password = 1, account_status = ? WHERE id = ?',
                  (hashed, 'pending_reset', user_id))
        conn.commit()

    print(f"[ADMIN] Password reset for user {user_id} ({user['email']}) by {session.get('email')}")
    return jsonify({'success': True, 'temp_password': temp_password, 'email': user['email']})


# ── Admin API: User Info (pre-delete preview) ──
@app.route('/api/admin/user-info/<int:user_id>')
@admin_required
def admin_user_info(user_id):
    """Return user details and associated data counts for deletion preview."""
    from .database import get_connection
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT id, email, tier, account_status FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        c.execute('SELECT COUNT(*) as cnt FROM brands WHERE user_id = ? AND is_active = 1', (user_id,))
        brand_count = c.fetchone()['cnt']
        c.execute('SELECT COUNT(*) as cnt FROM downloads WHERE user_id = ?', (user_id,))
        download_count = c.fetchone()['cnt']

    return jsonify({
        'success': True,
        'email': user['email'],
        'tier': user['tier'],
        'brand_count': brand_count,
        'download_count': download_count,
    })


# ── Admin API: Delete User (soft delete) ──
@app.route('/api/admin/delete-user', methods=['POST'])
@admin_required
def admin_delete_user():
    """Soft-delete a user: deactivate account and soft-delete their brands."""
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    confirmation = data.get('confirmation', '')

    if not user_id:
        return jsonify({'success': False, 'error': 'user_id required'}), 400
    if confirmation != 'DEACTIVATE':
        return jsonify({'success': False, 'error': 'Type DEACTIVATE to confirm'}), 400

    # Safety: cannot deactivate yourself
    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'error': 'Cannot deactivate your own account'}), 400

    # Safety: cannot delete the last admin
    with get_connection() as conn:
        c = conn.cursor()
        # Defensive: COALESCE for missing column
        c.execute('SELECT email, COALESCE(account_status, ?) as account_status FROM users WHERE id = ?', ('active', user_id))
        target = c.fetchone()
        if not target:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        target_email = target['email']
        if target_email.lower() in [e.lower() for e in ADMIN_EMAILS]:
            # Count remaining active admins
            admin_ids = []
            for admin_email in ADMIN_EMAILS:
                c.execute("SELECT id, COALESCE(account_status, ?) as account_status FROM users WHERE LOWER(email) = LOWER(?)", ('active', admin_email))
                row = c.fetchone()
                if row and row['account_status'] != 'deactivated':
                    admin_ids.append(row['id'])
            if len(admin_ids) <= 1:
                return jsonify({'success': False, 'error': 'Cannot deactivate the last remaining admin'}), 400

        # Deactivate user account
        c.execute('UPDATE users SET account_status = ? WHERE id = ?', ('deactivated', user_id))
        # Disable all their active brands
        c.execute('UPDATE brands SET is_active = 0 WHERE user_id = ? AND is_active = 1', (user_id,))
        brands_deleted = c.rowcount
        conn.commit()

    print(f"[ADMIN] Deactivated user {user_id} ({target_email}), {brands_deleted} brands disabled, by {session.get('email')}")
    return jsonify({'success': True, 'email': target_email, 'brands_deleted': brands_deleted})


# ── Admin API: Reset Account (wipe data + deactivate) ──
@app.route('/api/admin/reset-account', methods=['POST'])
@admin_required
def admin_reset_account():
    """Reset a user's account: wipe all owned data, disable account, preserve audit shell."""
    from .database import get_connection
    import os
    
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    confirmation = data.get('confirmation', '')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'user_id required'}), 400
    
    # Get target user for validation
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT email, COALESCE(account_status, ?) as account_status FROM users WHERE id = ?', ('active', user_id))
        target = c.fetchone()
        if not target:
            return jsonify({'success': False, 'error': 'User not found'}), 404
    
    target_email = target['email']
    
    # Validate typed confirmation: must be "RESET user@example.com"
    expected_confirmation = f'RESET {target_email}'
    if confirmation != expected_confirmation:
        return jsonify({'success': False, 'error': f'Type exactly: RESET {target_email}'}), 400
    
    # Safety: cannot reset your own account
    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'error': 'Cannot reset your own account'}), 400
    
    # Safety: cannot reset the last admin
    if target_email.lower() in [e.lower() for e in ADMIN_EMAILS]:
        with get_connection() as conn:
            c = conn.cursor()
            admin_ids = []
            for admin_email in ADMIN_EMAILS:
                c.execute("SELECT id, COALESCE(account_status, ?) as account_status FROM users WHERE LOWER(email) = LOWER(?)", ('active', admin_email))
                row = c.fetchone()
                if row and row['account_status'] != 'deactivated':
                    admin_ids.append(row['id'])
            if len(admin_ids) <= 1:
                return jsonify({'success': False, 'error': 'Cannot reset the last remaining admin'}), 400
    
    # Execute reset: count what will be removed
    with get_connection() as conn:
        c = conn.cursor()
        
        # Count active brands
        c.execute('SELECT COUNT(*) as cnt FROM brands WHERE user_id = ? AND is_active = 1', (user_id,))
        brand_count = c.fetchone()['cnt']
        
        # Count downloads
        c.execute('SELECT COUNT(*) as cnt FROM downloads WHERE user_id = ?', (user_id,))
        download_count = c.fetchone()['cnt']
        
        # Count usage records
        c.execute('SELECT COUNT(*) as cnt FROM daily_usage WHERE user_id = ?', (user_id,))
        usage_count = c.fetchone()['cnt']
        
        # Get user's brand names first (for cleaning up brand_configs)
        c.execute('SELECT name FROM brands WHERE user_id = ? AND is_system = 0', (user_id,))
        brand_names = [row['name'] for row in c.fetchall()]
        
        # Hard delete user-created brands (not system brands) - fixes UNIQUE constraint issue
        c.execute('DELETE FROM brands WHERE user_id = ? AND is_system = 0', (user_id,))
        
        # Clean up legacy brand_configs for those brand names
        if brand_names:
            placeholders = ','.join('?' * len(brand_names))
            c.execute(f'DELETE FROM brand_configs WHERE brand_name IN ({placeholders})', brand_names)
        
        # Delete downloads (hard delete - these are file references)
        c.execute('DELETE FROM downloads WHERE user_id = ?', (user_id,))
        
        # Clear usage history
        c.execute('DELETE FROM daily_usage WHERE user_id = ?', (user_id,))
        
        # Deactivate account
        c.execute('UPDATE users SET account_status = ? WHERE id = ?', ('deactivated', user_id))
        
        conn.commit()
    
    # Write audit log
    admin_email = session.get('email', 'unknown')
    admin_user_id = session.get('user_id')
    data_summary = json.dumps({
        'brands_disabled': brand_count,
        'downloads_deleted': download_count,
        'usage_records_cleared': usage_count,
    })
    
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO audit_log 
                     (admin_user_id, admin_email, action_type, target_user_id, target_email, details, data_summary)
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (admin_user_id, admin_email, 'reset_account', user_id, target_email,
                   f'Reset account: disabled {brand_count} brands, deleted {download_count} downloads, cleared {usage_count} usage records',
                   data_summary))
        conn.commit()
    
    print(f"[ADMIN] Reset account for user {user_id} ({target_email}): {brand_count} brands, {download_count} downloads, {usage_count} usage records. By {admin_email}")
    return jsonify({
        'success': True,
        'email': target_email,
        'brands_disabled': brand_count,
        'downloads_deleted': download_count,
        'usage_records_cleared': usage_count,
    })


# ── Admin API: Update Account Status ──
@app.route('/api/admin/update-status', methods=['POST'])
@admin_required
def admin_update_status():
    """Update a user's account_status (active, suspended, etc.)"""
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    new_status = data.get('account_status')
    valid_statuses = ('active', 'suspended', 'pending_reset', 'deactivated')

    if not user_id or new_status not in valid_statuses:
        return jsonify({'success': False, 'error': f'user_id and valid account_status required ({", ".join(valid_statuses)})'}), 400

    # Safety: cannot suspend/deactivate yourself
    if user_id == session.get('user_id') and new_status in ('suspended', 'deactivated'):
        return jsonify({'success': False, 'error': 'Cannot suspend or deactivate your own account'}), 400

    with get_connection() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET account_status = ? WHERE id = ?', (new_status, user_id))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404

    print(f"[ADMIN] Set user {user_id} account_status={new_status} by {session.get('email')}")
    return jsonify({'success': True, 'user_id': user_id, 'account_status': new_status})


# User state detection for Dashboard
def get_user_state(user_id):
    """Detect user state: 'no_brand_kit' or 'has_brand_kit'"""
    from .database import get_all_brands
    try:
        user_brands = get_all_brands(user_id=user_id, include_system=False)
        return 'has_brand_kit' if user_brands else 'no_brand_kit'
    except Exception as e:
        print(f"[DASHBOARD] Error detecting user state: {e}")
        return 'has_brand_kit'  # Default to workspace view on error


# Dashboard route - adaptive home based on user state
@app.route('/portal/dashboard')
@login_required
def dashboard():
    """Adaptive dashboard based on user state"""
    from .database import get_user_brand_count
    user_id = session.get('user_id')
    state = get_user_state(user_id)
    
    # Tier/limit context for usage widget
    tier = get_user_tier(user_id)
    special_status = get_user_special_status(user_id)
    limits = get_effective_limits(tier, special_status)
    max_brands = limits.get('max_brand_configs', 1)
    brand_count = get_user_brand_count(user_id) if user_id else 0
    can_create = (max_brands == -1) or (brand_count < max_brands)
    
    # Daily usage counters
    usage = get_daily_usage(user_id) if user_id else {'branding_jobs': 0, 'downloads': 0}
    
    return render_template('dashboard.html',
        state=state,
        tier=tier,
        max_brands=max_brands,
        brand_count=brand_count,
        can_create=can_create,
        usage=usage,
        limits=limits,
    )


# Default routing based on login status
@app.route('/portal/')
def portal_home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    else:
        return redirect(url_for('brand_video'))


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
    """Redirect root to portal"""
    return redirect(url_for('portal_home'))

@app.route('/api')
def api_root():
    """API root endpoint listing available API routes"""
    return jsonify({
        "status": "ok",
        "message": "Brandr API",
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
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    return render_template('downloader.html', tier=tier)

@app.route('/portal/library')
@login_required
def library():
    """Library page - view saved source media"""
    return render_template('library.html')

@app.route('/portal/brand')
@login_required
def brand_video():
    """Brand a video page"""
    try:
        user_id = session.get('user_id')
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        next_tier = get_next_visible_tier(tier)
        return render_template('clean_dashboard.html',
            tier=tier,
            max_brands_per_job=limits['max_brands_per_job'],
            next_tier=next_tier,
        )
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
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    return render_template('brands.html', tier=tier)

@app.route('/portal/profile')
@login_required
def profile_page():
    """User profile page with graceful error handling"""
    from .database import get_connection, get_all_brands, get_user_downloads
    from datetime import datetime
    
    try:
        user_id = session.get('user_id')
        email = session.get('email', 'User')
        
        # Get user info from database
        with get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT created_at, tier FROM users WHERE id = ?', (user_id,))
            user = c.fetchone()
        
        # Format created date
        created_at = 'Recently'
        if user and user['created_at']:
            try:
                created_dt = datetime.fromisoformat(user['created_at'])
                created_at = created_dt.strftime('%B %Y')
            except:
                pass
        
        # Get tier from database via helper
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        
        # Get actual brand count
        user_brands = get_all_brands(user_id=user_id, include_system=False)
        brand_configs = len(user_brands)
        
        return render_template('profile.html',
            email=email,
            created_at=created_at,
            tier=tier,
            limits=limits,
            brand_configs=brand_configs,
        )
    except Exception as e:
        print(f"[PROFILE ERROR] Failed to load profile page: {e}")
        import traceback
        traceback.print_exc()
        fallback_limits = get_tier_limits(DEFAULT_TIER)
        return render_template('profile.html',
            email=session.get('email', 'User'),
            created_at='Recently',
            tier=DEFAULT_TIER,
            limits=fallback_limits,
            brand_configs=0,
        ), 200

@app.route('/portal/shipr')
@login_required
def shipr_page():
    """Coming soon page"""
    return render_template('shipr.html')


# ============================================================================
# ADMIN CONSOLE
# ============================================================================

@app.route('/portal/admin')
@admin_required
def admin_console():
    """Admin console — manage users and tiers"""
    from .database import get_connection
    with get_connection() as conn:
        c = conn.cursor()
        # Defensive query: use COALESCE to handle missing columns gracefully
        c.execute('''SELECT u.id, u.email, u.tier, u.special_status, u.created_at,
                            COALESCE(u.account_status, 'active') as account_status,
                            COALESCE(u.must_change_password, 0) as must_change_password,
                            (SELECT COUNT(*) FROM brands b WHERE b.user_id = u.id AND b.is_active = 1) as brand_count,
                            (SELECT COUNT(*) FROM daily_usage du WHERE du.user_id = u.id AND du.usage_date = date('now')) as jobs_today
                     FROM users u ORDER BY u.created_at DESC''')
        users = [dict(row) for row in c.fetchall()]
        
        # Fetch recent admin actions for audit visibility
        try:
            c.execute('''SELECT admin_email, action_type, target_email, details, created_at
                         FROM audit_log
                         ORDER BY created_at DESC
                         LIMIT 20''')
            recent_actions = [dict(row) for row in c.fetchall()]
        except sqlite3.OperationalError:
            # audit_log table might not exist yet
            recent_actions = []
    
    tiers = list(TIER_CONFIG.keys())
    statuses = [''] + list(SPECIAL_STATUSES.keys())
    return render_template('admin.html', users=users, tiers=tiers, statuses=statuses, recent_actions=recent_actions)


@app.route('/api/admin/set-tier', methods=['POST'])
@admin_required
def admin_set_tier():
    """Set a user's tier and/or special_status (admin only)"""
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    new_tier = data.get('tier')
    new_status = data.get('special_status')  # '' or None means clear

    if not user_id or not new_tier:
        return jsonify({'success': False, 'error': 'user_id and tier required'}), 400
    if new_tier not in TIER_CONFIG:
        return jsonify({'success': False, 'error': f'Invalid tier: {new_tier}'}), 400
    if new_status and new_status not in SPECIAL_STATUSES:
        return jsonify({'success': False, 'error': f'Invalid status: {new_status}'}), 400

    # Normalize empty string to None
    new_status = new_status if new_status else None

    with get_connection() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET tier = ?, special_status = ? WHERE id = ?', (new_tier, new_status, user_id))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404

    print(f"[ADMIN] Set user {user_id} to tier={new_tier} status={new_status} by {session.get('email')}")
    return jsonify({'success': True, 'user_id': user_id, 'tier': new_tier, 'special_status': new_status})


@app.route('/portal/test')
@login_required
def test_page():
    """Test page to verify portal is online"""
    return jsonify({
        'status': 'online',
        'message': 'Brandr is running',
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
    return jsonify({'status': 'healthy', 'message': 'Brandr is running'}), 200


@app.route('/api/upgrade-link/<tier_name>')
@login_required
def upgrade_link(tier_name):
    """Return the PayPal payment link for a given tier."""
    link = get_payment_link(tier_name)
    if not link:
        return jsonify({'success': False, 'error': f'No payment link available for {tier_name}'}), 404
    return jsonify({'success': True, 'url': link, 'tier': tier_name})


@app.route('/api/usage')
@login_required
def api_usage():
    """Return current daily usage and limits for the logged-in user."""
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    special_status = get_user_special_status(user_id)
    limits = get_effective_limits(tier, special_status)
    usage = get_daily_usage(user_id)
    return jsonify({
        'success': True,
        'tier': tier,
        'usage': usage,
        'limits': {
            'branding_jobs_per_day': limits['branding_jobs_per_day'],
            'fetches_per_day': limits['fetches_per_day'],
            'max_brands_per_job': limits['max_brands_per_job'],
            'max_outputs_per_job': limits.get('max_outputs_per_job', limits['max_brands_per_job']),
        }
    })


@app.route('/api/videos/output-contract', methods=['POST'])
@login_required
def api_output_contract():
    """
    Calculate and return the output contract for a proposed job.
    
    Request body:
    - source_count: number of source videos (Beta-v1: always 1)
    - brand_count: number of selected brands
    - variant_count: number of output variants (Beta-v1: always 1)
    
    Returns:
    - computed_outputs: total outputs that will be generated
    - max_outputs_per_job: tier limit
    - within_limit: boolean
    - blocking: boolean
    """
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    special_status = get_user_special_status(user_id)
    limits = get_effective_limits(tier, special_status)
    
    data = request.get_json(force=True) or {}
    source_count = data.get('source_count', 1)
    brand_count = data.get('brand_count', 0)
    variant_count = data.get('variant_count', 1)
    
    contract = calculate_output_contract(source_count, brand_count, variant_count, limits)
    
    return jsonify({
        'success': True,
        'tier': tier,
        'contract': contract,
    })


@app.route('/portal/downloader_dashboard')
@login_required
def downloader_dashboard():
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    return render_template("downloader_dashboard.html", tier=tier)

# ============================================================================
# API: VIDEO PROCESSING
# ============================================================================

@app.route('/api/videos/process_brands', methods=['POST'])
@login_required
def process_branded_videos():
    """Process video with selected brand overlays (brand_id-first)"""
    try:
        # --- Tier enforcement: daily branding jobs limit ---
        user_id = session.get('user_id')
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        usage = get_daily_usage(user_id)
        if usage['branding_jobs'] >= limits['branding_jobs_per_day']:
            return jsonify({
                'success': False,
                'error': 'DAILY_LIMIT_REACHED',
                'message': f"You've used all {limits['branding_jobs_per_day']} branding jobs for today. Resets at midnight UTC.",
                'tier': tier,
                'limit': limits['branding_jobs_per_day'],
                'used': usage['branding_jobs'],
            }), 403

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
        
        # --- Tier enforcement: max brands per job ---
        num_brands = len(brand_ids) or len(selected_brands)
        max_per_job = limits['max_brands_per_job']
        if num_brands > max_per_job:
            return jsonify({
                'success': False,
                'error': 'BRANDS_PER_JOB_LIMIT',
                'message': f'{tier} plan allows {max_per_job} brands per job. You selected {num_brands}.',
                'tier': tier,
                'limit': max_per_job,
                'selected': num_brands,
            }), 403

        # --- Tier enforcement: max outputs per job (OUTPUT CONTRACT) ---
        # Beta-v1: source_count=1, variant_count=1, so outputs = brands
        source_count = 1  # Beta-v1: single source only
        variant_count = 1  # Beta-v1: no multi-variant rendering yet
        contract = calculate_output_contract(source_count, num_brands, variant_count, limits)
        
        if contract['blocking']:
            return jsonify({
                'success': False,
                'error': 'OUTPUTS_PER_JOB_LIMIT',
                'message': f'{tier} plan allows {contract["max_outputs_per_job"]} outputs per job. Your selection would generate {contract["computed_outputs"]} outputs ({source_count} source × {num_brands} brands × {variant_count} variants).',
                'tier': tier,
                'limit': contract['max_outputs_per_job'],
                'computed_outputs': contract['computed_outputs'],
                'contract': contract,
            }), 403

        # Optional branding configuration overrides (applied temporarily per-video)
        # These are read directly from 'data' dict later, no need to pre-extract

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
                    # Configure yt_dlp with platform-specific options
                    is_instagram = 'instagram.com' in url_input.lower()
                    is_tiktok = 'tiktok.com' in url_input.lower()
                    
                    ydl_opts = {
                        'outtmpl': os.path.join(RAW_DIR, '%(id)s.%(ext)s'),
                        'merge_output_format': 'mp4',
                        'format': 'bv*+ba/best',
                        'prefer_ffmpeg': True,
                        'retries': 5,
                        'fragment_retries': 5,
                        'socket_timeout': 300,
                    }
                    
                    # Apply Instagram-specific headers only for Instagram URLs
                    if is_instagram:
                        ydl_opts['http_headers'] = {
                            'User-Agent': 'Instagram 271.1.0.21.84 Android',
                            'X-IG-App-ID': '567067343352427',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        }
                    
                    # Apply TikTok impersonation for TikTok URLs
                    # Note: impersonation requires curl_cffi and specific target format
                    # Temporarily disabled until proper integration is tested
                    # if is_tiktok:
                    #     ydl_opts['impersonate'] = ('chrome', '110', 'windows')
                    
                    # Only add cookiefile if the file exists and is readable
                    from .config import COOKIE_FILE as cookie_file
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
            
            # Watermark is OPTIONAL - only validate if present
            wm_path = db_brand.get('watermark_path') or db_brand.get('watermark_vertical')
            has_watermark = wm_path is not None and wm_path != ''
            
            if has_watermark:
                # Validate watermark file exists on disk (only if path is set)
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
                
                # Validate watermark config values (only if watermark exists)
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
            
            # Logo validation
            logo_path = db_brand.get('logo_path')
            logo_scale = db_brand.get('logo_scale', 0)
            
            # Require at least one asset: logo OR watermark (not both required)
            if not has_watermark and (logo_scale == 0 or not logo_path):
                validation_errors.append({
                    'brand_id': brand_id,
                    'brand': brand_name,
                    'error': 'Brand must have at least a logo or watermark',
                    'fix': 'Upload logo or watermark in Manage Brands',
                    'fix_url': '/portal/brands'
                })
                continue
            
            # Validate logo if configured (non-zero scale) but path missing → fail
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
        
        # --- Dual-Logo Composition: resolve secondary logo (Platinum+ only) ---
        sec_logo_resolved_path = None
        if data.get('secondary_logo_enabled'):
            from .config import get_tier_features
            tier_features = get_tier_features(tier)
            if tier_features.get('dual_logo_composition_enabled'):
                sec_brand_id = data.get('secondary_logo_brand_id')
                if sec_brand_id:
                    try:
                        sec_brand_id = int(sec_brand_id)
                        sec_brand = get_brand(brand_id=sec_brand_id, user_id=user_id)
                        if sec_brand and sec_brand.get('logo_path'):
                            sec_logo_full = os.path.join(STORAGE_ROOT, sec_brand['logo_path'])
                            if os.path.exists(sec_logo_full):
                                sec_logo_resolved_path = sec_logo_full
                                print(f"[PROCESS BRANDS] Secondary logo resolved: brand #{sec_brand_id} -> {sec_logo_full}")
                            else:
                                print(f"[PROCESS BRANDS] Secondary logo file not found on disk: {sec_logo_full}")
                        else:
                            print(f"[PROCESS BRANDS] Secondary brand #{sec_brand_id} not found or has no logo (user_id={user_id})")
                    except (ValueError, TypeError):
                        print(f"[PROCESS BRANDS] Invalid secondary_logo_brand_id: {data.get('secondary_logo_brand_id')}")
            else:
                print(f"[PROCESS BRANDS] Dual-logo composition not available for tier: {tier}")
        
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
            
            # Merge temporary overrides from sliders (DO NOT save to database)
            # These are per-video adjustments that should NOT affect the brand's default config
            merged_config = db_brand.copy()  # Make a copy to avoid modifying DB record
            
            # CANONICAL NORMALIZATION: Ensure wm_* fields are populated
            # Prefer canonical wm_* fields, fallback to legacy watermark_* for backward compat
            if merged_config.get('wm_mode') is None:
                merged_config['wm_mode'] = merged_config.get('watermark_mode', 'positioned')
            if merged_config.get('wm_scale') is None:
                legacy_scale = merged_config.get('watermark_scale')
                if legacy_scale is not None:
                    merged_config['wm_scale'] = legacy_scale
            if merged_config.get('wm_opacity') is None:
                legacy_opacity = merged_config.get('watermark_opacity')
                if legacy_opacity is not None:
                    merged_config['wm_opacity'] = legacy_opacity
            
            # Log normalized values for debugging
            print(f"[PROCESS BRANDS] Normalized wm_mode={merged_config.get('wm_mode')}, wm_scale={merged_config.get('wm_scale')}, wm_opacity={merged_config.get('wm_opacity')}")
            
            # Apply overrides if provided in the request
            override_applied = False
            if 'watermark_scale' in data:
                merged_config['wm_scale'] = data['watermark_scale']
                override_applied = True
                print(f"[PROCESS BRANDS] Override: watermark_scale = {data['watermark_scale']} (DB default: {db_brand.get('wm_scale')})")
            if 'watermark_opacity' in data:
                merged_config['wm_opacity'] = data['watermark_opacity']
                override_applied = True
                print(f"[PROCESS BRANDS] Override: watermark_opacity = {data['watermark_opacity']} (DB default: {db_brand.get('wm_opacity')})")
            if 'logo_scale' in data:
                merged_config['logo_scale'] = data['logo_scale']
                override_applied = True
                print(f"[PROCESS BRANDS] Override: logo_scale = {data['logo_scale']} (DB default: {db_brand.get('logo_scale')})")
            if 'logo_padding' in data:
                merged_config['logo_padding'] = data['logo_padding']
                override_applied = True
                print(f"[PROCESS BRANDS] Override: logo_padding = {data['logo_padding']} (DB default: {db_brand.get('logo_padding')})")
            
            # Merge secondary logo composition fields (already tier-gated above)
            if sec_logo_resolved_path:
                merged_config['secondary_logo_enabled'] = True
                merged_config['secondary_logo_resolved_path'] = sec_logo_resolved_path
                merged_config['secondary_logo_scale'] = max(0.03, min(0.5, float(data.get('secondary_logo_scale', 0.12))))
                merged_config['secondary_logo_opacity'] = max(0.1, min(1.0, float(data.get('secondary_logo_opacity', 0.9))))
                merged_config['secondary_logo_x'] = max(0.0, min(1.0, float(data.get('secondary_logo_x', 0.15))))
                merged_config['secondary_logo_y'] = max(0.0, min(1.0, float(data.get('secondary_logo_y', 0.15))))
                merged_config['secondary_logo_rotation'] = float(data.get('secondary_logo_rotation', 0)) % 360
                override_applied = True
                print(f"[PROCESS BRANDS] Override: secondary logo from brand #{data.get('secondary_logo_brand_id')}")
            
            if override_applied:
                print(f"[PROCESS BRANDS] ⚠️  Using TEMPORARY overrides for this video only (not saved to brand config)")
            else:
                print(f"[PROCESS BRANDS] Using brand defaults (no overrides)")
            
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
        
        # Increment daily branding job counter
        increment_branding_jobs(user_id)
        
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
        # --- Tier enforcement: daily fetch limit ---
        user_id = session.get('user_id')
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        usage = get_daily_usage(user_id)
        
        if not YoutubeDL:
            return jsonify({'success': False, 'error': 'yt-dlp not installed'}), 500
        
        data = request.get_json(force=True) or {}
        urls = data.get('urls') or []
        
        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({'success': False, 'error': 'Provide JSON: {"urls": ["url1", "url2", ...]}'}), 400
        
        if len(urls) > 5:
            return jsonify({'success': False, 'error': 'Maximum 5 URLs at a time (Render free tier limit)'}), 400
        
        # Check if user would exceed daily fetch limit
        remaining_fetches = limits['fetches_per_day'] - usage['downloads']
        if remaining_fetches <= 0:
            return jsonify({
                'success': False,
                'error': 'DAILY_LIMIT_REACHED',
                'message': f"You've used all {limits['fetches_per_day']} fetches for today. Resets at midnight UTC.",
                'tier': tier,
                'limit': limits['fetches_per_day'],
                'used': usage['downloads'],
            }), 403
        
        # Clamp to remaining quota
        if len(urls) > remaining_fetches:
            urls = urls[:remaining_fetches]
        
        print(f"[FETCH] Downloading {len(urls)} videos from URLs")
        log_event('info', None, f'Fetching {len(urls)} URLs')
        
        def download_one(url_input):
            try:
                # Configure yt_dlp with platform-specific options
                is_instagram = 'instagram.com' in url_input.lower()
                is_tiktok = 'tiktok.com' in url_input.lower()
                
                ydl_opts = {
                    'outtmpl': os.path.join(RAW_DIR, '%(id)s.%(ext)s'),
                    'merge_output_format': 'mp4',
                    'format': 'bv*+ba/best',
                    'prefer_ffmpeg': True,
                    'retries': 5,
                    'fragment_retries': 5,
                    'socket_timeout': 300,
                }
                
                # Apply Instagram-specific headers only for Instagram URLs
                if is_instagram:
                    ydl_opts['http_headers'] = {
                        'User-Agent': 'Instagram 271.1.0.21.84 Android',
                        'X-IG-App-ID': '567067343352427',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Accept-Encoding': 'gzip, deflate',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                
                # Apply TikTok impersonation for TikTok URLs
                # Note: impersonation requires curl_cffi and specific target format
                # Temporarily disabled until proper integration is tested
                # if is_tiktok:
                #     ydl_opts['impersonate'] = ('chrome', '110', 'windows')
                
                # Only add cookiefile if the file exists and is readable
                from .config import COOKIE_FILE as cookie_file
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
                # Derive display name from yt-dlp title or filename
                video_title = info.get('title', '') if info else ''
                default_display_name = video_title if video_title else name.rsplit('.', 1)[0]
                
                return {
                    'url': url_input,
                    'filename': name,
                    'display_name': default_display_name,
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
        
        # Increment daily download counter for successful downloads
        if success_count > 0:
            increment_downloads(user_id, success_count)
        
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
            'wm_mode': brand.get('wm_mode', 'positioned'),
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
            'logo_rotation': brand.get('logo_rotation', 0.0),
            
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
        from .database import get_all_brands, get_user_brand_count
        
        # IMPORTANT: user_id comes from session, NOT from query params (security)
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        # Parse include_system from query (default false for SaaS)
        include_system = request.args.get('include_system', 'false').lower() == 'true'
        
        # Get user's brands (exclude system brands by default)
        brands = get_all_brands(user_id=user_id, include_system=include_system)
        
        # Include tier info so frontend can update button state dynamically
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        max_brands = limits.get('max_brand_configs', 1)
        current_count = get_user_brand_count(user_id)
        can_create = (max_brands == -1) or (current_count < max_brands)
        
        return jsonify({
            'success': True,
            'brands': brands,
            'count': len(brands),
            'tier': tier,
            'max_brands': max_brands,
            'can_create': can_create,
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
        from .database import create_brand, get_user_brand_count
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
        
        # --- Tier enforcement: check max_brand_configs ---
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        max_brands = limits.get('max_brand_configs', 1)
        
        if max_brands != -1:  # -1 means unlimited
            current_count = get_user_brand_count(user_id)
            if current_count >= max_brands:
                print(f"[BRANDS] Tier limit reached for user #{user_id}: {current_count}/{max_brands} ({tier})")
                return jsonify({
                    'success': False,
                    'error': f"You've reached your {tier} brand limit ({max_brands}). Upgrade your plan to create more brands.",
                    'code': 'TIER_LIMIT_REACHED'
                }), 403
        
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
            logo_rotation=data.get('logo_rotation', 0.0),
            wm_mode=data.get('wm_mode', 'positioned'),
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
            print(f"[BRANDS ERROR] DATABASE STILL LOCKED after retries while updating brand #{brand_id}: {e}")
            print(f"[BRANDS ERROR] This should be rare with retry logic. Check for concurrent requests.")
            return jsonify({
                'success': False, 
                'error': 'Database busy, please retry',
                'code': 'DB_LOCKED',
                'fix': 'Wait 2 seconds and click Save again'
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
        bg_strength = int(request.form.get('bg_strength', 50))  # 0-150
        
        norm_result = normalize_logo(
            original_path, 
            normalized_path,
            max_dimension=1024,
            remove_bg=remove_bg,
            bg_strength=bg_strength
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
        bg_strength = int(request.form.get('bg_strength', 50))  # 0-150
        
        norm_result = normalize_logo(
            original_path, 
            normalized_path,
            max_dimension=2048,  # Watermarks can be larger
            remove_bg=remove_bg,
            bg_strength=bg_strength
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
    display_name = data.get('display_name')  # Optional UI display name
    
    if not all([source_url, filename, file_path]):
        return jsonify({'error': 'source_url, filename, and file_path are required'}), 400
    
    user_id = session['user_id']
    
    # Verify file exists
    if not os.path.exists(file_path):
        return jsonify({'error': 'File does not exist at specified path'}), 400
    
    download_id = save_download(user_id, source_url, filename, file_path, display_name)
    
    return jsonify({
        'success': True,
        'download_id': download_id,
        'message': 'Download saved successfully'
    })


@app.route('/api/videos/upload', methods=['POST'])
@login_required
def upload_video():
    """Upload a video file directly to RAW_DIR"""
    from .database import save_download
    from .config import RAW_DIR, ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE
    import os
    import uuid
    
    # Check if file is present
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    # Check if file has a name
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Validate file extension
    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400
    
    # Generate safe unique filename
    safe_filename = f"{uuid.uuid4().hex}.{ext}"
    file_path = os.path.join(RAW_DIR, safe_filename)
    
    # Save the file
    try:
        file.save(file_path)
    except Exception as e:
        return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
    
    # Verify file was saved and check size
    if not os.path.exists(file_path):
        return jsonify({'error': 'File was not saved successfully'}), 500
    
    file_size = os.path.getsize(file_path)
    if file_size > MAX_UPLOAD_SIZE:
        os.remove(file_path)
        return jsonify({'error': f'File exceeds maximum size of {MAX_UPLOAD_SIZE // (1024*1024)}MB'}), 400
    
    # Save download record so it appears in Library
    user_id = session['user_id']
    source_url = f"upload://{file.filename}"  # Mark as upload source
    # Use original filename as display_name for uploads
    display_name = file.filename
    download_id = save_download(user_id, source_url, safe_filename, file_path, display_name)
    
    return jsonify({
        'success': True,
        'filename': safe_filename,  # Internal filename (source of truth)
        'display_name': display_name,  # UI display name
        'download_id': download_id,
        'message': 'File uploaded successfully'
    })

@app.route('/api/downloads/<int:download_id>/rename', methods=['PUT'])
@login_required
def rename_download(download_id):
    """Rename a download's display_name (UI-only, doesn't change disk filename)"""
    from .database import update_display_name
    
    data = request.get_json(force=True) or {}
    display_name = data.get('display_name')
    
    if not display_name or not display_name.strip():
        return jsonify({'error': 'display_name is required'}), 400
    
    user_id = session['user_id']
    success = update_display_name(download_id, user_id, display_name.strip())
    
    if not success:
        return jsonify({'error': 'Download not found or access denied'}), 404
    
    return jsonify({
        'success': True,
        'download_id': download_id,
        'display_name': display_name.strip(),
        'message': 'Display name updated successfully'
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
        # --- Tier enforcement: daily fetch limit ---
        user_id = session.get('user_id')
        if user_id:
            tier = get_user_tier(user_id)
            dl_limits = get_effective_limits(tier, get_user_special_status(user_id))
            usage = get_daily_usage(user_id)
            if usage['downloads'] >= dl_limits['fetches_per_day']:
                return jsonify({
                    'success': False,
                    'error': 'DAILY_LIMIT_REACHED',
                    'message': f"You've used all {dl_limits['fetches_per_day']} fetches for today.",
                    'tier': tier,
                }), 403

        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Import and run the async download function
        import asyncio
        from downloader.batch_downloader import download_single_video
        result = asyncio.run(download_single_video(url))
        
        # Increment download counter on success
        if user_id and result.get('success', True):
            increment_downloads(user_id)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/batch', methods=['POST'])
def api_download_batch():
    """Download multiple videos."""
    try:
        # --- Tier enforcement: daily fetch limit ---
        user_id = session.get('user_id')
        remaining = None
        if user_id:
            tier = get_user_tier(user_id)
            dl_limits = get_effective_limits(tier, get_user_special_status(user_id))
            usage = get_daily_usage(user_id)
            remaining = dl_limits['fetches_per_day'] - usage['downloads']
            if remaining <= 0:
                return jsonify({
                    'success': False,
                    'error': 'DAILY_LIMIT_REACHED',
                    'message': f"You've used all {dl_limits['fetches_per_day']} fetches for today.",
                    'tier': tier,
                }), 403

        data = request.get_json()
        urls = data.get('urls', [])
        
        if not urls:
            return jsonify({"error": "At least one URL is required"}), 400
        
        # Clamp to remaining quota if logged in
        if remaining is not None and len(urls) > remaining:
            urls = urls[:remaining]
        
        # Import and run the async batch download function
        import asyncio
        from downloader.batch_downloader import download_batch
        results = asyncio.run(download_batch(urls))
        
        # Increment download counter for successful downloads
        if user_id:
            success_count = sum(1 for r in results if r.get('success', True))
            if success_count > 0:
                increment_downloads(user_id, success_count)
        
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
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
schedule_cleanup()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
