"""
Database models and initialization
"""
import sqlite3
import json
import time
from contextlib import contextmanager
from datetime import datetime
from .config import DB_PATH


@contextmanager
def get_connection():
    """Context manager for database connections. Guarantees conn.close() even on exceptions."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        yield conn
    finally:
        conn.close()


def _retry_write(fn, max_retries=5):
    """Execute a write function with retry on database lock.
    fn receives a connection and should perform the write + commit.
    Returns whatever fn returns."""
    backoff_times = [0.2, 0.4, 0.8, 1.6, 3.2]
    for attempt in range(max_retries):
        try:
            with get_connection() as conn:
                result = fn(conn)
                return result
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < max_retries - 1:
                wait_time = backoff_times[attempt]
                print(f"[DATABASE] Locked on attempt {attempt + 1}/{max_retries}, retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                raise
    return None

def init_db():
    """Initialize database with required tables"""
    print(f"[DATABASE] Initializing database at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        # Enable WAL mode for better concurrency (persists at DB level)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA busy_timeout=30000')
        
        c = conn.cursor()
        
        # Jobs table
        c.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                video_filename TEXT,
                template TEXT,
                aspect_ratio TEXT,
                output_path TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                metadata TEXT
            )
        ''')
        
        # Logs table
        c.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                job_id TEXT,
                message TEXT NOT NULL,
                details TEXT
            )
        ''')
        
        # Queue table
        c.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL,
                priority INTEGER DEFAULT 0,
                added_at TEXT NOT NULL,
                processing BOOLEAN DEFAULT 0
            )
        ''')
        
        # Store sync table (placeholder for future)
        c.execute('''
            CREATE TABLE IF NOT EXISTS store_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT,
                sync_status TEXT,
                last_sync TEXT,
                metadata TEXT
            )
        ''')
        
        # Brand configurations table - per-brand persistent settings
        c.execute('''
            CREATE TABLE IF NOT EXISTS brand_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                brand_name TEXT UNIQUE NOT NULL,
                user_id TEXT,
                watermark_scale REAL DEFAULT 1.15,
                watermark_opacity REAL DEFAULT 0.4,
                logo_scale REAL DEFAULT 0.25,
                logo_padding INTEGER DEFAULT 40,
                text_enabled INTEGER DEFAULT 0,
                text_content TEXT DEFAULT '',
                text_position TEXT DEFAULT 'bottom',
                text_size INTEGER DEFAULT 48,
                text_color TEXT DEFAULT '#FFFFFF',
                text_font TEXT DEFAULT 'Arial',
                text_bg_enabled INTEGER DEFAULT 1,
                text_bg_color TEXT DEFAULT '#000000',
                text_bg_opacity REAL DEFAULT 0.6,
                text_margin INTEGER DEFAULT 40,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # Unified brands table - full brand ownership and assets
        c.execute('''
            CREATE TABLE IF NOT EXISTS brands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                user_id INTEGER,
                is_system INTEGER DEFAULT 0,
                is_locked INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                watermark_vertical TEXT,
                watermark_square TEXT,
                watermark_landscape TEXT,
                logo_path TEXT,
                watermark_scale REAL DEFAULT 1.15,
                watermark_opacity REAL DEFAULT 0.4,
                logo_scale REAL DEFAULT 0.25,
                logo_padding INTEGER DEFAULT 40,
                text_enabled INTEGER DEFAULT 0,
                text_content TEXT DEFAULT '',
                text_position TEXT DEFAULT 'bottom',
                text_size INTEGER DEFAULT 48,
                text_color TEXT DEFAULT '#FFFFFF',
                text_font TEXT DEFAULT 'Arial',
                text_bg_enabled INTEGER DEFAULT 1,
                text_bg_color TEXT DEFAULT '#000000',
                text_bg_opacity REAL DEFAULT 0.6,
                text_margin INTEGER DEFAULT 40,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(name, user_id)
            )
        ''')
        
        # Downloads table - track user downloads
        c.execute('''
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                source_url TEXT,
                filename TEXT,
                display_name TEXT,
                file_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Daily usage tracking table
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                branding_jobs INTEGER DEFAULT 0,
                downloads INTEGER DEFAULT 0,
                UNIQUE(user_id, usage_date)
            )
        ''')
        
        conn.commit()
        print("[DATABASE] Database initialized successfully")
    finally:
        conn.close()
    
    # Run migrations
    _run_migrations()

def _run_migrations():
    """Run database migrations"""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute('PRAGMA busy_timeout=30000')
    c = conn.cursor()
    try:
        # Migration: Add text_x and text_y to brands table
        try:
            c.execute("SELECT text_x FROM brands LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding text_x and text_y columns")
            c.execute("ALTER TABLE brands ADD COLUMN text_x INTEGER DEFAULT 0")
            c.execute("ALTER TABLE brands ADD COLUMN text_y INTEGER DEFAULT 0")
            conn.commit()
            print("[DATABASE] Migration completed: text_x, text_y added")
        
        # Migration: Add visual positioning columns for drag-and-drop editor
        try:
            c.execute("SELECT logo_x FROM brands LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding visual positioning columns")
            # Logo positioning
            c.execute("ALTER TABLE brands ADD COLUMN logo_x REAL DEFAULT 0.85")
            c.execute("ALTER TABLE brands ADD COLUMN logo_y REAL DEFAULT 0.85")
            c.execute("ALTER TABLE brands ADD COLUMN logo_opacity REAL DEFAULT 1.0")
            # Watermark positioning
            c.execute("ALTER TABLE brands ADD COLUMN wm_mode TEXT DEFAULT 'fullscreen'")
            c.execute("ALTER TABLE brands ADD COLUMN wm_x REAL DEFAULT 0.5")
            c.execute("ALTER TABLE brands ADD COLUMN wm_y REAL DEFAULT 0.5")
            c.execute("ALTER TABLE brands ADD COLUMN wm_scale REAL DEFAULT 1.0")
            c.execute("ALTER TABLE brands ADD COLUMN wm_opacity REAL DEFAULT 0.10")
            # Text positioning (convert old text_x/text_y to REAL, add new fields)
            c.execute("ALTER TABLE brands ADD COLUMN text_x_percent REAL DEFAULT 0.5")
            c.execute("ALTER TABLE brands ADD COLUMN text_y_percent REAL DEFAULT 0.2")
            conn.commit()
            print("[DATABASE] Migration completed: visual positioning columns added")
        
        # Migration: Remove all SYSTEM brands for SaaS model (one-time cleanup)
        try:
            c.execute("SELECT COUNT(*) FROM brands WHERE is_system = 1")
            system_brand_count = c.fetchone()[0]
            if system_brand_count > 0:
                print(f"[DATABASE] Running migration: Removing {system_brand_count} SYSTEM brands for SaaS model")
                c.execute("DELETE FROM brands WHERE is_system = 1")
                conn.commit()
                print(f"[DATABASE] Migration completed: Removed {system_brand_count} SYSTEM brands")
        except Exception as e:
            print(f"[DATABASE] Migration warning (system brands cleanup): {e}")
        
        # Migration: Add watermark_path for unified watermark asset storage
        try:
            c.execute("SELECT watermark_path FROM brands LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding watermark_path column")
            c.execute("ALTER TABLE brands ADD COLUMN watermark_path TEXT DEFAULT NULL")
            conn.commit()
            print("[DATABASE] Migration completed: watermark_path added")
        
        # Migration: Add logo_shape for circle/square clipping
        try:
            c.execute("SELECT logo_shape FROM brands LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding logo_shape column")
            c.execute("ALTER TABLE brands ADD COLUMN logo_shape TEXT DEFAULT 'original'")
            conn.commit()
            print("[DATABASE] Migration completed: logo_shape added")
        
        # Migration: Add logo_rotation for brand-default logo rotation (degrees, 0-360)
        try:
            c.execute("SELECT logo_rotation FROM brands LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding logo_rotation column")
            c.execute("ALTER TABLE brands ADD COLUMN logo_rotation REAL DEFAULT 0.0")
            conn.commit()
            print("[DATABASE] Migration completed: logo_rotation added")
        
        # Migration: Add display_name to downloads table
        try:
            c.execute("SELECT display_name FROM downloads LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Adding display_name to downloads")
            c.execute("ALTER TABLE downloads ADD COLUMN display_name TEXT DEFAULT NULL")
            conn.commit()
            # Backfill: Set display_name to filename for existing rows
            c.execute("UPDATE downloads SET display_name = filename WHERE display_name IS NULL")
            conn.commit()
            print("[DATABASE] Migration completed: display_name added and backfilled")
        
        # Migration: Create daily_usage table for existing databases
        try:
            c.execute("SELECT id FROM daily_usage LIMIT 1")
        except sqlite3.OperationalError:
            print("[DATABASE] Running migration: Creating daily_usage table")
            c.execute('''
                CREATE TABLE IF NOT EXISTS daily_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    usage_date TEXT NOT NULL,
                    branding_jobs INTEGER DEFAULT 0,
                    downloads INTEGER DEFAULT 0,
                    UNIQUE(user_id, usage_date)
                )
            ''')
            conn.commit()
            print("[DATABASE] Migration completed: daily_usage table created")
    finally:
        conn.close()

def get_db():
    """Get database connection with busy timeout.
    DEPRECATED: Prefer get_connection() context manager for guaranteed cleanup.
    WAL mode is persistent and set once in init_db(), not per-connection.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    return conn

