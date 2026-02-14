"""
Database models and initialization
"""
import sqlite3
import json
from datetime import datetime
from .config import DB_PATH

def init_db():
    """Initialize database with required tables"""
    print(f"[DATABASE] Initializing database at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
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
            logo_scale REAL DEFAULT 0.15,
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
            logo_scale REAL DEFAULT 0.15,
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
    
    conn.commit()
    conn.close()
    print("[DATABASE] Database initialized successfully")

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def create_job(job_id, video_filename, template, aspect_ratio='9:16', metadata=None):
    """Create a new job"""
    conn = get_db()
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
    conn.close()
    
    log_event('info', job_id, f'Job created: {template} template, {aspect_ratio}')
    return job_id

def update_job_status(job_id, status, output_path=None, error_message=None):
    """Update job status"""
    conn = get_db()
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
    conn.close()
    
    log_event('info', job_id, f'Status updated: {status}')

def get_job(job_id):
    """Get job by ID"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_recent_jobs(limit=20):
    """Get recent jobs"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def log_event(level, job_id, message, details=None):
    """Log an event"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO logs (timestamp, level, job_id, message, details)
        VALUES (?, ?, ?, ?, ?)
    ''', (datetime.utcnow().isoformat(), level, job_id, message, json.dumps(details) if details else None))
    conn.commit()
    conn.close()

def get_recent_logs(limit=50):
    """Get recent logs"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_next_queued_job():
    """Get next job from queue"""
    conn = get_db()
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
    
    conn.close()
    return row[0] if row else None

def remove_from_queue(job_id):
    """Remove job from queue"""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM queue WHERE job_id = ?', (job_id,))
    conn.commit()
    conn.close()

# ============================================================================
# BRAND CONFIG FUNCTIONS
# ============================================================================

def get_brand_config(brand_name):
    """Get brand configuration by name"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM brand_configs WHERE brand_name = ?', (brand_name,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    
    # Return defaults if no saved config
    return {
        'brand_name': brand_name,
        'watermark_scale': 1.15,
        'watermark_opacity': 0.4,
        'logo_scale': 0.15,
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
    conn = get_db()
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
            config.get('logo_scale', 0.15),
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
            config.get('logo_scale', 0.15),
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
    conn.close()
    return True

def get_all_brand_configs():
    """Get all brand configurations"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM brand_configs ORDER BY brand_name')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ============================================================================
# UNIFIED BRANDS TABLE FUNCTIONS
# ============================================================================

def get_brand(brand_id=None, name=None, user_id=None):
    """Get a brand by ID or name"""
    conn = get_db()
    c = conn.cursor()
    
    if brand_id:
        c.execute('SELECT * FROM brands WHERE id = ?', (brand_id,))
    elif name:
        if user_id is not None:
            c.execute('SELECT * FROM brands WHERE name = ? AND (user_id = ? OR user_id IS NULL)', (name, user_id))
        else:
            c.execute('SELECT * FROM brands WHERE name = ?', (name,))
    else:
        conn.close()
        return None
    
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_brands(user_id=None, include_system=True):
    """Get all brands for a user (including system brands if include_system=True)"""
    conn = get_db()
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
    conn.close()
    return [dict(row) for row in rows]

def create_brand(name, display_name, user_id=None, is_system=False, is_locked=False,
                 watermark_vertical=None, watermark_square=None, watermark_landscape=None,
                 logo_path=None, **config):
    """Create a new brand"""
    conn = get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    c.execute('''
        INSERT INTO brands (
            name, display_name, user_id, is_system, is_locked, is_active,
            watermark_vertical, watermark_square, watermark_landscape, logo_path,
            watermark_scale, watermark_opacity, logo_scale, logo_padding,
            text_enabled, text_content, text_position, text_size, text_color,
            text_font, text_bg_enabled, text_bg_color, text_bg_opacity, text_margin,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        name, display_name, user_id, 1 if is_system else 0, 1 if is_locked else 0,
        watermark_vertical, watermark_square, watermark_landscape, logo_path,
        config.get('watermark_scale', 1.15),
        config.get('watermark_opacity', 0.4),
        config.get('logo_scale', 0.15),
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
        now, now
    ))
    
    brand_id = c.lastrowid
    conn.commit()
    conn.close()
    return brand_id

def update_brand(brand_id, **updates):
    """Update a brand's properties"""
    conn = get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    # Build dynamic update query
    allowed_fields = [
        'name', 'display_name', 'is_active', 'is_locked',
        'watermark_vertical', 'watermark_square', 'watermark_landscape', 'logo_path',
        'watermark_scale', 'watermark_opacity', 'logo_scale', 'logo_padding',
        'text_enabled', 'text_content', 'text_position', 'text_size', 'text_color',
        'text_font', 'text_bg_enabled', 'text_bg_color', 'text_bg_opacity', 'text_margin'
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
        conn.close()
        return False
    
    set_clauses.append('updated_at = ?')
    params.append(now)
    params.append(brand_id)
    
    c.execute(f'''
        UPDATE brands SET {', '.join(set_clauses)} WHERE id = ?
    ''', params)
    
    conn.commit()
    conn.close()
    return True

def delete_brand(brand_id):
    """Soft delete a brand (set is_active = 0)"""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE brands SET is_active = 0, updated_at = ? WHERE id = ?',
              (datetime.utcnow().isoformat(), brand_id))
    conn.commit()
    conn.close()
    return True

def seed_system_brands():
    """Seed system brands from WTF_MASTER_ASSETS"""
    import os
    try:
        from .config import PROJECT_ROOT
    except ImportError:
        PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    conn = get_db()
    c = conn.cursor()
    
    # Check if brands already seeded
    c.execute('SELECT COUNT(*) FROM brands WHERE is_system = 1')
    count = c.fetchone()[0]
    if count > 0:
        print(f"[DATABASE] System brands already seeded ({count} brands)")
        conn.close()
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
    conn.close()
    print(f"[DATABASE] Seeded {seeded} system brands")

# Initialize database on import
init_db()

# Seed system brands if not already done
seed_system_brands()
