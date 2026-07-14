"""
Brandr - Flask Application
"""
from flask import Flask, request, jsonify, render_template, render_template_string, send_from_directory, send_file, redirect, url_for, session, flash, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import io
import json
import uuid
import time
import zipfile
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash as _wz_check
import subprocess
import tempfile
import threading
from yt_dlp import YoutubeDL
import hashlib
import sqlite3
from datetime import datetime
from functools import wraps
import shutil
import re

# --- ffmpeg detection: find ffmpeg and set yt-dlp format accordingly ---
_WINGET_FFMPEG = os.path.expandvars(
    r'%LOCALAPPDATA%\Microsoft\WinGet\Packages'
    r'\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe'
    r'\ffmpeg-8.1-full_build\bin'
)

def _find_ffmpeg_location():
    """Return the directory containing ffmpeg, or None."""
    # 1. Already on PATH?
    if shutil.which('ffmpeg'):
        return os.path.dirname(shutil.which('ffmpeg'))
    # 2. Winget default install location
    if os.path.isfile(os.path.join(_WINGET_FFMPEG, 'ffmpeg.exe')):
        return _WINGET_FFMPEG
    return None

FFMPEG_DIR = _find_ffmpeg_location()
HAS_FFMPEG = FFMPEG_DIR is not None

# If ffmpeg is found but not on PATH, inject it so yt-dlp / subprocess can find it
if HAS_FFMPEG and not shutil.which('ffmpeg'):
    os.environ['PATH'] = FFMPEG_DIR + os.pathsep + os.environ.get('PATH', '')
    print(f"[INIT] Added ffmpeg to PATH from: {FFMPEG_DIR}")

# yt-dlp format string: use merge (best quality) when ffmpeg is available,
# otherwise fall back to best single pre-muxed format.
YTDLP_FORMAT = 'bv*+ba/b' if HAS_FFMPEG else 'b/best'
print(f"[INIT] ffmpeg={'found' if HAS_FFMPEG else 'MISSING'}, yt-dlp format='{YTDLP_FORMAT}'")

def _strip_ansi(text):
    """Remove ANSI escape codes from a string."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


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
    get_credit_balance, spend_credits, set_subscription_credits,
    add_earned_credits, add_purchased_credits,
    log_render_event, get_render_stats, get_user_render_stats,
    get_user_special_status, set_user_special_status,
    create_waitlist_entry, get_waitlist_entry_by_email,
    get_pending_waitlist_entries, get_all_waitlist_entries, get_waitlist_counts,
    approve_waitlist_entry, claim_waitlist_entry, set_waitlist_entry_status,
    user_can_download_filename, save_branded_output, get_connection,
    init_invite_codes, create_invite_code, get_invite_code, redeem_invite_code,
    create_referral_code, get_referral_code, credit_referral_reward,
    get_all_invite_codes, get_all_referral_codes,
    init_source_edits, get_source_edit, upsert_source_edit, SOURCE_EDIT_DEFAULTS,
)


# Authentication functions

def hash_password(password):
    """Hash a password using werkzeug (bcrypt-style pbkdf2).
    Replaces the old SHA-256 implementation — new registrations and password
    changes always produce a werkzeug hash.  Legacy SHA-256 hashes in existing
    accounts are detected and upgraded on first successful login."""
    return generate_password_hash(password)


def _is_legacy_sha256_hash(h):
    """Return True if h looks like a raw SHA-256 hex digest (64 hex chars)."""
    return bool(h and len(h) == 64 and all(c in '0123456789abcdef' for c in h.lower()))


def _verify_password(password, stored_hash):
    """Verify a password against either a werkzeug hash or a legacy SHA-256 hash.
    Returns (is_valid, needs_upgrade).  needs_upgrade is True when the stored hash
    is legacy and the caller should re-hash with werkzeug and persist it."""
    if _is_legacy_sha256_hash(stored_hash):
        legacy = hashlib.sha256(password.encode()).hexdigest()
        if legacy == stored_hash:
            return True, True   # correct legacy hash → needs upgrade
        return False, False
    # Modern werkzeug hash
    return _wz_check(stored_hash, password), False


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

        is_valid, needs_upgrade = _verify_password(password, stored_hash)
        if is_valid:
            if needs_upgrade:
                # Silently upgrade legacy SHA-256 hash to werkzeug on successful login
                try:
                    from .database import get_connection as _gc
                    new_hash = generate_password_hash(password)
                    with _gc() as _conn:
                        _conn.execute(
                            'UPDATE users SET password_hash = ? WHERE id = ?',
                            (new_hash, user_id)
                        )
                        _conn.commit()
                    print(f"[AUTH] Upgraded legacy SHA-256 hash for user_id={user_id}", flush=True)
                except Exception as _ue:
                    print(f"[AUTH] Warning: hash upgrade failed for user_id={user_id}: {_ue}", flush=True)
            if must_change:
                return user_id, 'must_change_password'
            return user_id, None
    return None, None


def register_user(email, password, beta_entry=None):
    """Register a new user. Admin emails default to Platinum tier.
    beta_entry should be pre-fetched by the caller (register()) to avoid a
    redundant DB round-trip. Package application is best-effort — failures
    are logged but never block account creation."""
    from .database import get_connection
    try:
        is_admin_email = email.lower() in [e.lower() for e in ADMIN_EMAILS]

        # Determine tier: admin → Platinum, beta package → tier_grant if set, else Explorer
        print(f"[REGISTER] tier resolution start (admin={is_admin_email})", flush=True)
        if is_admin_email:
            tier = 'Platinum'
        elif beta_entry and beta_entry.get('tier_grant'):
            tier = beta_entry['tier_grant']
        else:
            tier = 'Explorer'

        special_status = None  # Never auto-assign
        print(f"[REGISTER] tier resolved: {tier}", flush=True)

        print("[REGISTER] user insert start", flush=True)
        with get_connection() as conn:
            c = conn.cursor()
            password_hash = hash_password(password)
            c.execute(
                'INSERT INTO users (email, password_hash, tier, special_status) VALUES (?, ?, ?, ?)',
                (email, password_hash, tier, special_status)
            )
            conn.commit()
            user_id = c.lastrowid
        print(f"[REGISTER] user insert done: user_id={user_id}", flush=True)

        print(f"[AUTH] Registered user: {email} tier={tier}", flush=True)

        # Apply optional beta package fields (best-effort — never blocks registration)
        if beta_entry:
            print("[REGISTER] apply beta package start", flush=True)
            try:
                _apply_beta_package(user_id, beta_entry)
            except Exception as _pkg_err:
                print(f"[AUTH] Warning: beta package apply failed for user={user_id}: {_pkg_err}", flush=True)
            print("[REGISTER] apply beta package done", flush=True)

            print("[REGISTER] claim waitlist start", flush=True)
            try:
                claim_waitlist_entry(beta_entry['id'], user_id)
            except Exception as _claim_err:
                print(f"[AUTH] Warning: claim_waitlist_entry failed for id={beta_entry['id']}: {_claim_err}", flush=True)
            print("[REGISTER] claim waitlist done", flush=True)

        return user_id
    except sqlite3.IntegrityError:
        print(f"[REGISTER] IntegrityError — duplicate email: {email}", flush=True)
        return None
    except Exception as _reg_err:
        import traceback as _tb
        print(f"[REGISTER] Unexpected error in register_user: {_reg_err}\n{_tb.format_exc()}", flush=True)
        return None


def _apply_beta_package(user_id, beta_entry):
    """Apply founding/bonus fields from a beta_access row to the user record.
    All fields are optional — only written if present in the row.
    Does not touch tier enforcement logic."""
    from .database import get_connection
    updates = {}
    if beta_entry.get('founding_status'):
        updates['founding_status'] = 1
        updates['founding_status_granted_at'] = datetime.utcnow().isoformat()
    if beta_entry.get('founding_discount_percent') is not None:
        updates['founding_discount_percent'] = beta_entry['founding_discount_percent']
    if beta_entry.get('bonus_tier_until'):
        updates['bonus_tier_until'] = beta_entry['bonus_tier_until']
    if not updates:
        return
    set_clause = ', '.join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
    print(f"[AUTH] Beta package applied to user={user_id}: {list(updates.keys())}")


def get_user_tier(user_id):
    """Get a user's tier from the database. Returns tier name string.
    On OperationalError (e.g. disk I/O error, disk full) logs server-side
    and returns DEFAULT_TIER as a safe fallback so callers don't crash."""
    import traceback as _tb
    from .database import get_connection
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT tier FROM users WHERE id = ?', (user_id,))
            row = c.fetchone()
        if row and row['tier']:
            return row['tier']
        return DEFAULT_TIER
    except sqlite3.OperationalError as e:
        print(f"[GET_USER_TIER] DB OperationalError for user_id={user_id}: {e}")
        print(f"[GET_USER_TIER] Full traceback:\n{_tb.format_exc()}")
        _log_disk_health_warning()
        return DEFAULT_TIER
    except Exception as e:
        print(f"[GET_USER_TIER] Unexpected error for user_id={user_id}: {e}")
        print(f"[GET_USER_TIER] Full traceback:\n{_tb.format_exc()}")
        return DEFAULT_TIER


def _log_disk_health_warning():
    """Fire-and-forget disk health snapshot, called when a DB error is caught."""
    try:
        import shutil, sys
        from .config import DB_PATH
        db_dir = os.path.dirname(DB_PATH) or '.'
        usage = shutil.disk_usage(db_dir)
        free_mb = usage.free / 1024 / 1024
        used_pct = (usage.used / usage.total) * 100
        print(
            f"[DISK HEALTH] free={free_mb:.1f}MB used={used_pct:.1f}% path={DB_PATH}",
            file=sys.stderr,
        )
        if free_mb < 200:
            print(
                f"[DISK HEALTH] CRITICAL: only {free_mb:.1f}MB free — "
                "disk likely full. Clear outputs dir or upgrade Render plan.",
                file=sys.stderr,
            )
    except Exception as _e:
        print(f"[DISK HEALTH] health check itself failed: {_e}")


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
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

# Startup diagnostics
print(f"[STARTUP] LOOPS_API_KEY={'SET' if os.environ.get('LOOPS_API_KEY') else 'MISSING'}", flush=True)

# P1 fix: rate limiter — in-memory storage safe because WEB_CONCURRENCY=1
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],          # no global limit; only apply where decorated
    storage_uri='memory://',
)

# P1 fix: startup warning if running with the insecure default secret key
if SECRET_KEY == 'dev-secret-key-change-in-production':
    import warnings
    warnings.warn(
        '[SECURITY] WTF_SECRET_KEY is set to the default dev value. '
        'Set WTF_SECRET_KEY in your environment before exposing to users.',
        stacklevel=2,
    )

# Initialize databases
from .database import init_db, init_founding_slots
init_db()
init_users_db()
init_founding_slots()
init_invite_codes()
init_source_edits()


@app.context_processor
def inject_global_context():
    """Inject admin flag, tier, badge info, feature gates, and founding slots into all templates."""
    from .database import get_all_founding_slots
    from .config import FOUNDING_MEMBER_CONFIG, FOUNDING_PAYMENT_LINKS as _fpl
    max_slots = FOUNDING_MEMBER_CONFIG.get('max_slots_per_tier', 100)
    slots_used = get_all_founding_slots()
    founding_slots_remaining = {t: max(0, max_slots - slots_used.get(t, 0))
                                 for t in FOUNDING_MEMBER_CONFIG.get('eligible_tiers', [])}
    ctx = {'is_admin_user': is_admin(), 'tier': DEFAULT_TIER,
           'theme_tier': DEFAULT_TIER,
           'founding_status': 0,
           'batch_link_limit': TIER_CONFIG.get(DEFAULT_TIER, {}).get('batch_link_limit', 5),
           'tier_features': get_tier_features(DEFAULT_TIER),
           'all_tier_features': TIER_FEATURES,
           'all_tier_config': TIER_CONFIG,
           'founding_slots_remaining': founding_slots_remaining,
           'founding_max_slots': max_slots,
           'founding_payment_links': _fpl,
           }
    user_id = session.get('user_id')
    if user_id:
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        badge = get_badge_info(tier, special_status)
        ctx['tier'] = tier
        ctx['user_badge'] = badge
        ctx['user_special_status'] = special_status
        ctx['tier_features'] = get_tier_features(tier)
        ctx['batch_link_limit'] = get_effective_limits(tier, special_status).get('batch_link_limit', 5)
        # Founding members get the GOLD theme, overriding their base-tier colour
        # entirely (theme_tier='Founding'). founding_status is an account marker,
        # not a tier — so a founding Creator still has Creator features but a gold UI.
        founding = 0
        try:
            from .database import get_connection
            with get_connection() as conn:
                row = conn.execute(
                    'SELECT COALESCE(founding_status, 0) AS fs FROM users WHERE id = ?',
                    (user_id,)
                ).fetchone()
                founding = row['fs'] if row else 0
        except Exception as _e:
            print(f"[THEME] founding_status fetch failed for user={user_id}: {_e}")
            founding = 0
        ctx['founding_status'] = founding
        ctx['theme_tier'] = 'Founding' if founding else tier
    return ctx