def create_job(job_id, video_filename, template, aspect_ratio='9:16', metadata=None):
    """Create a new job"""
    with get_connection() as conn:
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO jobs (job_id, status, video_filename, template, aspect_ratio, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, 'queued', video_filename, template, aspect_ratio, 
              datetime.utcnow().isoformat(), json.dumps(metadata or {})))
        
        # Add to queue
        c.execute('''
            INSERT INTO queue (job_id, added_at)
            VALUES (?, ?)
        ''', (job_id, datetime.utcnow().isoformat()))
        
        conn.commit()
    
    log_event('info', job_id, f'Job created: {template} template, {aspect_ratio}')
    return job_id

def update_job_status(job_id, status, output_path=None, error_message=None):
    """Update job status"""
    with get_connection() as conn:
        c = conn.cursor()
        
        updates = ['status = ?']
        params = [status]
        
        if status == 'processing':
            updates.append('started_at = ?')
            params.append(datetime.utcnow().isoformat())
        elif status in ['completed', 'failed']:
            updates.append('completed_at = ?')
            params.append(datetime.utcnow().isoformat())
        
        if output_path:
            updates.append('output_path = ?')
            params.append(output_path)
        
        if error_message:
            updates.append('error_message = ?')
            params.append(error_message)
        
        params.append(job_id)
        
        c.execute(f'''
            UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?
        ''', params)
        
        conn.commit()
    
    log_event('info', job_id, f'Status updated: {status}')

def get_job(job_id):
    """Get job by ID"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
        row = c.fetchone()
        return dict(row) if row else None

def get_recent_jobs(limit=20):
    """Get recent jobs"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,))
        rows = c.fetchall()
        return [dict(row) for row in rows]

def log_event(level, job_id, message, details=None):
    """Log an event"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO logs (timestamp, level, job_id, message, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (datetime.utcnow().isoformat(), level, job_id, message, json.dumps(details) if details else None))
        conn.commit()

def get_recent_logs(limit=50):
    """Get recent logs"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?', (limit,))
        rows = c.fetchall()
        return [dict(row) for row in rows]

def get_next_queued_job():
    """Get next job from queue"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT q.job_id FROM queue q
            JOIN jobs j ON q.job_id = j.job_id
            WHERE q.processing = 0 AND j.status = 'queued'
            ORDER BY q.priority DESC, q.added_at ASC
            LIMIT 1
        ''')
        row = c.fetchone()
        
        if row:
            job_id = row[0]
            # Mark as processing
            c.execute('UPDATE queue SET processing = 1 WHERE job_id = ?', (job_id,))
            conn.commit()
        
        return row[0] if row else None

def remove_from_queue(job_id):
    """Remove job from queue"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM queue WHERE job_id = ?', (job_id,))
        conn.commit()