# Authentication routes
@app.route('/portal/register', methods=['GET', 'POST'])
@limiter.limit('5 per minute')
def register():
    if session.get('user_id'):
        return redirect(url_for('brand_video'))
    if request.method == 'POST':
        print("[REGISTER] POST received", flush=True)
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        invite_code_str = (request.form.get('invite_code') or '').strip().upper()
        print(f"[REGISTER] email parsed: {email}", flush=True)

        if not email or not password:
            flash('Email and password are required.', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('register.html')

        # ── INVITE CODE CHECK ──────────────────────────────────────────────
        invite_code_entry = None
        if invite_code_str:
            invite_code_entry = get_invite_code(invite_code_str)
            if not invite_code_entry:
                flash('That invite code is not valid.', 'error')
                return render_template('register.html')
            if invite_code_entry.get('used_by_user_id'):
                flash('That invite code has already been used.', 'error')
                return render_template('register.html')
            print(f"[REGISTER] valid invite code: {invite_code_str}", flush=True)

        # ── REGISTRATION GATE ──────────────────────────────────────────────
        # Admin emails, valid invite codes, and approved beta_access entries may register.
        is_admin_email = email.lower() in [e.lower() for e in ADMIN_EMAILS]
        entry = None
        if not is_admin_email and not invite_code_entry:
            print("[REGISTER] beta lookup start", flush=True)
            try:
                entry = get_waitlist_entry_by_email(email)
            except Exception as _e:
                import traceback as _tb
                print(f"[REGISTER] DB error checking beta_access for {email}: {_tb.format_exc()}", flush=True)
                entry = None
            print(f"[REGISTER] beta lookup done: status={entry.get('status') if entry else None}", flush=True)

            if not entry or entry.get('status') != 'approved':
                print(f"[REGISTER] gate blocked — not approved", flush=True)
                flash(
                    'Brandr is currently invite-only. '
                    'Join the beta waitlist to request access.',
                    'error'
                )
                return render_template('register.html', blocked=True)
        # ── END GATE ───────────────────────────────────────────────────────

        print("[REGISTER] gate passed — calling register_user", flush=True)
        try:
            user_id = register_user(email, password, beta_entry=entry)
        except Exception as _ru_err:
            import traceback as _tb
            print(f"[REGISTER] register_user raised unexpectedly: {_ru_err}\n{_tb.format_exc()}", flush=True)
            flash('Brandr could not complete registration right now. Please try again shortly.', 'error')
            return render_template('register.html')

        print(f"[REGISTER] register_user returned: user_id={user_id}", flush=True)
        if user_id:
            # Apply invite code grants if used
            if invite_code_entry:
                try:
                    from datetime import timedelta
                    bonus_until = (datetime.utcnow() + timedelta(days=30 * invite_code_entry['grants_months'])).isoformat()
                    fs_at = datetime.utcnow().isoformat() if invite_code_entry['grants_founding_status'] else None
                    with get_connection() as _conn:
                        _conn.execute(
                            '''UPDATE users SET tier = ?, bonus_tier_until = ?,
                               founding_status = ?, founding_status_granted_at = ?
                               WHERE id = ?''',
                            (invite_code_entry['grants_tier'], bonus_until,
                             invite_code_entry['grants_founding_status'], fs_at, user_id)
                        )
                        _conn.commit()
                    redeem_invite_code(invite_code_str, user_id)
                    print(f"[REGISTER] Invite code applied: tier={invite_code_entry['grants_tier']} until={bonus_until}", flush=True)
                except Exception as _ic_err:
                    print(f"[REGISTER] Warning: invite code apply failed: {_ic_err}", flush=True)

            # Referral attribution — record which code brought this user in.
            # Automatic reward (credit_referral_reward) is DISABLED during beta:
            # bonus_tier_until mutations require a redemption audit trail first.
            if entry and entry.get('referral_code_used'):
                print(f"[REGISTER] Referral attribution recorded: code={entry['referral_code_used']} user={user_id}", flush=True)

            session['user_id'] = user_id
            session['email'] = email
            flash('Welcome to Brandr! Your account is ready.', 'success')
            print("[REGISTER] redirecting to brands_page", flush=True)
            return redirect(url_for('brands_page'))
        else:
            flash('An account with this email already exists.', 'error')

    return render_template('register.html')

@app.route('/portal/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
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
            return redirect(url_for('brands_page'))
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


# ── Admin: Beta Waitlist ──

_WAITLIST_STATUSES = {'pending', 'approved', 'claimed', 'rejected', 'spam'}


@app.route('/portal/admin/waitlist')
@admin_required
def admin_waitlist():
    """Admin view: all waitlist entries with optional ?status= filter and summary counts."""
    import traceback as _tb

    status_filter = request.args.get('status', '').strip().lower() or None
    if status_filter and status_filter not in _WAITLIST_STATUSES:
        status_filter = None  # ignore unrecognised filter values

    try:
        entries = get_all_waitlist_entries(status=status_filter)
    except Exception:
        print(f"[ADMIN WAITLIST] Error fetching entries: {_tb.format_exc()}")
        entries = []

    try:
        counts = get_waitlist_counts()
    except Exception:
        print(f"[ADMIN WAITLIST] Error fetching counts: {_tb.format_exc()}")
        counts = {'total': 0, 'pending': 0, 'approved': 0,
                  'claimed': 0, 'rejected': 0, 'spam': 0}

    return render_template('admin_waitlist.html',
                           entries=entries,
                           counts=counts,
                           active_filter=status_filter or 'all')


@app.route('/portal/admin/waitlist/export')
@admin_required
def admin_waitlist_export():
    """Export all waitlist entries as a UTF-8 CSV download."""
    import csv, io, traceback as _tb
    from datetime import datetime as _dt

    try:
        entries = get_all_waitlist_entries()
    except Exception:
        print(f"[ADMIN WAITLIST] Export error: {_tb.format_exc()}")
        return jsonify({'success': False, 'error': 'Could not fetch waitlist data.'}), 500

    fields = [
        'id', 'email', 'creator_name', 'main_platform', 'creator_type',
        'page_count', 'referral_code_used', 'discord_username', 'status',
        'access_level', 'tier_grant', 'founding_status', 'founding_discount_percent',
        'referred_by_user_id', 'claimed_user_id', 'created_at',
        'approved_at', 'approved_by', 'claimed_at', 'notes',
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for entry in entries:
        writer.writerow({k: (entry.get(k) if entry.get(k) is not None else '') for k in fields})

    filename = f"brandr_waitlist_{_dt.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    print(f"[ADMIN WAITLIST] CSV export: {len(entries)} rows by {session.get('email')}")
    return Response(
        buf.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/portal/admin/waitlist/<int:entry_id>/approve', methods=['POST'])
@admin_required
def admin_waitlist_approve(entry_id):
    """Admin action: approve a pending beta waitlist entry."""
    import traceback as _tb
    try:
        # Fetch email before approving so we can fire the Loops event
        from .database import get_connection as _gc
        with _gc() as _conn:
            _row = _conn.execute('SELECT email FROM beta_access WHERE id=?', (entry_id,)).fetchone()
        entry_email = _row['email'] if _row else None

        success = approve_waitlist_entry(entry_id, session.get('email', 'admin'))
        if success:
            print(f"[ADMIN WAITLIST] Approved entry id={entry_id} by {session.get('email')}")
            if entry_email:
                import threading
                def _loops_approve_tasks(email):
                    _loops_send_event(email, 'beta_approved')
                    _loops_update_contact(email, foundingStatus=True, userGroup='founding_member')
                threading.Thread(
                    target=_loops_approve_tasks,
                    args=(entry_email,),
                    daemon=True,
                ).start()
            return jsonify({'success': True, 'status': 'approved'})
        return jsonify({'success': False, 'error': 'Entry not found or already actioned.'}), 404
    except Exception as _e:
        print(f"[ADMIN WAITLIST] Approve error for id={entry_id}: {_tb.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error approving entry.'}), 500


@app.route('/portal/admin/waitlist/<int:entry_id>/status', methods=['POST'])
@admin_required
def admin_waitlist_set_status(entry_id):
    """Admin action: set status to rejected or spam (or reset to pending)."""
    import traceback as _tb
    try:
        data = request.get_json(force=True) or {}
        new_status = (data.get('status') or '').strip().lower()
        if new_status not in _WAITLIST_STATUSES:
            return jsonify({
                'success': False,
                'error': f"Invalid status '{new_status}'. Allowed: {sorted(_WAITLIST_STATUSES)}"
            }), 400

        success = set_waitlist_entry_status(entry_id, new_status, session.get('email', 'admin'))
        if success:
            print(f"[ADMIN WAITLIST] id={entry_id} → '{new_status}' by {session.get('email')}")
            return jsonify({'success': True, 'status': new_status})
        return jsonify({'success': False, 'error': 'Entry not found.'}), 404
    except Exception:
        print(f"[ADMIN WAITLIST] Set-status error for id={entry_id}: {_tb.format_exc()}")
        return jsonify({'success': False, 'error': 'Server error.'}), 500


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


# ── Admin API: Purge User (hard delete — frees email for re-registration) ──
@app.route('/api/admin/purge-user', methods=['POST'])
@admin_required
def admin_purge_user():
    """Hard-delete a user and all their data so the email can be used to re-register."""
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    confirmation = data.get('confirmation', '')

    if not user_id:
        return jsonify({'success': False, 'error': 'user_id required'}), 400

    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT email FROM users WHERE id = ?', (user_id,))
        target = c.fetchone()
        if not target:
            return jsonify({'success': False, 'error': 'User not found'}), 404

    target_email = target['email']

    if confirmation != f'PURGE {target_email}':
        return jsonify({'success': False, 'error': f'Type exactly: PURGE {target_email}'}), 400

    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'error': 'Cannot purge your own account'}), 400

    if target_email.lower() in [e.lower() for e in ADMIN_EMAILS]:
        return jsonify({'success': False, 'error': 'Cannot purge an admin account'}), 400

    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT name FROM brands WHERE user_id = ? AND is_system = 0', (user_id,))
        brand_names = [row['name'] for row in c.fetchall()]
        if brand_names:
            placeholders = ','.join('?' * len(brand_names))
            c.execute(f'DELETE FROM brand_configs WHERE brand_name IN ({placeholders})', brand_names)
        c.execute('DELETE FROM brands WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM branded_outputs WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM downloads WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM daily_usage WHERE user_id = ?', (user_id,))
        # Free the email so they can re-register via waitlist
        c.execute('DELETE FROM beta_access WHERE LOWER(email) = LOWER(?)', (target_email,))
        c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()

    print(f"[ADMIN] Purged user {user_id} ({target_email}) by {session.get('email')}")
    return jsonify({'success': True, 'email': target_email})


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

    try:
        special_status = get_user_special_status(user_id)
    except Exception as _e:
        print(f"[DASHBOARD] get_user_special_status failed for user={user_id}: {_e}")
        special_status = None

    limits = get_effective_limits(tier, special_status)
    max_brands = limits.get('max_brand_configs', 1)

    try:
        brand_count = get_user_brand_count(user_id) if user_id else 0
    except Exception as _e:
        print(f"[DASHBOARD] get_user_brand_count failed for user={user_id}: {_e}")
        brand_count = 0

    can_create = (max_brands == -1) or (brand_count < max_brands)

    # Daily usage counters
    try:
        usage = get_daily_usage(user_id) if user_id else {'branding_jobs': 0, 'downloads': 0}
    except Exception as _e:
        print(f"[DASHBOARD] get_daily_usage failed for user={user_id}: {_e}")
        usage = {'branding_jobs': 0, 'downloads': 0}

    # Credit balance (1 credit = 1 render). Fail-open to full allowance shape.
    credits_per_day = limits.get('credits_per_day', 0)
    try:
        credits = get_credit_balance(user_id, credits_per_day) if user_id else {
            'subscription': 0, 'earned': 0, 'purchased': 0, 'total': 0}
    except Exception as _e:
        print(f"[DASHBOARD] get_credit_balance failed for user={user_id}: {_e}")
        credits = {'subscription': credits_per_day, 'earned': 0, 'purchased': 0, 'total': credits_per_day}

    try:
        from .database import get_connection
        with get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT COALESCE(founding_status, 0) as founding_status FROM users WHERE id = ?', (user_id,))
            row = c.fetchone()
            founding_status = row['founding_status'] if row else 0
    except Exception as _e:
        print(f"[DASHBOARD] founding_status fetch failed for user={user_id}: {_e}")
        founding_status = 0

    return render_template('dashboard.html',
        state=state,
        tier=tier,
        max_brands=max_brands,
        brand_count=brand_count,
        can_create=can_create,
        usage=usage,
        limits=limits,
        credits=credits,
        credits_per_day=credits_per_day,
        founding_status=founding_status,
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
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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


@app.route('/admin/storage-health')
@admin_required
def admin_storage_health():
    """Storage health check — confirms persistent disk is mounted and writable.
    Returns paths, file counts, sizes, disk usage, and an overall pass/fail status.
    Use this to verify /var/data is correctly attached after deploys and restarts."""
    import shutil
    import os
    import time
    from .config import DB_PATH, STORAGE_ROOT, RAW_DIR, OUTPUT_DIR, BRANDS_DIR

    issues = []

    # ── Path configuration ────────────────────────────────────────────────────
    on_persistent_disk = (
        DB_PATH.startswith('/var/data') and
        STORAGE_ROOT.startswith('/var/data')
    )
    if not on_persistent_disk:
        issues.append(f'Paths NOT on /var/data — DB_PATH={DB_PATH}, STORAGE_ROOT={STORAGE_ROOT}')

    # ── DB file ───────────────────────────────────────────────────────────────
    db_exists   = os.path.isfile(DB_PATH)
    db_size     = os.path.getsize(DB_PATH) if db_exists else 0
    db_writable = os.access(os.path.dirname(DB_PATH), os.W_OK)
    if not db_exists:
        issues.append(f'DB file missing: {DB_PATH}')
    if not db_writable:
        issues.append(f'DB directory not writable: {os.path.dirname(DB_PATH)}')

    # ── Write test ────────────────────────────────────────────────────────────
    write_test_path = os.path.join(STORAGE_ROOT, '.health_write_test')
    try:
        with open(write_test_path, 'w') as f:
            f.write(str(time.time()))
        os.remove(write_test_path)
        disk_writable = True
    except Exception as wt_err:
        disk_writable = False
        issues.append(f'Disk write test failed: {wt_err}')

    # ── Directory checks ──────────────────────────────────────────────────────
    def _dir_info(path):
        if not os.path.isdir(path):
            return {'path': path, 'exists': False, 'file_count': 0, 'size_bytes': 0, 'writable': False}
        try:
            size  = sum(os.path.getsize(os.path.join(dp, fn))
                        for dp, _, fns in os.walk(path) for fn in fns)
            count = sum(len(fns) for _, _, fns in os.walk(path))
            return {'path': path, 'exists': True, 'file_count': count,
                    'size_bytes': size, 'writable': os.access(path, os.W_OK)}
        except Exception as e:
            return {'path': path, 'exists': True, 'error': str(e)}

    dirs = {
        'brands_dir':  _dir_info(BRANDS_DIR),
        'output_dir':  _dir_info(OUTPUT_DIR),
        'raw_dir':     _dir_info(RAW_DIR),
        'storage_root': _dir_info(STORAGE_ROOT),
    }
    for k, v in dirs.items():
        if not v.get('exists'):
            issues.append(f'{k} missing: {v["path"]}')

    # ── Disk usage ────────────────────────────────────────────────────────────
    try:
        du = shutil.disk_usage(STORAGE_ROOT)
        disk_usage = {
            'total_bytes': du.total,
            'used_bytes':  du.used,
            'free_bytes':  du.free,
            'used_pct':    round(du.used / du.total * 100, 1) if du.total else 0,
        }
        if du.free < 50 * 1024 * 1024:   # warn if < 50 MB free
            issues.append(f'Low disk space: {du.free // (1024*1024)} MB free')
    except Exception as du_err:
        disk_usage = {'error': str(du_err)}

    # ── Overall status ────────────────────────────────────────────────────────
    overall = 'fail' if issues else 'ok'

    return jsonify({
        'overall': overall,
        'issues': issues,
        'config': {
            'db_path':      DB_PATH,
            'storage_root': STORAGE_ROOT,
            'brands_dir':   BRANDS_DIR,
            'output_dir':   OUTPUT_DIR,
            'raw_dir':      RAW_DIR,
            'on_persistent_disk': on_persistent_disk,
        },
        'db': {
            'path':     DB_PATH,
            'exists':   db_exists,
            'size_bytes': db_size,
            'dir_writable': db_writable,
        },
        'disk_writable': disk_writable,
        'dirs': dirs,
        'disk_usage': disk_usage,
        'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    })


@app.route("/__debug_brands")
@admin_required
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
@admin_required
def debug_health():
    """Deep health check — admin/debug use only, not called by Render health checks.
    Includes PRAGMA integrity_check (full DB page scan — can take seconds on large DBs)
    and directory write-test probes. Never link this publicly."""
    import os
    from .config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR
    from .database import run_db_integrity_check

    # Directory writability probes
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

    # Full DB integrity scan (moved here from /health and startup _log_db_health)
    integrity = run_db_integrity_check()

    all_healthy = all(status == 'OK' for status in health_checks.values())

    return jsonify({
        'status': 'healthy' if all_healthy else 'unhealthy',
        'checks': health_checks,
        'db_integrity': integrity,
    })

# Global conversion lock - only one FFmpeg process at a time (Render free tier 512MB RAM)
conversion_lock = threading.Lock()
conversion_in_progress = {'active': False, 'start_time': None}

# Job status dictionary for async watermark conversions
watermark_jobs = {}

# Job status dictionary for async brand render jobs (Phase 18)
# Keyed by job_id (uuid). Each entry:
#   status:       'queued'|'processing'|'completed'|'failed'
#   user_id:      int   — ownership check on poll
#   brand_name:   str   — label for the frontend pill
#   created_at:   float
#   started_at:   float|None
#   completed_at: float|None
#   outputs:      list|None — same shape as old synchronous response['outputs']
#   error:        str|None
brand_render_jobs = {}

# ============================================================================

@app.route('/')
def index():
    """Root: logged-in users go to Create, logged-out visitors go to /beta."""
    if 'user_id' in session:
        return redirect(url_for('brand_video'))
    return redirect(url_for('beta_page'))


@app.route('/beta')
@app.route('/waitlist')
def beta_page():
    """Public beta waitlist landing page.
    Logged-in users are redirected to Create."""
    if 'user_id' in session:
        return redirect(url_for('brand_video'))
    return render_template('beta.html')


def _loops_send_event(email, event_name):
    """Fire a Loops event for an existing contact."""
    import urllib.request, urllib.error, json as _json
    api_key = os.environ.get('LOOPS_API_KEY', '')
    if not api_key:
        return
    payload = _json.dumps({'email': email, 'eventName': event_name}).encode()
    req = urllib.request.Request(
        'https://app.loops.so/api/v1/events/send',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            # Default urllib UA (Python-urllib/x.y) trips Cloudflare error 1010 in front
            # of app.loops.so, blocking the API call before it reaches Loops.
            'User-Agent': 'Mozilla/5.0 (compatible; BrandrServer/1.0; +https://brandr.online)',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[LOOPS] event '{event_name}' sent for {email} ({resp.status})")
    except urllib.error.HTTPError as _he:
        print(f"[LOOPS] event error {_he.code} for {email}: {_he.read()[:200]}")
    except Exception as _e:
        print(f"[LOOPS] event failed for {email}: {_e}")


def _loops_update_contact(email, **props):
    """Update contact properties in Loops. Creates the property if it doesn't exist."""
    import urllib.request, urllib.error, json as _json
    api_key = os.environ.get('LOOPS_API_KEY', '')
    if not api_key:
        return
    payload = _json.dumps({'email': email, **props}).encode()
    req = urllib.request.Request(
        'https://app.loops.so/api/v1/contacts/update',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            # Default urllib UA (Python-urllib/x.y) trips Cloudflare error 1010 in front
            # of app.loops.so, blocking the API call before it reaches Loops.
            'User-Agent': 'Mozilla/5.0 (compatible; BrandrServer/1.0; +https://brandr.online)',
        },
        method='PUT',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[LOOPS] contact updated: {email} {props} ({resp.status})")
    except urllib.error.HTTPError as _he:
        print(f"[LOOPS] update error {_he.code} for {email}: {_he.read()[:200]}")
    except Exception as _e:
        print(f"[LOOPS] update failed for {email}: {_e}")


def _loops_sync_contact(email, creator_name, **kwargs):
    """Add a new waitlist contact to Loops. Called synchronously from the request so
    the HTTP call is guaranteed to run and any failure is observable in the logs."""
    import urllib.request, urllib.error, json as _json
    # .strip() guards against a trailing newline/quote on the Render env value, which
    # would otherwise corrupt the Bearer token and 401 every call.
    api_key = os.environ.get('LOOPS_API_KEY', '').strip()
    if not api_key:
        print("[LOOPS] sync skipped: LOOPS_API_KEY is empty", flush=True)
        return
    first_name = creator_name.split()[0] if creator_name else ''
    payload = _json.dumps({
        'email': email,
        'firstName': first_name,
        'source': 'waitlist',
        'mainPlatform': kwargs.get('main_platform', ''),
        'creatorType': kwargs.get('creator_type', ''),
    }).encode()
    req = urllib.request.Request(
        'https://app.loops.so/api/v1/contacts/create',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            # Default urllib UA (Python-urllib/x.y) trips Cloudflare error 1010 in front
            # of app.loops.so, blocking the API call before it reaches Loops.
            'User-Agent': 'Mozilla/5.0 (compatible; BrandrServer/1.0; +https://brandr.online)',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[LOOPS] contact synced: {email} ({resp.status})", flush=True)
    except urllib.error.HTTPError as _he:
        body = ''
        try:
            body = _he.read()[:300].decode('utf-8', 'replace')
        except Exception:
            pass
        print(f"[LOOPS] sync error {_he.code} for {email}: {body}", flush=True)
    except Exception as _e:
        print(f"[LOOPS] sync failed for {email}: {repr(_e)}", flush=True)


@app.route('/api/waitlist', methods=['POST'])
def waitlist_submit():
    """Public endpoint: submit a waitlist application.
    No login required. Does not create an app account."""
    import traceback as _tb
    try:
        email = (request.form.get('email') or '').strip().lower()
        creator_name = (request.form.get('creator_name') or '').strip()
        main_platform = (request.form.get('main_platform') or '').strip()
        creator_type = (request.form.get('creator_type') or '').strip()
        page_count = (request.form.get('page_count') or '').strip()
        referral_code_used = (request.form.get('referral_code_used') or '').strip() or None
        discord_username = (request.form.get('discord_username') or '').strip() or None

        if not email or not creator_name:
            return jsonify({'success': False, 'error': 'Email and name are required.'}), 400

        _entry_id, created = create_waitlist_entry(
            email, creator_name, main_platform, creator_type,
            page_count, referral_code_used, discord_username
        )

        if created:
            print(f"[WAITLIST] New entry: {email} platform={main_platform} type={creator_type}", flush=True)
            # Synchronous (not a background thread): a single 5s-timeout HTTP call is fine
            # within the request window, guarantees the call runs, and surfaces any Loops
            # failure in the logs instead of swallowing it on a dying thread.
            _loops_sync_contact(
                email, creator_name,
                main_platform=main_platform, creator_type=creator_type,
            )
            return jsonify({'success': True, 'created': True})
        else:
            # Already on the list — friendly, not an error
            return jsonify({'success': True, 'created': False,
                            'message': "You're already on the Brandr beta list."})

    except Exception as _e:
        print(f"[WAITLIST] Error on submission: {_tb.format_exc()}")
        return jsonify({
            'success': False,
            'error': 'Something went wrong saving your application. Please try again.'
        }), 500


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
    """Deprecated: redirect to Create (which now includes full media intake)"""
    return redirect(url_for('brand_video'))

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
        # Never expose raw tracebacks to the browser — log server-side only
        return jsonify({
            'error': 'Brandr could not load your account data. Please refresh or try again shortly.',
        }), 500

@app.route('/portal/create-experiment')
@admin_required
def create_experiment():
    """Admin-only experimental Create/Composer prototype."""
    experiments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'experiments')
    return send_from_directory(experiments_dir, 'brandr_create_job_builder_prototype.html')

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
    from .database import get_connection, get_all_brands
    from datetime import datetime
    
    try:
        user_id = session.get('user_id')
        email = session.get('email', 'User')
        
        # Get user info from database
        with get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT created_at, tier, COALESCE(founding_status, 0) as founding_status FROM users WHERE id = ?', (user_id,))
            user = c.fetchone()

        # Format created date
        created_at = 'Recently'
        if user and user['created_at']:
            try:
                created_dt = datetime.fromisoformat(user['created_at'])
                created_at = created_dt.strftime('%B %Y')
            except:
                pass

        founding_status = user['founding_status'] if user else 0

        # Get tier from database via helper
        tier = get_user_tier(user_id)
        special_status = get_user_special_status(user_id)
        limits = get_effective_limits(tier, special_status)
        usage = get_daily_usage(user_id)
        credits_per_day = limits.get('credits_per_day', 0)
        try:
            credits = get_credit_balance(user_id, credits_per_day)
        except Exception:
            credits = {'subscription': credits_per_day, 'earned': 0, 'purchased': 0, 'total': credits_per_day}

        # Get actual brand count
        user_brands = get_all_brands(user_id=user_id, include_system=False)
        brand_configs = len(user_brands)

        return render_template('profile.html',
            email=email,
            created_at=created_at,
            tier=tier,
            limits=limits,
            usage=usage,
            credits=credits,
            credits_per_day=credits_per_day,
            brand_configs=brand_configs,
            founding_status=founding_status,
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
            usage={'branding_jobs': 0, 'downloads': 0},
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
                            COALESCE(u.founding_status, 0) as founding_status,
                            u.founding_status_granted_at,
                            u.bonus_tier_until,
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
    
    from datetime import datetime, timedelta
    from .config import FOUNDING_MEMBER_CONFIG
    tiers = list(TIER_CONFIG.keys())
    statuses = [''] + list(SPECIAL_STATUSES.keys())
    tier_colors = {k: v.get('color', '#555') for k, v in TIER_CONFIG.items()}
    new_cutoff = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
    eligible_founding_tiers = FOUNDING_MEMBER_CONFIG.get('eligible_tiers', [])
    return render_template('admin.html', users=users, tiers=tiers, statuses=statuses,
                           recent_actions=recent_actions, tier_colors=tier_colors,
                           new_cutoff=new_cutoff, eligible_founding_tiers=eligible_founding_tiers)


@app.route('/api/admin/set-tier', methods=['POST'])
@admin_required
def admin_set_tier():
    """Set a user's tier and/or special_status (admin only).
    Founding status is NEVER granted automatically — requires explicit grant_founder=True."""
    from .database import get_connection
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    new_tier = data.get('tier')
    new_status = data.get('special_status')  # '' or None means clear
    grant_founder = bool(data.get('grant_founder', False))

    if not user_id or not new_tier:
        return jsonify({'success': False, 'error': 'user_id and tier required'}), 400
    if new_tier not in TIER_CONFIG:
        return jsonify({'success': False, 'error': f'Invalid tier: {new_tier}'}), 400
    if new_status and new_status not in SPECIAL_STATUSES:
        return jsonify({'success': False, 'error': f'Invalid status: {new_status}'}), 400

    new_status = new_status if new_status else None

    with get_connection() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET tier = ?, special_status = ? WHERE id = ?', (new_tier, new_status, user_id))
        conn.commit()
        if c.rowcount == 0:
            return jsonify({'success': False, 'error': 'User not found'}), 404

    # Only grant founding status when explicitly requested by the admin
    from .database import get_founding_slots_used, claim_founding_slot
    from .config import FOUNDING_MEMBER_CONFIG
    founding_granted = False
    eligible = FOUNDING_MEMBER_CONFIG.get('eligible_tiers', [])
    max_slots = FOUNDING_MEMBER_CONFIG.get('max_slots_per_tier', 100)

    if grant_founder:
        if new_tier not in eligible:
            return jsonify({'success': False,
                            'error': f'{new_tier} is not eligible for Founder Status (Explorer and Elite are excluded).'}), 400
        slots_used = get_founding_slots_used(new_tier)
        if slots_used >= max_slots:
            return jsonify({'success': False,
                            'error': f'No founding slots remaining for {new_tier} ({slots_used}/{max_slots} used).'}), 400
        claim_founding_slot(new_tier, user_id)
        founding_granted = True

    admin_email = session.get('email', 'unknown')
    founder_note = ' + Founder Status granted' if founding_granted else ''
    with get_connection() as conn:
        try:
            conn.execute(
                '''INSERT INTO audit_log (admin_user_id, admin_email, action_type, target_user_id, target_email, details)
                   SELECT ?, ?, 'set_tier', ?, email,
                          'Tier → ' || ? || CASE WHEN ? THEN ' + special_status → ' || COALESCE(?, 'cleared') ELSE '' END || ?
                   FROM users WHERE id = ?''',
                (session.get('user_id'), admin_email, user_id,
                 new_tier, bool(new_status), new_status, founder_note, user_id)
            )
            conn.commit()
        except Exception:
            pass  # audit log is best-effort

    print(f"[ADMIN] set-tier user={user_id} tier={new_tier} status={new_status} founder_granted={founding_granted} by {admin_email}")
    return jsonify({'success': True, 'user_id': user_id, 'tier': new_tier,
                    'special_status': new_status, 'founding_granted': founding_granted})


@app.route('/portal/admin/codes')
@admin_required
def admin_codes():
    """Admin: manage invite and referral codes."""
    invite_codes = get_all_invite_codes()
    referral_codes = get_all_referral_codes()
    # Fetch all users for referral code owner dropdown
    with get_connection() as conn:
        users = [dict(r) for r in conn.execute(
            'SELECT id, email, tier FROM users WHERE COALESCE(account_status,\'active\') = \'active\' ORDER BY email'
        ).fetchall()]
    return render_template('admin_codes.html',
                           invite_codes=invite_codes,
                           referral_codes=referral_codes,
                           users=users,
                           tier_config=TIER_CONFIG)


@app.route('/api/admin/codes/create-invite', methods=['POST'])
@admin_required
def admin_create_invite_code():
    """Generate a new single-use invite code."""
    import secrets as _sec
    data = request.get_json(force=True) or {}
    grants_tier = data.get('grants_tier', 'Studio')
    grants_months = int(data.get('grants_months', 3))
    grants_founding = bool(data.get('grants_founding_status', True))
    notes = (data.get('notes') or '').strip() or None

    if grants_tier not in TIER_CONFIG:
        return jsonify({'success': False, 'error': f'Invalid tier: {grants_tier}'}), 400
    if grants_months < 1 or grants_months > 24:
        return jsonify({'success': False, 'error': 'months must be 1–24'}), 400

    code = 'BRANDR-' + _sec.token_hex(3).upper()
    admin_email = session.get('email', 'admin')
    ok = create_invite_code(code, grants_tier, grants_months, grants_founding, admin_email, notes)
    if ok:
        print(f"[ADMIN] invite code created: {code} tier={grants_tier} months={grants_months} by {admin_email}")
        return jsonify({'success': True, 'code': code})
    return jsonify({'success': False, 'error': 'Code generation failed (possible duplicate — try again)'}), 500


@app.route('/api/admin/codes/create-referral', methods=['POST'])
@admin_required
def admin_create_referral_code():
    """Generate a personal referral code for a user."""
    import secrets as _sec
    data = request.get_json(force=True) or {}
    owner_user_id = data.get('owner_user_id')
    reward_months = int(data.get('reward_months', 1))

    if not owner_user_id:
        return jsonify({'success': False, 'error': 'owner_user_id required'}), 400
    if reward_months < 1 or reward_months > 12:
        return jsonify({'success': False, 'error': 'reward_months must be 1–12'}), 400

    code = 'REF-' + _sec.token_hex(3).upper()
    ok = create_referral_code(code, owner_user_id, reward_months)
    if ok:
        print(f"[ADMIN] referral code created: {code} owner={owner_user_id} reward={reward_months}mo by {session.get('email')}")
        return jsonify({'success': True, 'code': code})
    return jsonify({'success': False, 'error': 'Code generation failed (possible duplicate — try again)'}), 500


@app.route('/api/admin/revoke-founder', methods=['POST'])
@admin_required
def admin_revoke_founder():
    """Remove founding status from a user and return one slot to the pool."""
    from .database import get_connection, revoke_founding_status
    data = request.get_json(force=True) or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'user_id required'}), 400

    with get_connection() as conn:
        row = conn.execute('SELECT tier, email FROM users WHERE id = ?', (user_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    tier = row['tier']
    target_email = row['email']
    revoked = revoke_founding_status(user_id, tier)

    admin_email = session.get('email', 'unknown')
    with get_connection() as conn:
        try:
            conn.execute(
                '''INSERT INTO audit_log (admin_user_id, admin_email, action_type, target_user_id, target_email, details)
                   VALUES (?, ?, 'revoke_founder', ?, ?, ?)''',
                (session.get('user_id'), admin_email, user_id, target_email,
                 f'Founder Status revoked (was on {tier} tier)' if revoked else 'Revoke attempted — user was not a founder')
            )
            conn.commit()
        except Exception:
            pass

    print(f"[ADMIN] revoke-founder user={user_id} tier={tier} revoked={revoked} by {admin_email}")
    return jsonify({'success': True, 'revoked': revoked})


@app.route('/api/admin/disk-cleanup', methods=['POST'])
@admin_required
def admin_disk_cleanup():
    """
    Delete old files from OUTPUT_DIR and RAW_DIR to recover disk space.
    Runs PRAGMA wal_checkpoint(TRUNCATE) afterwards to shrink the WAL file.

    Body (JSON, all optional):
        cutoff_hours  int  How many hours old a file must be to be deleted.
                          Default 1.  Clamped to [0, 168].

    Response:
        success              bool
        files_deleted        int   total files removed
        bytes_freed          int   total bytes freed
        raw_files_deleted    int
        output_files_deleted int
        checkpoint_result    str   "ok" | "failed" | "skipped"
        checkpoint_error     str   only present when checkpoint fails
        errors               list  non-fatal per-file errors (usually empty)
        cutoff_hours         int   the effective cutoff used
    """
    import os
    import time
    import shutil
    from .config import RAW_DIR, OUTPUT_DIR, DB_PATH
    from .database import get_connection, get_bookmarked_realpaths

    admin_email = session.get('email', 'unknown')
    data = request.get_json(force=True) or {}

    # --- Cutoff: clamp to [0, 168] hours (0 = delete everything, 168 = one week) ---
    try:
        cutoff_hours = float(data.get('cutoff_hours', 1))
    except (TypeError, ValueError):
        cutoff_hours = 1.0
    cutoff_hours = max(0.0, min(168.0, cutoff_hours))
    cutoff_mtime = time.time() - cutoff_hours * 3600

    # Fail-safe: never delete bookmarked/saved sources or outputs. If the protected
    # set cannot be loaded, abort and delete nothing rather than risk saved assets.
    try:
        protected_paths = get_bookmarked_realpaths()
    except Exception as e:
        print(f"[DISK CLEANUP] aborted — could not load bookmarked paths: {e}")
        return jsonify({
            'success': False,
            'error': 'Could not load protected (bookmarked) paths; cleanup aborted to avoid deleting saved assets.',
            'files_deleted': 0,
            'bytes_freed': 0,
        }), 500

    print(f"[DISK CLEANUP] started by admin={admin_email} cutoff_hours={cutoff_hours:.1f} "
          f"protected={len(protected_paths)}")

    total_deleted = 0
    total_bytes = 0
    raw_deleted = 0
    output_deleted = 0
    protected_skipped = 0
    errors = []

    def _purge_dir(directory):
        """Delete files older than cutoff_mtime in directory, skipping bookmarked
        files. Returns (count, bytes)."""
        nonlocal protected_skipped
        count = 0
        freed = 0
        if not os.path.isdir(directory):
            return count, freed
        try:
            entries = os.listdir(directory)
        except OSError as e:
            errors.append(f"listdir({directory}): {e}")
            return count, freed
        for name in entries:
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            if os.path.realpath(path) in protected_paths:
                protected_skipped += 1
                continue
            try:
                mtime = os.path.getmtime(path)
                if mtime < cutoff_mtime:
                    size = os.path.getsize(path)
                    os.remove(path)
                    count += 1
                    freed += size
            except OSError as e:
                errors.append(f"{path}: {e}")
        return count, freed

    raw_deleted, raw_bytes = _purge_dir(RAW_DIR)
    output_deleted, output_bytes = _purge_dir(OUTPUT_DIR)
    total_deleted = raw_deleted + output_deleted
    total_bytes = raw_bytes + output_bytes

    freed_mb = total_bytes / 1024 / 1024
    print(
        f"[DISK CLEANUP] deleted {total_deleted} files "
        f"({raw_deleted} raw + {output_deleted} output), "
        f"freed {freed_mb:.2f} MB"
    )

    # --- WAL checkpoint ---
    checkpoint_result = "skipped"
    checkpoint_error = None
    try:
        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        checkpoint_result = "ok"
        print("[DISK CLEANUP] wal checkpoint success")
    except Exception as e:
        checkpoint_result = "failed"
        checkpoint_error = str(e)
        print(f"[DISK CLEANUP] wal checkpoint failure: {e}")

    # --- Disk usage snapshot post-cleanup ---
    try:
        db_dir = os.path.dirname(DB_PATH) or '.'
        usage = shutil.disk_usage(db_dir)
        free_mb_after = usage.free / 1024 / 1024
        print(f"[DISK CLEANUP] disk free after cleanup: {free_mb_after:.1f} MB")
    except Exception:
        free_mb_after = None

    response = {
        'success': True,
        'files_deleted': total_deleted,
        'bytes_freed': total_bytes,
        'raw_files_deleted': raw_deleted,
        'output_files_deleted': output_deleted,
        'protected_skipped': protected_skipped,
        'cutoff_hours': cutoff_hours,
        'checkpoint_result': checkpoint_result,
        'errors': errors,
    }
    if free_mb_after is not None:
        response['disk_free_mb_after'] = round(free_mb_after, 1)
    if checkpoint_error:
        response['checkpoint_error'] = checkpoint_error

    return jsonify(response)


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
    """Health check endpoint for Render.
    Intentionally fast and non-blocking:
    - Disk usage: one syscall (shutil.disk_usage)
    - DB: SELECT 1 connectivity check + journal_mode PRAGMA only
    - No PRAGMA integrity_check (full page scan — moved to /__debug_health)
    - No os.walk on storage dirs (O(n) in file count — removed)
    """
    import shutil
    from .config import DB_PATH

    info = {'status': 'healthy', 'message': 'Brandr is running'}

    # Disk space — single syscall, always fast
    try:
        db_dir = os.path.dirname(DB_PATH) or '.'
        usage = shutil.disk_usage(db_dir)
        free_mb = round(usage.free / 1024 / 1024, 1)
        total_mb = round(usage.total / 1024 / 1024, 1)
        used_pct = round((usage.used / usage.total) * 100, 1)
        info['disk'] = {
            'free_mb': free_mb,
            'total_mb': total_mb,
            'used_pct': used_pct,
            'warning': free_mb < 200,
        }
        if free_mb < 200:
            info['status'] = 'degraded'
            info['message'] = f'Disk nearly full ({free_mb}MB free). SQLite I/O errors likely.'
    except Exception as e:
        info['disk'] = {'error': str(e)}

    # DB file metadata — filesystem stat calls only, no DB open
    info['db'] = {
        'exists': os.path.exists(DB_PATH),
        'readable': os.access(DB_PATH, os.R_OK) if os.path.exists(DB_PATH) else False,
        'writable': os.access(DB_PATH, os.W_OK) if os.path.exists(DB_PATH) else False,
        'wal_exists': os.path.exists(DB_PATH + '-wal'),
    }

    # DB connectivity — SELECT 1 (microseconds) + journal_mode PRAGMA
    # PRAGMA integrity_check is intentionally omitted: it does a full DB
    # page scan and blocks the worker. Use /__debug_health for that.
    try:
        with sqlite3.connect(DB_PATH, timeout=3.0) as _c:
            _c.execute('SELECT 1').fetchone()
            journal = _c.execute('PRAGMA journal_mode').fetchone()
        info['db']['connectivity'] = 'ok'
        info['db']['journal_mode'] = journal[0] if journal else 'unknown'
    except Exception as e:
        info['db']['connectivity'] = f'ERROR: {e}'
        info['status'] = 'degraded'

    status_code = 200 if info['status'] == 'healthy' else 503
    return jsonify(info), status_code


@app.route('/api/upgrade-link/<tier_name>')
@login_required
def upgrade_link(tier_name):
    """Return the best available PayPal payment link for a tier.
    Prefers the founding member rate while slots remain; falls back to regular price."""
    from .database import get_founding_slots_used
    from .config import FOUNDING_MEMBER_CONFIG, FOUNDING_PAYMENT_LINKS
    max_slots = FOUNDING_MEMBER_CONFIG.get('max_slots_per_tier', 100)
    eligible = FOUNDING_MEMBER_CONFIG.get('eligible_tiers', [])
    # Try founding link first if slots available
    if tier_name in eligible:
        slots_used = get_founding_slots_used(tier_name)
        if slots_used < max_slots:
            founding_link = FOUNDING_PAYMENT_LINKS.get(tier_name, '')
            if founding_link:
                return jsonify({
                    'success': True,
                    'url': founding_link,
                    'tier': tier_name,
                    'founding': True,
                    'slots_remaining': max_slots - slots_used,
                })
    # Fall back to regular price
    link = get_payment_link(tier_name)
    if not link:
        return jsonify({'success': False, 'error': f'No payment link available for {tier_name}'}), 404
    return jsonify({'success': True, 'url': link, 'tier': tier_name, 'founding': False})


@app.route('/api/usage')
@login_required
def api_usage():
    """Return current daily usage and limits for the logged-in user."""
    user_id = session.get('user_id')
    tier = get_user_tier(user_id)
    special_status = get_user_special_status(user_id)
    limits = get_effective_limits(tier, special_status)
    usage = get_daily_usage(user_id)
    credits_allowance = limits.get('credits_per_day', 0)
    balance = get_credit_balance(user_id, credits_allowance)
    return jsonify({
        'success': True,
        'tier': tier,
        'usage': usage,
        'credits': {
            'per_day': credits_allowance,
            'subscription': balance['subscription'],
            'earned': balance['earned'],
            'purchased': balance['purchased'],
            'total': balance['total'],
        },
        'limits': {
            'branding_jobs_per_day': limits['branding_jobs_per_day'],
            'credits_per_day': credits_allowance,
            'fetches_per_day': limits['fetches_per_day'],
            'max_brands_per_job': limits['max_brands_per_job'],
            'max_outputs_per_job': limits.get('max_outputs_per_job', limits['max_brands_per_job']),
        }
    })


@app.route('/api/admin/credits', methods=['POST'])
@admin_required
def admin_manage_credits():
    """Admin credit tool — fix a user's credits in seconds.

    Body: {"user_id": <int>, "action": <str>, "amount": <int>}
      action = grant_earned      -> add permanent earned credits
             | grant_purchased   -> add permanent purchased credits
             | set_subscription  -> set today's subscription credits exactly
             | reset_subscription-> reset subscription to the tier's daily allowance
    Returns the resulting balance. GET the current balance with action=inspect.
    """
    data = request.get_json(force=True) or {}
    try:
        target_id = int(data.get('user_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'user_id (int) is required'}), 400
    action = data.get('action', 'inspect')
    try:
        amount = int(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'amount must be an integer'}), 400

    allowance = get_effective_limits(
        get_user_tier(target_id), get_user_special_status(target_id)
    ).get('credits_per_day', 0)

    if action == 'grant_earned':
        add_earned_credits(target_id, amount)
    elif action == 'grant_purchased':
        add_purchased_credits(target_id, amount)
    elif action == 'set_subscription':
        set_subscription_credits(target_id, amount)
    elif action == 'reset_subscription':
        set_subscription_credits(target_id, allowance)
    elif action == 'inspect':
        pass  # just report the balance
    else:
        return jsonify({'success': False,
                        'error': "action must be one of: grant_earned, grant_purchased, "
                                 "set_subscription, reset_subscription, inspect"}), 400

    bal = get_credit_balance(target_id, allowance)
    print(f"[CREDITS][ADMIN] user={target_id} action={action} amount={amount} "
          f"-> balance={bal['total']} (sub={bal['subscription']} earned={bal['earned']} "
          f"purchased={bal['purchased']})", flush=True)
    return jsonify({'success': True, 'user_id': target_id, 'action': action,
                    'credits_per_day': allowance, 'balance': bal})


@app.route('/api/admin/render-stats', methods=['GET'])
@admin_required
def admin_render_stats():
    """Evidence-backed unit economics from per-render telemetry (render_events).

    Query params:
      days   — lookback window (default 30)
      cost   — assumed fixed infrastructure GBP/month for cost math (default 20)
      users  — if truthy, also return the heaviest per-user breakdown
    Answers: renders/user, cost per render (loaded + marginal), capacity,
    and heavy-user behaviour — replacing the modelled figures in the handbook
    (Section 16) with measured ones.
    """
    try:
        days = max(1, min(365, int(request.args.get('days', 30))))
    except (TypeError, ValueError):
        days = 30
    try:
        cost = float(request.args.get('cost', 20.0))
    except (TypeError, ValueError):
        cost = 20.0

    stats = get_render_stats(days=days, cost_per_month_gbp=cost)
    payload = {'success': True, 'stats': stats}
    if request.args.get('users'):
        payload['heaviest_users'] = get_user_render_stats(days=days)
    return jsonify(payload)


# ── Source reframe/crop edits (Studio content edit, per source+format) ──────────
SOURCE_EDIT_FORMATS = {'vertical_9_16', 'square_1_1'}
SOURCE_EDIT_CROP_MODES = {'fit', 'fill'}


def _clamp(value, lo, hi, default):
    """Coerce to float and clamp to [lo, hi]; fall back to default on bad input."""
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _validate_source_edit_request(user_id, source_filename, output_format):
    """Shared validation for source-edit GET/POST.
    Returns (error_response, status_code) on failure, or (None, None) when OK."""
    if not source_filename or not output_format:
        return jsonify({'success': False, 'error': 'source_filename and output_format required'}), 400
    if output_format not in SOURCE_EDIT_FORMATS:
        return jsonify({'success': False, 'error': 'Invalid output_format'}), 400
    # Reject any path component — only bare filenames are accepted (matches render path guard).
    if os.path.basename(source_filename) != source_filename:
        return jsonify({'success': False, 'error': 'Invalid source filename'}), 400
    if not user_can_download_filename(user_id, source_filename):
        return jsonify({'success': False, 'error': 'Source video not found or access denied'}), 404
    return None, None


def _resolve_render_source_edit(user_id, source_filename, output_format, payload_edit):
    """Return a clamped source-edit dict for render, or None.
    Crop/reframe apply to vertical_9_16 only; flip_h applies to ALL formats, so
    non-vertical returns a flip-only edit (or None when not flipped)."""
    edit = payload_edit if isinstance(payload_edit, dict) else None
    if edit is None and source_filename:
        try:
            edit = get_source_edit(user_id, source_filename, output_format)
            print(f"[SOURCE-EDIT] Loaded persisted edit for user={user_id} source={source_filename} format={output_format}: {edit}")
        except Exception as e:
            print(f"[SOURCE-EDIT] Persisted edit fallback failed: {e}")
            edit = None

    if not isinstance(edit, dict):
        edit = SOURCE_EDIT_DEFAULTS.copy()

    flip_h = 1 if edit.get('flip_h') else 0

    if output_format != 'vertical_9_16':
        # Non-vertical: no crop/reframe yet, but flip still applies.
        return {'flip_h': flip_h} if flip_h else None

    crop_mode = edit.get('crop_mode', SOURCE_EDIT_DEFAULTS.get('crop_mode', 'fit'))
    if crop_mode not in SOURCE_EDIT_CROP_MODES:
        print(f"[SOURCE-EDIT] Unsupported crop_mode='{crop_mode}' ignored; using fit")
        crop_mode = SOURCE_EDIT_DEFAULTS.get('crop_mode', 'fit')

    resolved = {
        'crop_x': _clamp(edit.get('crop_x', 0.5), 0.0, 1.0, 0.5),
        'crop_y': _clamp(edit.get('crop_y', 0.5), 0.0, 1.0, 0.5),
        'zoom': _clamp(edit.get('zoom', 1.0), 0.25, 4.0, 1.0),
        'crop_mode': crop_mode,
        'flip_h': flip_h,
    }
    print(f"[SOURCE-EDIT] Render edit resolved: {resolved}")
    return resolved


def _is_default_source_edit(edit):
    if not isinstance(edit, dict):
        return True
    try:
        crop_x = float(edit.get('crop_x', 0.5))
        crop_y = float(edit.get('crop_y', 0.5))
        zoom = float(edit.get('zoom', 1.0))
    except (TypeError, ValueError):
        return False
    return (
        abs(crop_x - 0.5) < 1e-9
        and abs(crop_y - 0.5) < 1e-9
        and abs(zoom - 1.0) < 1e-9
        # Only the legacy centered cover-crop can skip the source-edit pipeline.
        # The new default is fit, which must still render through the reframe filter.
        and edit.get('crop_mode', 'fill') == 'fill'
        # A flip is a real transform — never skip the pipeline when it's set.
        and not edit.get('flip_h')
    )


@app.route('/api/source-edits', methods=['GET'])
@login_required
def get_source_edit_api():
    """Return the saved reframe/crop for (current user, source, format), or defaults."""
    user_id = session['user_id']
    source_filename = (request.args.get('source_filename') or '').strip()
    output_format = (request.args.get('output_format') or '').strip()

    err, code = _validate_source_edit_request(user_id, source_filename, output_format)
    if err:
        return err, code

    edit = get_source_edit(user_id, source_filename, output_format)
    return jsonify({'success': True, 'edit': edit})


@app.route('/api/source-edits', methods=['POST'])
@login_required
def save_source_edit_api():
    """Insert/update the reframe/crop for (current user, source, format)."""
    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    source_filename = (data.get('source_filename') or '').strip()
    output_format = (data.get('output_format') or '').strip()

    err, code = _validate_source_edit_request(user_id, source_filename, output_format)
    if err:
        return err, code

    crop_mode = data.get('crop_mode', SOURCE_EDIT_DEFAULTS.get('crop_mode', 'fit'))
    if crop_mode not in SOURCE_EDIT_CROP_MODES:
        return jsonify({'success': False, 'error': 'Invalid crop_mode'}), 400

    crop_x = _clamp(data.get('crop_x', 0.5), 0.0, 1.0, 0.5)
    crop_y = _clamp(data.get('crop_y', 0.5), 0.0, 1.0, 0.5)
    # fit: zoom=1 shows the full source inside the canvas; fill: zoom=1 cover-crops.
    # zoom < 1 shrinks further; zoom > 1 crops. Floor of 0.25 keeps it usable.
    zoom = _clamp(data.get('zoom', 1.0), 0.25, 4.0, 1.0)
    flip_h = 1 if data.get('flip_h') else 0

    if not upsert_source_edit(user_id, source_filename, output_format,
                              crop_x, crop_y, zoom, crop_mode, flip_h):
        return jsonify({'success': False, 'error': 'Could not save reframe'}), 500

    return jsonify({
        'success': True,
        'edit': {'crop_x': crop_x, 'crop_y': crop_y, 'zoom': zoom,
                 'crop_mode': crop_mode, 'flip_h': flip_h}
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

def _do_brand_render(job_id, video_filepath, url_was_remote, resolved_brands,
                     data, user_id, output_format, sec_logo_resolved_path, video_id,
                     source_edit=None):
    """Background thread: run FFmpeg render for one or more brands.
    Updates brand_render_jobs[job_id] in place. No Flask request context.
    Phase 18 — called from process_branded_videos() after all validation passes.
    """
    from .config import STORAGE_ROOT
    job = brand_render_jobs[job_id]
    job['status']     = 'processing'
    job['started_at'] = time.time()

    try:
        # Normalize video (fixes corrupted timestamps, enforces output dimensions)
        print(f"[RENDER-ASYNC] {job_id[:8]} normalizing video: {video_filepath}")
        normalized_video_path = normalize_video(
            video_filepath,
            output_format=output_format,
            source_edit=source_edit,
            job_id=job_id,
        )
        print(f"[RENDER-ASYNC] {job_id[:8]} using normalized: {normalized_video_path}")

        processor    = VideoProcessor(normalized_video_path, OUTPUT_DIR)
        output_paths = []
        output_metadata = {}
        _bo_save_warnings = []
        total_brands = len(resolved_brands)

        for i, db_brand in enumerate(resolved_brands, 1):
            brand_id   = db_brand.get('id')
            brand_name = db_brand.get('display_name') or db_brand.get('name')
            print(f"[RENDER-ASYNC] {job_id[:8]} brand {i}/{total_brands}: #{brand_id} ({brand_name})")

            # Merge overrides (identical logic to synchronous path)
            merged_config = db_brand.copy()

            # Patch 54: apply per-format position/scale overrides before any other merging
            if merged_config.get('format_overrides') and output_format != 'vertical_9_16':
                try:
                    import json as _json
                    _fov_all = _json.loads(merged_config['format_overrides'])
                    _fov = _fov_all.get(output_format, {})
                    for _fov_key in ('logo_x', 'logo_y', 'logo_scale', 'wm_x', 'wm_y', 'wm_scale'):
                        if _fov_key in _fov:
                            merged_config[_fov_key] = _fov[_fov_key]
                    if _fov:
                        print(f"[RENDER-ASYNC] {job_id[:8]} applied format override '{output_format}' for brand #{brand_id}")
                except (ValueError, TypeError, KeyError):
                    pass

            # Canonical wm_* normalization
            if merged_config.get('wm_mode') is None:
                merged_config['wm_mode'] = merged_config.get('watermark_mode', 'positioned')
            if merged_config.get('wm_mode') != 'positioned':
                merged_config['wm_mode'] = 'positioned'
            if merged_config.get('wm_scale') is None:
                legacy_scale = merged_config.get('watermark_scale')
                if legacy_scale is not None:
                    merged_config['wm_scale'] = legacy_scale
            if merged_config.get('wm_opacity') is None:
                legacy_opacity = merged_config.get('watermark_opacity')
                if legacy_opacity is not None:
                    merged_config['wm_opacity'] = legacy_opacity

            # Apply request overrides
            _override_fields = [
                ('watermark_scale',   'wm_scale'),
                ('watermark_opacity', 'wm_opacity'),
                ('logo_scale',        'logo_scale'),
                ('logo_padding',      'logo_padding'),
            ]
            for req_key, cfg_key in _override_fields:
                if req_key in data:
                    merged_config[cfg_key] = data[req_key]
            for _fld in ('logo_x', 'logo_y', 'logo_rotation', 'wm_x', 'wm_y'):
                if _fld in data:
                    merged_config[_fld] = float(data[_fld])
            if 'text_enabled' in data:
                merged_config['text_enabled'] = 1 if data['text_enabled'] else 0
            for _fld in ('text_content', 'text_color', 'text_position'):
                if _fld in data:
                    merged_config[_fld] = str(data[_fld])
            if 'text_size' in data:
                merged_config['text_size'] = int(data['text_size'])
            if 'text_bg_enabled' in data:
                merged_config['text_bg_enabled'] = 1 if data['text_bg_enabled'] else 0
            if 'text_bg_opacity' in data:
                merged_config['text_bg_opacity'] = float(data['text_bg_opacity'])

            # Secondary logo (already resolved and tier-gated before thread spawn)
            if sec_logo_resolved_path:
                merged_config['secondary_logo_enabled']       = True
                merged_config['secondary_logo_resolved_path'] = sec_logo_resolved_path
                merged_config['secondary_logo_scale']    = max(0.03, min(0.5, float(data.get('secondary_logo_scale', 0.12))))
                merged_config['secondary_logo_opacity']  = max(0.1, min(1.0, float(data.get('secondary_logo_opacity', 0.9))))
                merged_config['secondary_logo_x']        = max(0.0, min(1.0, float(data.get('secondary_logo_x', 0.15))))
                merged_config['secondary_logo_y']        = max(0.0, min(1.0, float(data.get('secondary_logo_y', 0.15))))
                merged_config['secondary_logo_rotation'] = float(data.get('secondary_logo_rotation', 0)) % 360

            try:
                import time as _rt
                _t0 = _rt.time()
                output_path = processor.process_brand(merged_config, video_id=video_id, output_format=output_format)
                _render_secs = _rt.time() - _t0
                print(f"[RENDER-ASYNC] {job_id[:8]} brand '{brand_name}' done in {_render_secs:.1f}s")
                output_paths.append(output_path)
                output_metadata[output_path] = {'brand_id': brand_id, 'brand_name': brand_name}

                # Per-render telemetry (best-effort; never affects the render).
                # One row per brand render = the real compute unit — powers
                # measured unit economics (renders/user, cost/user, capacity).
                try:
                    _out_kb = (os.path.getsize(output_path) // 1024) if os.path.exists(output_path) else None
                    log_render_event(
                        user_id=user_id, job_id=job_id, brand_id=brand_id,
                        brand_name=brand_name, output_format=output_format,
                        render_seconds=_render_secs, output_kb=_out_kb,
                        brand_count=total_brands,
                    )
                except Exception as _te:
                    print(f"[RENDER-EVENT] telemetry skipped: {_te}")

                # Best-effort: persist branded output record
                try:
                    if output_format == 'vertical_9_16':
                        _bw, _bh, _bar = 720, 1280, 0.5625
                    elif output_format == 'square_1_1':
                        _bw, _bh, _bar = 720, 720, 1.0
                    else:
                        _bw, _bh, _bar = None, None, None
                    save_branded_output(
                        user_id=user_id,
                        source_filename=os.path.basename(video_filepath),
                        output_filename=os.path.basename(output_path),
                        file_path=output_path,
                        brand_id=brand_id,
                        brand_name=brand_name,
                        output_format=output_format,
                        width=_bw, height=_bh, aspect_ratio=_bar,
                    )
                except Exception as _bo_e:
                    _bo_save_warnings.append(str(_bo_e))
                    print(f"[RENDER-ASYNC] branded_output save failed: {_bo_e}")

            except Exception as render_err:
                print(f"[RENDER-ASYNC] {job_id[:8]} brand '{brand_name}' FAILED: {render_err}")
                import traceback; traceback.print_exc()
                job['status'] = 'failed'
                job['error']  = f'{brand_name}: {str(render_err)}'
                job['completed_at'] = time.time()
                return

        # Build download_urls (same shape as synchronous path)
        if output_format == 'vertical_9_16':
            _fmt = {'width': 720, 'height': 1280, 'aspect_ratio': 0.5625}
        elif output_format == 'square_1_1':
            _fmt = {'width': 720, 'height': 720, 'aspect_ratio': 1.0}
        else:
            _fmt = {}

        download_urls = []
        for op in output_paths:
            fname = os.path.basename(op)
            _m    = output_metadata.get(op, {})
            download_urls.append({
                'brand':         _m.get('brand_name', 'unknown'),
                'filename':      fname,
                'download_url':  f'/api/videos/download/{fname}',
                'output_format': output_format,
                **_fmt
            })

        # Clean up downloaded source (not local files)
        if url_was_remote:
            try:
                os.remove(video_filepath)
            except Exception as _e:
                print(f"[RENDER-ASYNC] Could not remove source: {_e}")

        # Charge 1 credit for the successful render (charge-on-success) and keep
        # the daily_usage counter for analytics. Both best-effort — a completed
        # render must never error out on accounting.
        try:
            increment_branding_jobs(user_id)
        except Exception as _e:
            print(f"[RENDER-ASYNC] Usage increment failed: {_e}")
        # spend_credits logs balance_before/spent/after (and 'insufficient')
        # itself, so no extra logging needed here.
        try:
            _allowance = get_effective_limits(
                get_user_tier(user_id), get_user_special_status(user_id)
            ).get('credits_per_day', 0)
            spend_credits(user_id, 1, _allowance)
        except Exception as _e:
            print(f"[CREDITS] credit spend failed for user={user_id}: {_e}")

        try:
            log_event('info', None, f'Async branding job {job_id[:8]} completed: {len(output_paths)} output(s) user={user_id}')
        except Exception:
            pass

        job['status']       = 'completed'
        job['outputs']      = download_urls
        job['completed_at'] = time.time()
        if _bo_save_warnings:
            job['warnings'] = _bo_save_warnings
        print(f"[RENDER-ASYNC] {job_id[:8]} ALL DONE — {len(download_urls)} output(s)")

    except Exception as e:
        import traceback; traceback.print_exc()
        job['status']       = 'failed'
        job['error']        = str(e)
        job['completed_at'] = time.time()
        print(f"[RENDER-ASYNC] {job_id[:8]} EXCEPTION: {e}")
        try:
            log_event('error', None, f'Async branding job {job_id[:8]} exception: {str(e)}')
        except Exception:
            pass


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
        # Credit enforcement: 1 credit per render, actually charged on success
        # in _do_brand_render. Pre-check here so we reject before doing work.
        credits_allowance = limits.get('credits_per_day', 0)
        balance = get_credit_balance(user_id, credits_allowance)
        if not balance.get('ok', True):
            # Credit system unreachable (DB down) — fail CLOSED so a persistent
            # DB issue can't hand out free renders. Charge-on-success still
            # fails open, so a brief blip mid-render just skips the charge.
            print(f"[CREDITS] user={user_id} pre-check unavailable (DB) — returning 503", flush=True)
            return jsonify({
                'success': False,
                'error': 'SERVICE_UNAVAILABLE',
                'message': "We couldn't check your credits right now. Please try again in a moment.",
            }), 503
        if balance['total'] < 1:
            return jsonify({
                'success': False,
                'error': 'OUT_OF_CREDITS',
                'message': "You're out of credits for today. Your daily credits reset at 00:00 UTC.",
                'tier': tier,
                'credits_per_day': credits_allowance,
                'credits_remaining': balance['total'],
            }), 403

        data = request.get_json(force=True) or {}

        SUPPORTED_OUTPUT_FORMATS = {'vertical_9_16', 'square_1_1'}
        output_format = data.get('output_format', 'vertical_9_16')
        if output_format not in SUPPORTED_OUTPUT_FORMATS:
            return jsonify({
                'success': False,
                'error': 'OUTPUT_FORMAT_UNSUPPORTED',
                'message': f'Output format "{output_format}" is not yet supported. Supported: Vertical 9:16, Square 1:1.',
                'supported_formats': ['vertical_9_16', 'square_1_1']
            }), 400

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
        print(f"[PROCESS BRANDS] Output format: {output_format}")
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
                        'format': YTDLP_FORMAT,
                        'prefer_ffmpeg': HAS_FFMPEG,
                        'retries': 5,
                        'fragment_retries': 5,
                        'socket_timeout': 300,
                    }
                    if HAS_FFMPEG and FFMPEG_DIR:
                        ydl_opts['ffmpeg_location'] = FFMPEG_DIR
                    
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
                        _ig_proxy = os.environ.get('IG_PROXY', '').strip()
                        if _ig_proxy:
                            ydl_opts['proxy'] = _ig_proxy

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
                                'error': _strip_ansi(str(download_error)),
                                'success': False
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
                        'success': False
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

            requested_filename = os.path.basename(url)
            if requested_filename != url:
                return jsonify({
                    'success': False,
                    'error': 'Invalid source filename'
                }), 400

            if not user_can_download_filename(user_id, requested_filename):
                print(f"[PROCESS BRANDS] Ownership denied for source: user={user_id} file={requested_filename}")
                return jsonify({
                    'success': False,
                    'error': 'Source video not found or access denied'
                }), 404

            # First check if it's in RAW_DIR
            video_filepath = os.path.join(RAW_DIR, requested_filename)

            # If not in the new location, check the old OUTPUT_DIR
            if not os.path.exists(video_filepath):
                video_filepath = os.path.join(OUTPUT_DIR, requested_filename)

            allowed_roots = (os.path.realpath(RAW_DIR), os.path.realpath(OUTPUT_DIR))
            real_video_filepath = os.path.realpath(video_filepath)
            try:
                is_allowed_source = any(os.path.commonpath([real_video_filepath, root]) == root for root in allowed_roots)
            except ValueError:
                is_allowed_source = False
            if not is_allowed_source:
                return jsonify({
                    'success': False,
                    'error': 'Invalid source path'
                }), 400

            video_id = os.path.splitext(requested_filename)[0]

            # Check if file exists
            if not os.path.exists(real_video_filepath):
                return jsonify({
                    'success': False,
                    'error': 'Source video file is no longer available'
                }), 404

            video_filepath = real_video_filepath
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
        
        print(f"[PROCESS BRANDS] Validation passed — {len(resolved_brands)} brand(s) queued for async render")

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
        
        # Phase 18: validation complete — spawn background render thread, return job_id immediately.
        # The browser connection is released; the render continues on the server regardless of
        # whether the client tab stays open.
        single_brand_name = resolved_brands[0].get('display_name') or resolved_brands[0].get('name') if resolved_brands else ''
        source_filename_for_edit = os.path.basename(video_filepath)
        source_edit = _resolve_render_source_edit(
            user_id,
            source_filename_for_edit,
            output_format,
            data.get('source_edit'),
        )
        if _is_default_source_edit(source_edit):
            print("[SOURCE-EDIT] Default edit detected; using legacy normalize path")
            source_edit = None
        job_id = str(uuid.uuid4())
        brand_render_jobs[job_id] = {
            'status':       'queued',
            'user_id':      user_id,
            'brand_name':   single_brand_name,
            'brand_id':     resolved_brands[0].get('id') if resolved_brands else None,
            'created_at':   time.time(),
            'started_at':   None,
            'completed_at': None,
            'outputs':      None,
            'error':        None,
        }

        threading.Thread(
            target=_do_brand_render,
            args=(
                job_id,
                video_filepath,
                url.startswith('http'),   # url_was_remote
                resolved_brands,
                data,
                user_id,
                output_format,
                sec_logo_resolved_path,
                video_id,
                source_edit,
            ),
            daemon=True
        ).start()

        print(f"[PROCESS BRANDS] Job {job_id[:8]} queued — returning immediately")
        return jsonify({
            'success':    True,
            'job_id':     job_id,
            'status':     'queued',
            'brand_name': single_brand_name,
            'message':    f'Render queued for {single_brand_name}. Poll /api/videos/brand-job/{job_id} for status.',
        })

    except Exception as e:
        import traceback
        print(f"[PROCESS BRANDS EXCEPTION]:")
        traceback.print_exc()
        # log_event is best-effort — wrap to prevent double-fault
        try:
            log_event('error', None, f'Brand processing failed: {str(e)}')
        except Exception:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/videos/fetch', methods=['POST'])
@login_required
def fetch_videos_from_urls():
    """Download videos from URLs (TikTok, Instagram, YouTube incl. Shorts, X;
    Threads experimental) - up to the tier's batch_link_limit at a time."""
    try:
        # --- Tier enforcement: daily fetch limit ---
        user_id = session.get('user_id')
        print(f"[FETCH] POST received user_id={user_id}", flush=True)

        print("[FETCH] tier lookup start", flush=True)
        tier = get_user_tier(user_id)
        print(f"[FETCH] tier lookup done: {tier}", flush=True)

        print("[FETCH] special_status lookup start", flush=True)
        try:
            special_status = get_user_special_status(user_id)
        except Exception as _ss_err:
            print(f"[FETCH] special_status lookup error (using None): {_ss_err}", flush=True)
            special_status = None
        print(f"[FETCH] special_status lookup done: {special_status}", flush=True)

        limits = get_effective_limits(tier, special_status)

        print("[FETCH] daily usage lookup start", flush=True)
        try:
            usage = get_daily_usage(user_id)
        except Exception as _du_err:
            print(f"[FETCH] daily usage lookup error (using zeros): {_du_err}", flush=True)
            usage = {'branding_jobs': 0, 'downloads': 0}
        print(f"[FETCH] daily usage lookup done: {usage}", flush=True)
        
        if not YoutubeDL:
            return jsonify({'success': False, 'error': 'yt-dlp not installed'}), 500
        
        data = request.get_json(force=True) or {}
        urls = data.get('urls') or []
        
        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({'success': False, 'error': 'Provide JSON: {"urls": ["url1", "url2", ...]}'}), 400
        
        _batch_cap = limits.get('batch_link_limit', 5)
        if len(urls) > _batch_cap:
            return jsonify({'success': False,
                            'error': f'Maximum {_batch_cap} links at a time on your plan. Upgrade for more.',
                            'tier': tier, 'batch_link_limit': _batch_cap}), 400

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
                # Threads is Meta infra with no dedicated yt-dlp extractor (2026.03) —
                # EXPERIMENTAL: let the generic extractor try, routed through the same
                # Meta cookies + proxy as Instagram (so it shares the IG unblock path).
                is_threads = 'threads.net' in url_input.lower() or 'threads.com' in url_input.lower()
                is_meta = is_instagram or is_threads

                ydl_opts = {
                    'outtmpl': os.path.join(RAW_DIR, '%(id)s.%(ext)s'),
                    'merge_output_format': 'mp4',
                    'format': YTDLP_FORMAT,
                    'prefer_ffmpeg': HAS_FFMPEG,
                    'retries': 5,
                    'fragment_retries': 5,
                    'socket_timeout': 300,
                }
                if HAS_FFMPEG and FFMPEG_DIR:
                    ydl_opts['ffmpeg_location'] = FFMPEG_DIR
                
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
                    # Route Instagram through a residential proxy when IG_PROXY is
                    # set (e.g. http://user:pass@host:port). Off by default —
                    # Instagram blocks datacenter IPs like Render's, so this is the
                    # durable fix for those 403s. Inert until the env var is set.
                    _ig_proxy = os.environ.get('IG_PROXY', '').strip()
                    if _ig_proxy:
                        ydl_opts['proxy'] = _ig_proxy
                        print("[FETCH] Instagram routed via IG_PROXY (residential)")

                # Threads (experimental): route through the same Meta proxy if set,
                # but do NOT apply the Instagram-app headers — with no dedicated
                # extractor, yt-dlp falls back to the generic one, which needs a
                # normal browser UA to read the og:video tag off the page.
                if is_threads:
                    _ig_proxy = os.environ.get('IG_PROXY', '').strip()
                    if _ig_proxy:
                        ydl_opts['proxy'] = _ig_proxy
                        print("[FETCH] Threads (experimental) routed via IG_PROXY")

                # Apply TikTok impersonation for TikTok URLs
                # Note: impersonation requires curl_cffi and specific target format
                # Temporarily disabled until proper integration is tested
                # if is_tiktok:
                #     ydl_opts['impersonate'] = ('chrome', '110', 'windows')

                # --- Cookie selection + rotation ----------------------------
                # Instagram needs session cookies and they expire; we hold a pool
                # (INSTAGRAM_COOKIES + _1.._10) and rotate least-recently-used,
                # failing over on auth errors. Non-Instagram sources fall back to
                # the single legacy cookies.txt (behaviour unchanged).
                from .config import COOKIE_FILE as _legacy_cookie_file
                from . import cookie_pool

                def _legacy_cookie_candidate():
                    """The single portal/data/cookies.txt if it carries cookie data."""
                    try:
                        if os.path.exists(_legacy_cookie_file) and os.path.isfile(_legacy_cookie_file):
                            with open(_legacy_cookie_file, 'r', encoding='utf-8') as f:
                                for line in f:
                                    line = line.strip()
                                    if line and not line.startswith('#') and '\t' in line:
                                        return _legacy_cookie_file
                    except Exception as _ck_err:
                        print(f"[FETCH] legacy cookie check failed: {_ck_err}")
                    return None

                if is_meta and cookie_pool.pool_size() > 0:
                    using_pool = True
                    cookie_candidates = cookie_pool.candidates_lru()
                else:
                    using_pool = False
                    _legacy = _legacy_cookie_candidate()
                    cookie_candidates = [_legacy] if _legacy else [None]

                # Circuit breaker: if the whole pool just failed (Instagram IP
                # block), skip entirely instead of firing a request per cookie and
                # digging the block deeper. Returns the friendly error instantly.
                if using_pool and cookie_pool.breaker_open():
                    _mins = max(1, cookie_pool.breaker_remaining() // 60)
                    print(f"[COOKIE POOL] breaker open — skipping Instagram fetch for "
                          f"{url_input[:50]} (~{_mins}min left)", flush=True)
                    return {
                        'url': url_input,
                        'error': ("Instagram downloads are paused for a few minutes while a "
                                  "temporary block clears. Please try again shortly, or use a "
                                  "TikTok / X link in the meantime."),
                        'success': False,
                    }

                info = None
                filename = None
                tried_bad = []          # cookies that auth-failed on this URL
                content_error = None    # non-auth error → don't rotate, surface it

                for _idx, _cookie in enumerate(cookie_candidates):
                    opts = dict(ydl_opts)
                    if _cookie:
                        opts['cookiefile'] = _cookie
                        _label = os.path.basename(_cookie)
                        print(f"[FETCH] Using cookie: {_label}"
                              + (f" (pool {_idx + 1}/{len(cookie_candidates)})" if using_pool else ""))
                        if using_pool:
                            cookie_pool.mark_used(_cookie)
                    else:
                        print("[FETCH] No cookie file in use")

                    try:
                        with YoutubeDL(opts) as ydl:
                            print(f"[FETCH] Downloading: {url_input[:50]}...")
                            info = ydl.extract_info(url_input, download=True)
                            filename = ydl.prepare_filename(info)
                        if using_pool and _cookie:
                            cookie_pool.mark_success(_cookie)
                            cookie_pool.reset_breaker()  # Instagram is responding again
                            # A later cookie worked, so the earlier failures were
                            # genuinely dead cookies — cool them down.
                            for _b in tried_bad:
                                cookie_pool.mark_bad(_b)
                        break
                    except Exception as download_error:
                        err_text = _strip_ansi(str(download_error))
                        print(f"[FETCH ERROR] Download failed for {url_input}: {err_text}")
                        info = None
                        if (using_pool and cookie_pool.is_auth_failure(err_text)
                                and _idx < len(cookie_candidates) - 1):
                            print(f"[FETCH] auth failure on {os.path.basename(_cookie)} "
                                  f"— rotating to next cookie")
                            tried_bad.append(_cookie)
                            continue
                        if using_pool and cookie_pool.is_auth_failure(err_text):
                            break  # last cookie also auth-failed → all failed
                        # Non-auth error (private/removed post, network, unsupported)
                        # — another cookie won't help. Surface it as before.
                        import traceback
                        traceback.print_exc()
                        content_error = err_text
                        break

                if content_error is not None:
                    return {'url': url_input, 'error': content_error, 'success': False}

                if info is None:
                    # Every available cookie auth-failed for this URL. Ambiguous
                    # (private post OR whole pool stale), so we do NOT cool every
                    # cookie down — but alert loudly and show one friendly error.
                    if using_pool:
                        print(f"[COOKIE ALERT] all {cookie_pool.pool_size()} pooled "
                              f"cookie(s) failed auth for {url_input} — refresh the "
                              f"pool if this persists")
                        cookie_pool.trip_breaker()  # stop hammering IG for a while
                    return {
                        'url': url_input,
                        'error': ("Instagram couldn't be reached for this link right now. "
                                  "It may be private or removed, or Instagram is temporarily "
                                  "blocking downloads. Please try again shortly or try another link."),
                        'success': False,
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
                    'success': False
                }
        
        # Download sequentially to keep memory low
        print(f"[FETCH] download loop start: {len(urls)} URL(s)", flush=True)
        results = []
        for url in urls:
            results.append(download_one(url))

        success_count = sum(1 for r in results if r.get('success'))
        print(f"[FETCH] download loop done: {success_count}/{len(urls)} succeeded", flush=True)

        try:
            log_event('info', None, f'Fetch complete: {success_count}/{len(urls)} successful')
        except Exception as _log_err:
            print(f"[FETCH] log_event warning: {_log_err}", flush=True)

        # Increment daily download counter for successful downloads (non-critical)
        if success_count > 0:
            try:
                increment_downloads(user_id, success_count)
            except Exception as _inc_err:
                print(f"[FETCH] increment_downloads warning (non-critical): {_inc_err}", flush=True)

        print("[FETCH] returning success response", flush=True)
        return jsonify({
            'success': True,
            'total': len(urls),
            'successful': success_count,
            'results': results
        })

    except Exception as e:
        import traceback
        print(f"[FETCH EXCEPTION]:", flush=True)
        traceback.print_exc()
        log_event('error', None, f'Fetch failed: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

# Process endpoint removed - using client-side Canvas watermarking only

# Status endpoint removed - no server-side job queue

@app.route('/api/videos/download/<filename>', methods=['GET'])
@login_required
def download_video(filename):
    """Download processed video (raw or branded output)"""
    from .config import UPLOAD_DIR
    from .database import get_connection

    # Sanitize filename to prevent path traversal
    filename = os.path.basename(filename)
    print(f'[DOWNLOAD] Requested: {filename}')

    user_id = session.get('user_id')

    # Ownership check — must pass before we reveal whether the file exists.
    # Returns False on any DB error (conservative deny-by-default).
    if not user_can_download_filename(user_id, filename):
        print(f'[DOWNLOAD] Ownership denied: user={user_id} file={filename}')
        # Return 404, not 403, so we don't reveal file existence to non-owners.
        return jsonify({'error': 'File not found', 'filename': filename}), 404

    # Search all known locations in priority order
    search_paths = [
        os.path.join(OUTPUT_DIR, filename),   # branded outputs first
        os.path.join(RAW_DIR, filename),       # raw downloads
        os.path.join(UPLOAD_DIR, filename),    # legacy uploads
    ]
    filepath = next((p for p in search_paths if os.path.exists(p)), None)

    # Last resort: authoritative file_path from downloads DB record (user-scoped)
    if filepath is None:
        try:
            with get_connection() as conn:
                c = conn.cursor()
                c.execute(
                    'SELECT file_path FROM downloads WHERE filename = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1',
                    (filename, user_id)
                )
                row = c.fetchone()
                if row and row['file_path'] and os.path.exists(row['file_path']):
                    filepath = row['file_path']
                    print(f'[DOWNLOAD] Found via DB file_path: {filepath}')
        except Exception as db_err:
            print(f'[DOWNLOAD] DB lookup failed for {filename}: {db_err}')

    print(f'[DOWNLOAD] Resolved path: {filepath} | exists: {filepath is not None}')

    if filepath is None:
        print(f'[DOWNLOAD] Not found in any location — searched: {search_paths}')
        return jsonify({'error': 'File not found', 'filename': filename}), 404

    try:
        file_size = os.path.getsize(filepath)
        print(f'[DOWNLOAD] Serving {filename} ({file_size} bytes) from {filepath}')

        directory = os.path.dirname(os.path.abspath(filepath))
        response = send_from_directory(directory, filename, as_attachment=True)
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.headers['Content-Type'] = 'video/mp4'
        response.headers['Cache-Control'] = 'no-cache'
        print(f'[DOWNLOAD] Response status: 200 for {filename}')
        return response
    except Exception as e:
        import traceback
        print(f'[DOWNLOAD] Exception serving {filename}: {e}')
        traceback.print_exc()
        return jsonify({'error': 'Failed to serve file', 'details': str(e), 'filename': filename}), 500

# ZIP safety caps — protect Render free tier from OOM on large batch downloads
MAX_ZIP_FILES = 10
MAX_ZIP_BYTES = 250 * 1024 * 1024  # 250 MB

@app.route('/api/videos/download-zip', methods=['POST'])
@login_required
def download_zip():
    """Bundle multiple branded output files into a single ZIP and return it."""
    from datetime import datetime

    data = request.get_json(force=True) or {}
    raw_files = data.get('files', [])
    source = data.get('source', '')

    print(f'[DOWNLOAD-ZIP] Requested files: {raw_files}')

    if not raw_files:
        return jsonify({'error': 'No files requested'}), 400

    user_id = session.get('user_id')

    # Sanitize: basename only, .mp4 only, no traversal
    sanitized, rejected = [], []
    for name in raw_files:
        base = os.path.basename(str(name))
        if not base or not base.endswith('.mp4'):
            rejected.append(name)
            continue
        sanitized.append(base)

    if rejected:
        print(f'[DOWNLOAD-ZIP] Rejected (invalid names): {rejected}')
        return jsonify({'error': 'Invalid filenames', 'rejected': rejected}), 400

    # Ownership check — reject the entire request if any file is not owned.
    # Deny-by-default: user_can_download_filename returns False on DB errors.
    unauthorized = [f for f in sanitized if not user_can_download_filename(user_id, f)]
    if unauthorized:
        print(f'[DOWNLOAD-ZIP] Ownership denied for user={user_id}: {unauthorized}')
        return jsonify({'error': 'Unauthorized', 'unauthorized': unauthorized}), 403

    # Cap: file count — checked after ownership, before disk/DB resolution
    if len(sanitized) > MAX_ZIP_FILES:
        print(f'[DOWNLOAD-ZIP] Too many files: {len(sanitized)} > {MAX_ZIP_FILES}')
        return jsonify({
            'error': 'ZIP_TOO_MANY_FILES',
            'message': f'You can ZIP up to {MAX_ZIP_FILES} files at once.',
            'max_files': MAX_ZIP_FILES,
            'requested': len(sanitized),
        }), 400

    # Resolve files — 3-step chain matching direct download endpoint
    found, missing = [], []
    for base in sanitized:
        resolved = None

        # Step 1: OUTPUT_DIR flat lookup
        candidate = os.path.join(OUTPUT_DIR, base)
        if os.path.exists(candidate):
            resolved = candidate

        # Step 2: branded_outputs.file_path
        if resolved is None:
            try:
                with get_connection() as conn:
                    row = conn.execute(
                        'SELECT file_path FROM branded_outputs WHERE output_filename = ? AND user_id = ? LIMIT 1',
                        (base, user_id)
                    ).fetchone()
                if row and row['file_path'] and os.path.exists(row['file_path']):
                    resolved = row['file_path']
            except Exception as e:
                print(f'[DOWNLOAD-ZIP] DB branded_outputs lookup error for {base}: {e}')

        # Step 3: downloads.file_path
        if resolved is None:
            try:
                with get_connection() as conn:
                    row = conn.execute(
                        'SELECT file_path FROM downloads WHERE filename = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1',
                        (base, user_id)
                    ).fetchone()
                if row and row['file_path'] and os.path.exists(row['file_path']):
                    resolved = row['file_path']
            except Exception as e:
                print(f'[DOWNLOAD-ZIP] DB downloads lookup error for {base}: {e}')

        if resolved:
            found.append((base, resolved))
            print(f'[DOWNLOAD-ZIP] Added: {resolved}')
        else:
            missing.append(base)
            print(f'[DOWNLOAD-ZIP] Missing: {base}')

    if missing:
        return jsonify({'error': 'Some output files not found', 'missing': missing}), 404

    if not found:
        return jsonify({'error': 'No valid output files found'}), 404

    # Cap: total projected size — calculated after resolution, before building ZIP in memory
    total_bytes = sum(os.path.getsize(path) for _, path in found)
    if total_bytes > MAX_ZIP_BYTES:
        print(f'[DOWNLOAD-ZIP] Too large: {total_bytes} bytes > {MAX_ZIP_BYTES} bytes')
        return jsonify({
            'error': 'ZIP_TOO_LARGE',
            'message': 'This ZIP would be too large. Try downloading fewer files at once.',
            'max_bytes': MAX_ZIP_BYTES,
            'total_bytes': total_bytes,
        }), 413

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for base, path in found:
            zf.write(path, arcname=base)
    zip_buffer.seek(0)

    # Derive a sensible zip name
    if source:
        source_stem = os.path.splitext(os.path.basename(source))[0]
        zip_name = f'{source_stem}_branded_outputs.zip'
    else:
        ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        zip_name = f'brandr_outputs_{ts}.zip'

    print(f'[DOWNLOAD-ZIP] Sending zip: {zip_name} ({zip_buffer.getbuffer().nbytes} bytes, {len(found)} files)')

    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=zip_name,
    )

# Recent videos endpoint removed - using localStorage history only

# ============================================================================
# API: PREVIEW - Frame extraction and asset serving for canvas preview
# ============================================================================

@app.route('/api/preview/extract-frame', methods=['POST'])
@login_required
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

        # Find the video file — search all known locations in priority order
        from .config import UPLOAD_DIR
        from .database import get_connection
        search_paths = [
            os.path.join(RAW_DIR, filename),
            os.path.join(OUTPUT_DIR, filename),
            os.path.join(UPLOAD_DIR, filename),
        ]
        video_path = next((p for p in search_paths if os.path.exists(p)), None)

        # Last resort: authoritative file_path from the downloads DB record
        # P1 fix: filter by user_id so users cannot extract frames from other users' files
        if video_path is None:
            try:
                req_user_id = session.get('user_id')
                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute(
                        'SELECT file_path FROM downloads WHERE filename = ? AND user_id = ? ORDER BY created_at DESC LIMIT 1',
                        (filename, req_user_id)
                    )
                    row = c.fetchone()
                    if row and row['file_path'] and os.path.exists(row['file_path']):
                        video_path = row['file_path']
                        print(f'[EXTRACT-FRAME] Found via DB file_path: {video_path}')
            except Exception as db_err:
                print(f'[EXTRACT-FRAME] DB lookup failed for {filename}: {db_err}')

        if video_path is None:
            print(f'[EXTRACT-FRAME] File not found in any location: {filename}')
            print(f'[EXTRACT-FRAME] Searched: {search_paths}')
            return jsonify({'success': False, 'error': f'File not found: {filename}'}), 404

        print(f'[EXTRACT-FRAME] Using path: {video_path}')
        
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
        
        # Extract frame as JPEG — optional timestamp seek (default: frame 0)
        raw_t = data.get('t') or data.get('timestamp')
        try:
            seek_sec = float(raw_t) if raw_t is not None else None
            if seek_sec is not None and seek_sec < 0:
                seek_sec = None
        except (TypeError, ValueError):
            seek_sec = None

        temp_frame = os.path.join(tempfile.gettempdir(), f'frame_{uuid.uuid4().hex}.jpg')

        extract_cmd = [FFMPEG_BIN, '-y']
        if seek_sec is not None:
            extract_cmd += ['-ss', str(seek_sec)]
        extract_cmd += [
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '5',
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
@login_required
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
@login_required
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
        
        # Get brand by ID with ownership verification.
        # Patch 46A: retry up to 3 times on transient sqlite3.OperationalError
        # (disk I/O errors during preview bursts are often transient on Render).
        # Read-only lookup — retrying is always safe; no write side-effects.
        brand = None
        _preview_exc = None
        for _attempt in range(3):
            try:
                brand = get_brand(brand_id=brand_id, user_id=user_id)
                break
            except sqlite3.OperationalError as _e:
                _preview_exc = _e
                if _attempt < 2:
                    _wait = 0.15 * (_attempt + 1)   # 0.15 s, 0.30 s
                    print(f"[PREVIEW] get_brand #{brand_id} attempt {_attempt + 1}/3 failed "
                          f"({_e}), retrying in {_wait}s")
                    time.sleep(_wait)
                else:
                    print(f"[PREVIEW] get_brand #{brand_id} failed after 3 attempts: {_e}")
                    return '', 503

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

        # Serve the file — no-cache so browsers always revalidate after re-upload
        # (logo_normalized.png is overwritten in-place; same URL but new content)
        response = send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))
        response.headers['Cache-Control'] = 'no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        return response
        
    except Exception as e:
        import traceback
        print(f"[PREVIEW] brand-asset #{brand_id}/{asset_type} error: {traceback.format_exc()}")
        return '', 503   # Patch 46A: empty body — browser <img> onerror handles gracefully

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
            'updated_at': brand.get('updated_at'),  # cache-bust token for logo/watermark URLs
            
            # Asset Paths
            'logo_path': brand.get('logo_path'),
            'watermark_path': brand.get('watermark_path') or brand.get('watermark_vertical'),
            'watermark_vertical': brand.get('watermark_vertical'),
            'watermark_square': brand.get('watermark_square'),
            'watermark_landscape': brand.get('watermark_landscape'),
            
            # Watermark Config (wm_* keys are canonical)
            # Always positioned — normalize any stale 'fullscreen' DB values
            'wm_mode': 'positioned',
            'wm_scale': brand['wm_scale'] if brand.get('wm_scale') is not None else (brand['watermark_scale'] if brand.get('watermark_scale') is not None else 0.25),
            'wm_opacity': brand['wm_opacity'] if brand.get('wm_opacity') is not None else (brand['watermark_opacity'] if brand.get('watermark_opacity') is not None else 0.20),
            'wm_x': brand.get('wm_x', 0.5),
            'wm_y': brand.get('wm_y', 0.5),
            
            # Logo Config
            'logo_scale': brand.get('logo_scale', 0.25),
            'logo_opacity': brand.get('logo_opacity', 1.0),
            'logo_x': brand.get('logo_x', 0.85),
            'logo_y': brand.get('logo_y', 0.85),
            'logo_padding': brand.get('logo_padding', 40),
            'logo_shape': brand.get('logo_shape'),
            'logo_rotation': brand.get('logo_rotation', 0.0),
            
            # Text Layer Config
            'text_enabled': brand.get('text_enabled', False),
            'text_content': brand.get('text_content', ''),
            'text_x': brand.get('text_x', 0),
            'text_y': brand.get('text_y', 0),
            'text_size': brand.get('text_size', 48),
            'text_color': brand.get('text_color', '#FFFFFF'),
            'text_bg_enabled': brand.get('text_bg_enabled', True),
            'text_bg_opacity': brand.get('text_bg_opacity', 0.6),
            # Patch 54: per-format position overrides
            'format_overrides': brand.get('format_overrides')  # raw JSON string; parsed client-side
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
@login_required
def get_brand_config_api(brand_name):
    """Get saved configuration for a brand (legacy)"""
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
            'watermark_scale': float(data.get('watermark_scale', 0.25)),
            'watermark_opacity': float(data.get('watermark_opacity', 0.20)),
            'logo_scale': float(data.get('logo_scale', 0.25)),
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
        user_id = session.get('user_id')
        brand = get_brand(brand_id=brand_id, user_id=user_id)
        
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

        # --- Reactivation: check for soft-deleted brand with same (name, user_id) ---
        # delete_brand() only sets is_active=0 — the UNIQUE(name, user_id) constraint
        # still applies, so a plain INSERT would fail.  Instead we reactivate the row.
        from .database import find_inactive_brand, update_brand
        deleted_brand = find_inactive_brand(name, user_id)
        if deleted_brand:
            deleted_id = deleted_brand['id']
            print(f"[BRANDS] Found soft-deleted brand '{name}' (id={deleted_id}) — reactivating as clean slate")
            update_brand(
                deleted_id,
                is_active=1,
                display_name=display_name,
                # Wipe all asset paths and positions so the user gets a genuinely fresh brand,
                # not the previous brand's logo/watermark/layout.
                logo_path=None,
                watermark_path=None,
                watermark_vertical=None,
                watermark_square=None,
                watermark_landscape=None,
                logo_x=None, logo_y=None, logo_scale=0, logo_opacity=1.0, logo_rotation=0,
                logo_padding=40,
                wm_x=None, wm_y=None, wm_scale=1.0, wm_opacity=0.4, wm_mode='positioned',
                watermark_scale=1.15, watermark_opacity=0.4,
                text_enabled=0, text_content='',
            )
            print(f"[BRANDS] Reactivated brand: {name} (id={deleted_id})")
            return jsonify({
                'success': True,
                'brand_id': deleted_id,
                'reactivated': True,
                'message': f'Brand {name} reactivated',
            }), 200

        # No deleted brand found — create new
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
            watermark_scale=data.get('watermark_scale', 0.25),
            watermark_opacity=data.get('watermark_opacity', 0.20),
            logo_scale=data.get('logo_scale', 0.25),
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
            wm_mode='positioned',  # always — normalize stale 'fullscreen' from client
            wm_x=data.get('wm_x', 0.5),
            wm_y=data.get('wm_y', 0.5),
            wm_scale=data.get('wm_scale', 0.25),
            wm_opacity=data.get('wm_opacity', 0.20),
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
        
        user_id = session.get('user_id')
        
        # Check brand exists and belongs to user
        brand = get_brand(brand_id=brand_id, user_id=user_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
        # Check if locked (system templates)
        if brand['is_locked']:
            return jsonify({'success': False, 'error': 'This brand is locked and cannot be modified'}), 403
        
        data = request.get_json(force=True) or {}

        print(f"[BRANDS] Updating brand #{brand_id} ({brand['name']}) with fields: {list(data.keys())}")

        # Patch 54: if a format key is present and it's not the base format,
        # save position/scale fields into format_overrides JSON instead of base columns.
        # logo_opacity and logo_rotation are always saved to base columns (global, not per-format).
        format_key = data.pop('format', None)
        _per_format_fields = ('logo_x', 'logo_y', 'logo_scale', 'wm_x', 'wm_y', 'wm_scale')
        if format_key and format_key != 'vertical_9_16':
            import json as _json
            _fov_payload = {k: data.pop(k) for k in _per_format_fields if k in data}
            if _fov_payload:
                try:
                    _existing = json.loads(brand.get('format_overrides') or '{}')
                except (ValueError, TypeError):
                    _existing = {}
                _existing[format_key] = _fov_payload
                data['format_overrides'] = json.dumps(_existing)
                print(f"[BRANDS] Saving format override for '{format_key}': {_fov_payload}")

        # Update brand
        update_brand(brand_id, **data)
        
        print(f"[BRANDS] Updated brand: {brand['name']} (id={brand_id})")
        
        return jsonify({
            'success': True,
            'message': f'Brand {brand["name"]} updated'
        })
    except sqlite3.OperationalError as e:
        print(f"[BRANDS ERROR] SQLite OperationalError updating brand #{brand_id}: {e}")
        if 'locked' in str(e).lower():
            return jsonify({
                'success': False,
                'error': 'Database busy, please retry',
                'code': 'DB_LOCKED',
                'fix': 'Wait 2 seconds and click Save again'
            }), 503
        return jsonify({'success': False, 'error': str(e)}), 500
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
        
        user_id = session.get('user_id')
        
        # Check brand exists and belongs to user
        brand = get_brand(brand_id=brand_id, user_id=user_id)
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
        brand = get_brand(brand_id=brand_id, user_id=user_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
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

        fallback_used = False
        if not norm_result['success']:
            # BG removal produced a bad/transparent result — fall back to safe mode (resize+convert only)
            print(f"[BRANDS] normalize_logo failed for brand {brand_id} (remove_bg={remove_bg}): {norm_result.get('error')}")
            print(f"[BRANDS] Falling back to safe normalization (no BG removal) for brand {brand_id}")
            norm_result = normalize_logo(
                original_path,
                normalized_path,
                max_dimension=1024,
                remove_bg=None,
                bg_strength=0
            )
            fallback_used = True
            if not norm_result['success']:
                return jsonify({
                    'success': False,
                    'error': f'Failed to normalize image even in safe mode: {norm_result.get("error")}'
                }), 500

        # Store relative path of NORMALIZED version (this is what VideoProcessor will use)
        relative_path = os.path.relpath(normalized_path, STORAGE_ROOT)

        # Update database
        update_brand(brand_id, logo_path=relative_path)

        print(f"[BRANDS] Uploaded & normalized logo for brand {brand_id}: {relative_path} (fallback={fallback_used})")
        print(f"[BRANDS] Original: {norm_result.get('original_format')} {norm_result.get('original_size')}")
        print(f"[BRANDS] Normalized: PNG {norm_result.get('normalized_size')}")

        return jsonify({
            'success': True,
            'logo_path': relative_path,
            'message': 'Logo uploaded and normalized successfully' + (' (BG removal skipped — result was transparent, using original)' if fallback_used else ''),
            'fallback_used': fallback_used,
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
        brand = get_brand(brand_id=brand_id, user_id=user_id)
        if not brand:
            return jsonify({'success': False, 'error': 'Brand not found'}), 404
        
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

        # Validate file type before writing to disk
        _wm_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if _wm_ext not in {'webm', 'mp4', 'mov', 'avi'}:
            return jsonify({'error': 'Invalid file type. Allowed: webm, mp4, mov, avi', 'reason': 'invalid_type'}), 400

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
        # P1 fix: store user_id so ownership can be verified on status poll
        watermark_jobs[job_id] = {
            'status': 'queued',
            'filename': mp4_filename,
            'webm_path': temp_webm,
            'output_path': output_path,
            'created_at': time.time(),
            'message': 'Waiting for conversion worker...',
            'user_id': session.get('user_id'),
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
            '-threads', '1',                 # Single-threaded — prevents OOM on 512MB Render instance
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

    # P1 fix: verify the job belongs to the requesting user
    if job.get('user_id') != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403
    
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

@app.route('/api/videos/brand-job/<job_id>', methods=['GET'])
@login_required
def get_brand_job_status(job_id):
    """Poll async brand render job status (Phase 18).
    Mirrors /api/videos/convert-status/<job_id> for the render pipeline.
    """
    if job_id not in brand_render_jobs:
        return jsonify({
            'error':   'Job not found',
            'job_id':  job_id,
            'message': 'Invalid job ID or job expired.',
        }), 404

    job = brand_render_jobs[job_id]

    # Ownership check — users can only poll their own jobs
    if job.get('user_id') != session.get('user_id'):
        return jsonify({'error': 'Access denied'}), 403

    response = {
        'job_id':     job_id,
        'status':     job['status'],           # queued|processing|completed|failed
        'brand_name': job.get('brand_name', ''),
        'message':    job.get('message', ''),
    }

    if job['status'] == 'completed':
        response['success'] = True
        response['outputs'] = job['outputs']   # same shape as old synchronous response
        if job.get('warnings'):
            response['warnings'] = job['warnings']
    elif job['status'] == 'failed':
        response['success'] = False
        response['error']   = job.get('error', 'Unknown error')

    return jsonify(response)


# Stub endpoints removed - focus on core watermarking functionality


# ================================================================
# DEBUG ENDPOINT: BRAND INTEGRITY CHECK
# ================================================================
@app.route('/api/debug/brand-integrity', methods=['GET'])
@login_required
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
@login_required
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
    from .config import UPLOAD_DIR
    import os
    from flask import send_from_directory

    user_id = session['user_id']

    download_record = get_download(download_id, user_id)
    if not download_record:
        print(f'[DOWNLOAD-ORIGINAL] #{download_id}: not found or unauthorized for user #{user_id}')
        return jsonify({'error': 'Download not found or unauthorized'}), 404

    stored_path = download_record['file_path']
    filename = download_record['filename']
    print(f'[DOWNLOAD-ORIGINAL] #{download_id}: filename={filename} stored_path={stored_path}')

    # Resolve the actual file — stored path is authoritative; fall back to known dirs
    search_paths = [
        stored_path,
        os.path.join(RAW_DIR, filename),
        os.path.join(OUTPUT_DIR, filename),
        os.path.join(UPLOAD_DIR, filename),
    ]
    file_path = next((p for p in search_paths if p and os.path.exists(p)), None)

    print(f'[DOWNLOAD-ORIGINAL] #{download_id}: resolved={file_path} exists={file_path is not None}')

    if file_path is None:
        print(f'[DOWNLOAD-ORIGINAL] #{download_id}: file not found in any location — searched {search_paths}')
        return jsonify({'error': 'File has expired or been deleted', 'filename': filename}), 404

    directory = os.path.dirname(os.path.abspath(file_path))
    basename = os.path.basename(file_path)
    print(f'[DOWNLOAD-ORIGINAL] #{download_id}: serving {basename} from {directory}')
    response = send_from_directory(directory, basename, as_attachment=True)
    response.headers['Content-Type'] = 'video/mp4'
    response.headers['Content-Disposition'] = f'attachment; filename="{basename}"'
    return response


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

    # P1 fix: reject file_path values that point outside allowed storage dirs.
    # Prevents a logged-in user from registering an arbitrary server path
    # (e.g. the SQLite DB) and then downloading it via download-original.
    from .config import RAW_DIR, OUTPUT_DIR
    allowed_roots = (
        os.path.realpath(RAW_DIR),
        os.path.realpath(OUTPUT_DIR),
    )
    real_file_path = os.path.realpath(file_path)
    try:
        is_allowed_path = any(os.path.commonpath([real_file_path, root]) == root for root in allowed_roots)
    except ValueError:
        is_allowed_path = False
    if not is_allowed_path:
        return jsonify({'error': 'Invalid file path'}), 400

    # Verify file exists
    if not os.path.isfile(real_file_path):
        return jsonify({'error': 'File does not exist at specified path'}), 400

    download_id = save_download(user_id, source_url, filename, real_file_path, display_name)
    
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
    """Rename a download's display_name (UI-only, doesn't change disk filename).
    Patch 47A: tightened validation — max 80 chars, no slash/backslash, no control chars."""
    from .database import update_display_name

    data = request.get_json(force=True) or {}
    raw = data.get('display_name')

    # Patch 47A validation
    name = raw.strip() if raw else ''
    if not name:
        return jsonify({'error': 'display_name is required'}), 400
    if len(name) > 80:
        return jsonify({'error': 'display_name must be 80 characters or fewer'}), 400
    if '/' in name or '\\' in name:
        return jsonify({'error': 'display_name cannot contain / or \\'}), 400
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return jsonify({'error': 'display_name contains invalid characters'}), 400

    user_id = session['user_id']
    success = update_display_name(download_id, user_id, name)

    if not success:
        return jsonify({'error': 'Download not found or access denied'}), 404

    return jsonify({
        'success': True,
        'download_id': download_id,
        'display_name': name,
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


@app.route('/api/outputs/branded', methods=['GET'])
@login_required
def get_branded_outputs():
    """List branded output records for the current user.
    Patch 43: Check file existence server-side, add file_available field, strip file_path."""
    from .database import get_branded_outputs_for_user
    user_id = session['user_id']
    limit = request.args.get('limit', 50, type=int)
    try:
        outputs = get_branded_outputs_for_user(user_id, limit)
        for output in outputs:
            file_path = output.get('file_path') or ''
            output['file_available'] = bool(file_path and os.path.exists(file_path))
            output.pop('file_path', None)
        return jsonify({'success': True, 'outputs': outputs, 'count': len(outputs)})
    except Exception as e:
        return jsonify({'success': False, 'error': 'Could not load branded outputs'}), 500


@app.route('/api/downloads/<int:download_id>/bookmark', methods=['POST'])
@login_required
def toggle_download_bookmark_api(download_id):
    """Toggle bookmark status for a download"""
    from .database import toggle_download_bookmark, get_user_bookmark_count
    
    user_id = session['user_id']
    new_state = toggle_download_bookmark(download_id, user_id)
    
    if new_state is None:
        return jsonify({
            'success': False,
            'error': 'Download not found'
        }), 404
    
    bookmark_count = get_user_bookmark_count(user_id)
    
    return jsonify({
        'success': True,
        'bookmarked': new_state,
        'bookmark_count': bookmark_count
    })


@app.route('/api/outputs/<int:output_id>/bookmark', methods=['POST'])
@login_required
def toggle_render_bookmark_api(output_id):
    """Toggle bookmark on a finished render. Enforces per-tier bookmark limits."""
    from .database import toggle_branded_output_bookmark, get_user_render_bookmark_count

    user_id = session['user_id']
    tier = get_user_tier(user_id)
    limits = get_effective_limits(tier, get_user_special_status(user_id))
    max_bookmarks = limits.get('max_render_bookmarks', 5)

    # Check limit only when bookmarking (not unbookmarking)
    current_count = get_user_render_bookmark_count(user_id)
    # Peek at current state to know if this is a bookmark or unbookmark
    from .database import get_connection
    with get_connection() as _conn:
        _row = _conn.execute(
            'SELECT bookmarked FROM branded_outputs WHERE id = ? AND user_id = ?',
            (output_id, user_id)
        ).fetchone()
    if _row is None:
        return jsonify({'success': False, 'error': 'Render not found'}), 404
    is_currently_bookmarked = bool(_row['bookmarked'])

    if not is_currently_bookmarked and max_bookmarks != -1 and current_count >= max_bookmarks:
        return jsonify({
            'success': False,
            'error': f'Bookmark limit reached ({max_bookmarks} for {tier}). Upgrade to save more renders.',
            'limit_reached': True,
            'max_bookmarks': max_bookmarks,
        }), 403

    new_state = toggle_branded_output_bookmark(output_id, user_id)
    return jsonify({
        'success': True,
        'bookmarked': new_state,
        'bookmark_count': get_user_render_bookmark_count(user_id),
        'max_bookmarks': max_bookmarks,
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
@login_required
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
@login_required
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
@login_required
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



@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    """
    Global catch-all for unhandled exceptions.
    Prevents raw Werkzeug 500 pages from leaking tracebacks to users.
    DB OperationalErrors (disk I/O, lock) are handled with a specific message.
    """
    import traceback as _tb
    from werkzeug.exceptions import HTTPException

    # HTTPExceptions (404, 403, 405, abort()…) pass through with their original status.
    # e.get_response() works in both Flask 1.x and 2.x; bare `return e` is 2.x-only.
    if isinstance(e, HTTPException):
        return e.get_response()

    tb_str = _tb.format_exc()

    if isinstance(e, sqlite3.OperationalError):
        print(f"[GLOBAL HANDLER] DB OperationalError on {request.path}: {e}")
        print(f"[GLOBAL HANDLER] Traceback:\n{tb_str}")
        _log_disk_health_warning()
        user_msg = (
            "Brandr is temporarily unavailable — the database is under heavy load "
            "or the disk is full. Please refresh or try again in a moment."
        )
    else:
        print(f"[GLOBAL HANDLER] Unhandled {type(e).__name__} on {request.path}: {e}")
        print(f"[GLOBAL HANDLER] Traceback:\n{tb_str}")
        user_msg = "Something went wrong on our end. Please refresh or try again shortly."

    # API routes → JSON
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': user_msg}), 500

    # HTML routes → friendly error card
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Brandr — Temporarily Unavailable</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, sans-serif;
      background: #121214; color: #eaeaea;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
    }
    .card {
      background: #1e1e22; border: 1px solid #333; border-radius: 12px;
      padding: 2.5rem 3rem; max-width: 480px; width: 90%; text-align: center;
    }
    h1 { font-size: 1.4rem; margin-bottom: 1rem; color: #fff; }
    p { color: #a0a0a0; line-height: 1.6; margin-bottom: 1.5rem; }
    a {
      display: inline-block; padding: 0.6rem 1.5rem;
      background: #C7A53C; color: #121214; border-radius: 6px;
      text-decoration: none; font-weight: 600;
    }
    a:hover { background: #d4b44e; }
  </style>
</head>
<body>
  <div class="card">
    <h1>⚠️ Something went wrong</h1>
    <p>{{ message }}</p>
    <a href="/portal/dashboard">Back to Dashboard</a>
  </div>
</body>
</html>
''', message=user_msg), 500


def schedule_cleanup():
    """Schedule periodic cleanup of old files"""
    import time
    import threading
    from .database import (cleanup_old_downloads, cleanup_old_branded_outputs,
                           sweep_normalized_temp_files)

    SWEEP_INTERVAL = 30 * 60     # 30 min — normalized temp sweep cadence
    FULL_CLEANUP_EVERY = 12      # full age-based cleanup every 12 sweeps (~6h)

    def cleanup_worker():
        tick = 0
        while True:
            try:
                # Frequent: clear stale normalized temp files (>30 min old). These are
                # the bulk of RAW_DIR and accumulate fast; the 24h cleanup is too slow.
                swept = sweep_normalized_temp_files(30)
                if swept:
                    print(f"[CLEANUP] Swept {swept} stale normalized temp files")

                # Periodic (~6h): age-based cleanup of downloads + expired renders.
                if tick % FULL_CLEANUP_EVERY == 0:
                    dl_count = cleanup_old_downloads(24)
                    render_count = cleanup_old_branded_outputs(24)
                    print(f"[CLEANUP] Deleted {dl_count} old downloads and {render_count} expired renders")

                tick += 1
                time.sleep(SWEEP_INTERVAL)
            except Exception as e:
                print(f"[CLEANUP] Error during cleanup: {e}")
                time.sleep(60 * 60)
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
    cleanup_thread.start()


# Schedule periodic cleanup
schedule_cleanup()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