# ============================================================================
# BRAND CONFIG FUNCTIONS
# ============================================================================

def get_brand_config(brand_name):
    """Get brand configuration by name"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM brand_configs WHERE brand_name = ?', (brand_name,))
        row = c.fetchone()
    
    if row:
        return dict(row)
    
    # Return defaults if no saved config
    return {
        'brand_name': brand_name,
        'watermark_scale': 1.15,
        'watermark_opacity': 0.4,
        'logo_scale': 0.25,
        'logo_padding': 40,
        'text_enabled': 0,
        'text_content': '',
        'text_position': 'bottom',
        'text_size': 48,
        'text_color': '#FFFFFF',
        'text_font': 'Arial',
        'text_bg_enabled': 1,
        'text_bg_color': '#000000',
        'text_bg_opacity': 0.6,
        'text_margin': 40
    }

def save_brand_config(brand_name, config):
    """Save or update brand configuration"""
    def _do_save(conn):
        c = conn.cursor()
        now = datetime.utcnow().isoformat()
        
        # Check if exists
        c.execute('SELECT id FROM brand_configs WHERE brand_name = ?', (brand_name,))
        exists = c.fetchone()
        
        if exists:
            # Update
            c.execute('''
                UPDATE brand_configs SET
                    watermark_scale = ?,
                    watermark_opacity = ?,
                    logo_scale = ?,
                    logo_padding = ?,
                    text_enabled = ?,
                    text_content = ?,
                    text_position = ?,
                    text_size = ?,
                    text_color = ?,
                    text_font = ?,
                    text_bg_enabled = ?,
                    text_bg_color = ?,
                    text_bg_opacity = ?,
                    text_margin = ?,
                    updated_at = ?
                WHERE brand_name = ?
            ''', (
                config.get('watermark_scale', 1.15),
                config.get('watermark_opacity', 0.4),
                config.get('logo_scale', 0.25),
                config.get('logo_padding', 40),
                1 if config.get('text_enabled') else 0,
                config.get('text_content', ''),
                config.get('text_position', 'bottom'),
                config.get('text_size', 48),
                config.get('text_color', '#FFFFFF'),
                config.get('text_font', 'Arial'),
                1 if config.get('text_bg_enabled', True) else 0,
                config.get('text_bg_color', '#000000'),
                config.get('text_bg_opacity', 0.6),
                config.get('text_margin', 40),
                now,
                brand_name
            ))
        else:
            # Insert
            c.execute('''
                INSERT INTO brand_configs (
                    brand_name, watermark_scale, watermark_opacity, logo_scale, logo_padding,
                    text_enabled, text_content, text_position, text_size, text_color,
                    text_font, text_bg_enabled, text_bg_color, text_bg_opacity, text_margin,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                brand_name,
                config.get('watermark_scale', 1.15),
                config.get('watermark_opacity', 0.4),
                config.get('logo_scale', 0.25),
                config.get('logo_padding', 40),
                1 if config.get('text_enabled') else 0,
                config.get('text_content', ''),
                config.get('text_position', 'bottom'),
                config.get('text_size', 48),
                config.get('text_color', '#FFFFFF'),
                config.get('text_font', 'Arial'),
                1 if config.get('text_bg_enabled', True) else 0,
                config.get('text_bg_color', '#000000'),
                config.get('text_bg_opacity', 0.6),
                config.get('text_margin', 40),
                now,
                now
            ))
        
        conn.commit()
        return True
    
    return _retry_write(_do_save)

def get_all_brand_configs():
    """Get all brand configurations"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM brand_configs ORDER BY brand_name')
        rows = c.fetchall()
        return [dict(row) for row in rows]

# ============================================================================
# UNIFIED BRANDS TABLE FUNCTIONS
# ============================================================================

def get_brand(brand_id=None, name=None, user_id=None):
    """Get a brand by ID or name"""
    with get_connection() as conn:
        c = conn.cursor()
        
        if brand_id:
            c.execute('SELECT * FROM brands WHERE id = ?', (brand_id,))
        elif name:
            if user_id is not None:
                c.execute('SELECT * FROM brands WHERE name = ? AND (user_id = ? OR user_id IS NULL)', (name, user_id))
            else:
                c.execute('SELECT * FROM brands WHERE name = ?', (name,))
        else:
            return None
        
        row = c.fetchone()
    
    if row:
        brand = dict(row)
        # Add readiness flag: READY if logo OR watermark exists
        brand['is_ready'] = bool(brand.get('logo_path') or brand.get('watermark_path'))
        return brand
    return None

def get_all_brands(user_id=None, include_system=True):
    """Get all brands for a user (including system brands if include_system=True)"""
    with get_connection() as conn:
        c = conn.cursor()
        
        if user_id and include_system:
            # User's brands + system brands
            c.execute('''
                SELECT * FROM brands 
                WHERE (user_id = ? OR is_system = 1) AND is_active = 1
                ORDER BY is_system DESC, name
            ''', (user_id,))
        elif user_id:
            # Only user's brands
            c.execute('SELECT * FROM brands WHERE user_id = ? AND is_active = 1 ORDER BY name', (user_id,))
        else:
            # All system brands (for anonymous/guest)
            c.execute('SELECT * FROM brands WHERE is_system = 1 AND is_active = 1 ORDER BY name')
        
        rows = c.fetchall()
    
    brands = [dict(row) for row in rows]
    # Add readiness flag to all brands
    for brand in brands:
        brand['is_ready'] = bool(brand.get('logo_path') or brand.get('watermark_path'))
    
    return brands

def get_user_brand_count(user_id):
    """Return the number of active, non-system brands owned by a user."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT COUNT(*) FROM brands WHERE user_id = ? AND is_system = 0 AND is_active = 1',
            (user_id,)
        )
        return c.fetchone()[0]


def create_brand(name, display_name, user_id=None, is_system=False, is_locked=False,
                 watermark_vertical=None, watermark_square=None, watermark_landscape=None,
                 logo_path=None, **config):
    """Create a new brand with retry on database lock"""
    now = datetime.utcnow().isoformat()
    
    def _do_create(conn):
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        c.execute('''
            INSERT INTO brands (
                name, display_name, user_id, is_system, is_locked, is_active,
                watermark_vertical, watermark_square, watermark_landscape, logo_path,
                watermark_scale, watermark_opacity, logo_scale, logo_padding,
                text_enabled, text_content, text_position, text_x, text_y, text_size, text_color,
                text_font, text_bg_enabled, text_bg_color, text_bg_opacity, text_margin,
                logo_x, logo_y, logo_opacity, logo_rotation,
                wm_mode, wm_x, wm_y, wm_scale, wm_opacity,
                text_x_percent, text_y_percent,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name, display_name, user_id, 1 if is_system else 0, 1 if is_locked else 0,
            watermark_vertical, watermark_square, watermark_landscape, logo_path,
            config.get('watermark_scale', 1.15),
            config.get('watermark_opacity', 0.4),
            config.get('logo_scale', 0.25),
            config.get('logo_padding', 40),
            1 if config.get('text_enabled') else 0,
            config.get('text_content', ''),
            config.get('text_position', 'bottom'),
            config.get('text_x', 0),
            config.get('text_y', 0),
            config.get('text_size', 48),
            config.get('text_color', '#FFFFFF'),
            config.get('text_font', 'Arial'),
            1 if config.get('text_bg_enabled', True) else 0,
            config.get('text_bg_color', '#000000'),
            config.get('text_bg_opacity', 0.6),
            config.get('text_margin', 40),
            # Visual positioning fields
            config.get('logo_x', 0.85),
            config.get('logo_y', 0.85),
            config.get('logo_opacity', 1.0),
            config.get('logo_rotation', 0.0),
            config.get('wm_mode', 'fullscreen'),
            config.get('wm_x', 0.5),
            config.get('wm_y', 0.5),
            config.get('wm_scale', 1.0),
            config.get('wm_opacity', 0.10),
            config.get('text_x_percent', 0.5),
            config.get('text_y_percent', 0.2),
            now, now
        ))
        
        brand_id = c.lastrowid
        conn.commit()
        return brand_id
    
    return _retry_write(_do_create)

def update_brand(brand_id, **updates):
    """Update a brand's properties with retry on database lock"""
    now = datetime.utcnow().isoformat()
    
    # Build dynamic update query
    allowed_fields = [
        'name', 'display_name', 'is_active', 'is_locked',
        'watermark_vertical', 'watermark_square', 'watermark_landscape', 'logo_path', 'watermark_path',
        'watermark_scale', 'watermark_opacity', 'logo_scale', 'logo_padding',
        'text_enabled', 'text_content', 'text_position', 'text_x', 'text_y', 'text_size', 'text_color',
        'text_font', 'text_bg_enabled', 'text_bg_color', 'text_bg_opacity', 'text_margin',
        # Visual positioning fields
        'logo_x', 'logo_y', 'logo_opacity', 'logo_shape', 'logo_rotation',
        'wm_mode', 'wm_x', 'wm_y', 'wm_scale', 'wm_opacity',
        'text_x_percent', 'text_y_percent'
    ]
    
    set_clauses = []
    params = []
    
    for field in allowed_fields:
        if field in updates:
            set_clauses.append(f'{field} = ?')
            value = updates[field]
            # Handle boolean fields
            if field in ['is_active', 'is_locked', 'text_enabled', 'text_bg_enabled']:
                value = 1 if value else 0
            params.append(value)
    
    if not set_clauses:
        return False
    
    set_clauses.append('updated_at = ?')
    params.append(now)
    params.append(brand_id)
    
    query = f"UPDATE brands SET {', '.join(set_clauses)} WHERE id = ?"
    
    def _do_update(conn):
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        c.execute(query, params)
        conn.commit()
        return True
    
    return _retry_write(_do_update)

def delete_brand(brand_id):
    """Soft delete a brand (set is_active = 0)"""
    def _do_delete(conn):
        c = conn.cursor()
        c.execute('UPDATE brands SET is_active = 0, updated_at = ? WHERE id = ?',
                  (datetime.utcnow().isoformat(), brand_id))
        conn.commit()
        return True
    
    return _retry_write(_do_delete)

def seed_system_brands():
    """Seed system brands from WTF_MASTER_ASSETS"""
    import os
    try:
        from .config import PROJECT_ROOT
    except ImportError:
        PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    conn = get_db()
    try:
        c = conn.cursor()
        
        # Check if brands already seeded
        c.execute('SELECT COUNT(*) FROM brands WHERE is_system = 1')
        count = c.fetchone()[0]
        if count > 0:
            print(f"[DATABASE] System brands already seeded ({count} brands)")
            return
        
        print("[DATABASE] Seeding system brands from WTF_MASTER_ASSETS...")
        
        # Master asset paths
        master_root = os.path.join(PROJECT_ROOT, 'WTF_MASTER_ASSETS', 'Branding')
        watermarks_dir = os.path.join(master_root, 'Watermarks')
        logos_dir = os.path.join(master_root, 'Logos', 'Circle')
        
        # System brands to seed (based on existing logos)
        now = datetime.utcnow().isoformat()
        seeded = 0
        
        # Scan for logo files to determine brand list
        if os.path.exists(logos_dir):
            for filename in os.listdir(logos_dir):
                if filename.endswith('_logo.png'):
                    brand_name = filename.replace('_logo.png', '')
                    display_name = brand_name
                    
                    # Clean brand name for watermark search
                    clean_name = brand_name.replace('WTF', '').strip()
                    
                    # Find watermarks for each orientation
                    watermark_vertical = None
                    watermark_square = None
                    watermark_landscape = None
                    
                    for orientation in ['Vertical_HD', 'Square', 'Landscape']:
                        orient_dir = os.path.join(watermarks_dir, orientation)
                        if os.path.exists(orient_dir):
                            # Try different naming patterns
                            patterns = [
                                f"{clean_name}_watermark.png",
                                f"{clean_name.lower()}_watermark.png",
                                f"{clean_name.capitalize()}_watermark.png",
                            ]
                            for pattern in patterns:
                                path = os.path.join(orient_dir, pattern)
                                if os.path.exists(path):
                                    # Store relative path from project root
                                    rel_path = os.path.relpath(path, PROJECT_ROOT)
                                    if orientation == 'Vertical_HD':
                                        watermark_vertical = rel_path
                                    elif orientation == 'Square':
                                        watermark_square = rel_path
                                    else:
                                        watermark_landscape = rel_path
                                    break
                    
                    # Logo path
                    logo_path = os.path.relpath(os.path.join(logos_dir, filename), PROJECT_ROOT)
                    
                    # Insert brand
                    try:
                        c.execute('''
                            INSERT INTO brands (
                                name, display_name, user_id, is_system, is_locked, is_active,
                                watermark_vertical, watermark_square, watermark_landscape, logo_path,
                                watermark_scale, watermark_opacity, logo_scale, logo_padding,
                                text_enabled, text_content, text_position, text_size, text_color,
                                text_font, text_bg_enabled, text_bg_color, text_bg_opacity, text_margin,
                                created_at, updated_at
                            ) VALUES (?, ?, NULL, 1, 0, 1, ?, ?, ?, ?, 1.15, 0.4, 0.15, 40, 0, '', 'bottom', 48, '#FFFFFF', 'Arial', 1, '#000000', 0.6, 40, ?, ?)
                        ''', (brand_name, display_name, watermark_vertical, watermark_square, watermark_landscape, logo_path, now, now))
                        seeded += 1
                        print(f"  [SEED] Created brand: {brand_name}")
                    except sqlite3.IntegrityError:
                        print(f"  [SEED] Brand already exists: {brand_name}")
        
        conn.commit()
        print(f"[DATABASE] Seeded {seeded} system brands")
    finally:
        conn.close()


def save_download(user_id, source_url, filename, file_path, display_name=None):
    """Save a download record"""
    # Use filename as default display_name if not provided
    if display_name is None:
        display_name = filename
    
    def _do_save(conn):
        c = conn.cursor()
        c.execute('''
            INSERT INTO downloads (user_id, source_url, filename, display_name, file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, source_url, filename, display_name, file_path, datetime.utcnow().isoformat()))
        
        download_id = c.lastrowid
        conn.commit()
        return download_id
    
    return _retry_write(_do_save)

def update_display_name(download_id, user_id, display_name):
    """Update the display_name for a download (UI-only rename)"""
    def _do_update(conn):
        c = conn.cursor()
        
        # Verify ownership
        c.execute('SELECT id FROM downloads WHERE id = ? AND user_id = ?', (download_id, user_id))
        if not c.fetchone():
            return False
        
        c.execute('''
            UPDATE downloads 
            SET display_name = ? 
            WHERE id = ? AND user_id = ?
        ''', (display_name, download_id, user_id))
        
        conn.commit()
        return True
    
    return _retry_write(_do_update)

def get_user_downloads(user_id, limit=10):
    """Get recent downloads for a user"""
    with get_connection() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT * FROM downloads 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (user_id, limit))
        
        rows = c.fetchall()
        return [dict(row) for row in rows]

def get_download(download_id, user_id):
    """Get a specific download for a user"""
    with get_connection() as conn:
        c = conn.cursor()
        
        c.execute('''
            SELECT * FROM downloads 
            WHERE id = ? AND user_id = ?
        ''', (download_id, user_id))
        
        row = c.fetchone()
        return dict(row) if row else None

def cleanup_old_downloads(max_age_hours=24):
    """Delete downloads older than max_age_hours"""
    from datetime import datetime, timedelta
    import os
    
    cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
    
    with get_connection() as conn:
        c = conn.cursor()
        
        c.execute('''
            DELETE FROM downloads 
            WHERE created_at < ?
        ''', (cutoff_time.isoformat(),))
        
        deleted_count = c.rowcount
        conn.commit()
    
    # Also clean up old files from the storage directory
    cleanup_old_files(max_age_hours)
    
    return deleted_count

def cleanup_old_files(max_age_hours=24):
    """Delete old files from storage directories"""
    import os
    from datetime import datetime, timedelta
    from .config import RAW_DIR, OUTPUT_DIR
    
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    
    deleted_count = 0
    
    # Clean up both directories
    for directory in [RAW_DIR, OUTPUT_DIR]:
        if os.path.exists(directory):
            for filename in os.listdir(directory):
                filepath = os.path.join(directory, filename)
                if os.path.isfile(filepath):
                    # Check file modification time
                    file_modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if file_modified < cutoff_time:
                        try:
                            os.remove(filepath)
                            deleted_count += 1
                        except OSError as e:
                            print(f"[CLEANUP] Error deleting {filepath}: {e}")
    
    return deleted_count

# ============================================================================
# DAILY USAGE TRACKING
# ============================================================================

def _today_str():
    """Return today's date as YYYY-MM-DD string."""
    return datetime.utcnow().strftime('%Y-%m-%d')


def get_daily_usage(user_id):
    """Get today's usage counters for a user. Returns dict with branding_jobs, downloads."""
    today = _today_str()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            'SELECT branding_jobs, downloads FROM daily_usage WHERE user_id = ? AND usage_date = ?',
            (user_id, today)
        )
        row = c.fetchone()
    if row:
        return {'branding_jobs': row['branding_jobs'], 'downloads': row['downloads']}
    return {'branding_jobs': 0, 'downloads': 0}


def increment_branding_jobs(user_id, count=1):
    """Increment today's branding_jobs counter for a user."""
    today = _today_str()
    def _do_increment(conn):
        c = conn.cursor()
        c.execute('''
            INSERT INTO daily_usage (user_id, usage_date, branding_jobs, downloads)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(user_id, usage_date)
            DO UPDATE SET branding_jobs = branding_jobs + ?
        ''', (user_id, today, count, count))
        conn.commit()
        return True
    return _retry_write(_do_increment)


def increment_downloads(user_id, count=1):
    """Increment today's downloads counter for a user."""
    today = _today_str()
    def _do_increment(conn):
        c = conn.cursor()
        c.execute('''
            INSERT INTO daily_usage (user_id, usage_date, branding_jobs, downloads)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(user_id, usage_date)
            DO UPDATE SET downloads = downloads + ?
        ''', (user_id, today, count, count))
        conn.commit()
        return True
    return _retry_write(_do_increment)


# Initialize database on import
init_db()

# DO NOT seed system brands for SaaS - users create their own brands
# seed_system_brands()  # DISABLED for production SaaS model
