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

# ============================================================================
# TIER SYSTEM
# ============================================================================
# Base tiers: a user has exactly ONE tier
# Choke points: fetch (ingestion) + process_brands (transformation)
TIER_CONFIG = {
    'Explorer': {
        'label': 'Explorer',
        'price': 0,
        'color': '#86EDA5',       # mint green
        'accent': '#86EDA5',
        'badge_image': 'badges/explorer.png',
        'fetches_per_day': 25,
        'ig_per_hour': 3,
        'branding_jobs_per_day': 15,
        'max_brands_per_job': 3,
        'max_brand_configs': 1,
        'concurrent_jobs': 1,
    },
    'Creator': {
        'label': 'Creator',
        'price': 9,
        'color': '#F5A623',       # orange
        'accent': '#F5A623',
        'badge_image': 'badges/creator.png',
        'fetches_per_day': 100,
        'ig_per_hour': 10,
        'branding_jobs_per_day': 60,
        'max_brands_per_job': 8,
        'max_brand_configs': 5,
        'concurrent_jobs': 3,
    },
    'Studio': {
        'label': 'Studio',
        'price': 19,
        'color': '#A855F7',       # purple
        'accent': '#A855F7',
        'badge_image': 'badges/studio.png',
        'fetches_per_day': 200,
        'ig_per_hour': 15,
        'branding_jobs_per_day': 120,
        'max_brands_per_job': 20,
        'max_brand_configs': -1,  # unlimited
        'concurrent_jobs': 5,
        'priority_processing': True,
    },
    # Platinum: hidden future tier — system-level control, not just bigger numbers
    # Queue priority, batch campaigns, saved pipelines, team/multi-user (future)
    'Platinum': {
        'label': 'Platinum',
        'price': 49,
        'color': '#D4A017',       # gold
        'accent': '#D4A017',
        'badge_image': 'badges/legacy.png',
        'fetches_per_day': 500,
        'ig_per_hour': 30,
        'branding_jobs_per_day': 300,
        'max_brands_per_job': 50,
        'max_brand_configs': -1,
        'concurrent_jobs': 10,
        'priority_processing': True,
        'hidden': True,           # NOT shown in upgrade modal
    },
}

# Tiers visible in upgrade flow (excludes hidden tiers)
VISIBLE_TIERS = [k for k, v in TIER_CONFIG.items() if not v.get('hidden')]

DEFAULT_TIER = 'Explorer'

# ============================================================================
# SPECIAL STATUS (modifier, NOT a tier)
# ============================================================================
# A user may have ONE optional special_status that overrides tier limits.
# beta_tester visually overrides the tier badge.
SPECIAL_STATUSES = {
    'beta_tester': {
        'label': 'Beta Tester',
        'color': '#D4A017',       # gold
        'accent': '#D4A017',
        'badge_image': 'badges/beta_tester.png',
        'badge_priority': True,   # overrides tier badge visually
        'overrides': {
            'fetches_per_day': 9999,
            'branding_jobs_per_day': 9999,
            'max_brands_per_job': 100,
            'max_brand_configs': -1,
        },
    },
    # Future: uncomment when ready
    # 'legacy': {
    #     'label': 'Legacy',
    #     'color': '#D4A017',
    #     'accent': '#D4A017',
    #     'badge_image': 'badges/legacy.png',
    #     'badge_priority': True,
    #     'overrides': {},
    # },
}

# Admin emails — these users get admin console access + beta_tester status
ADMIN_EMAILS = [
    'wtf@watchthefall.com',
    'jamiemg96@gmail.com',
]

# PayPal payment links (no-code checkout)
PAYMENT_LINKS = {
    'Creator': 'https://www.paypal.com/ncp/payment/DJY4Q8DPKDULU',
    'Studio': '',  # TODO: Add Studio PayPal link when ready
}


def get_tier_limits(tier_name):
    """Return the limits dict for a given tier. Falls back to Explorer."""
    return TIER_CONFIG.get(tier_name, TIER_CONFIG[DEFAULT_TIER])


def get_effective_limits(tier_name, special_status=None):
    """Return limits with special_status overrides merged on top of tier base."""
    base = dict(get_tier_limits(tier_name))
    if special_status and special_status in SPECIAL_STATUSES:
        overrides = SPECIAL_STATUSES[special_status].get('overrides', {})
        for key, val in overrides.items():
            if key in base:
                # Override only if the special status value is more generous
                if val == -1 or (base[key] != -1 and val > base[key]):
                    base[key] = val
    return base


def get_next_visible_tier(current_tier):
    """Return the next visible tier above the current one, or None if at top."""
    try:
        idx = VISIBLE_TIERS.index(current_tier)
        if idx + 1 < len(VISIBLE_TIERS):
            name = VISIBLE_TIERS[idx + 1]
            cfg = TIER_CONFIG[name]
            return {'name': name, 'label': cfg['label'], 'color': cfg['color'],
                    'max_brands_per_job': cfg['max_brands_per_job'], 'price': cfg['price']}
    except ValueError:
        pass
    return None


def get_badge_info(tier_name, special_status=None):
    """Return badge image path, label, and color for display.
    Special status with badge_priority shows BOTH status and tier (Option B)."""
    tier_cfg = TIER_CONFIG.get(tier_name, TIER_CONFIG[DEFAULT_TIER])
    result = {
        'image': tier_cfg['badge_image'],
        'label': tier_cfg['label'],
        'color': tier_cfg['color'],
        'accent': tier_cfg['accent'],
        'tier': tier_name,
        'special_status': special_status,
        # Base tier info always preserved for dual-badge display
        'tier_label': tier_cfg['label'],
        'tier_color': tier_cfg['color'],
    }
    if special_status and special_status in SPECIAL_STATUSES:
        status_cfg = SPECIAL_STATUSES[special_status]
        if status_cfg.get('badge_priority'):
            # Option B: Show special status as primary, tier as secondary
            result['image'] = status_cfg['badge_image']
            result['label'] = status_cfg['label']  # Primary: special status
            result['color'] = status_cfg['color']
            result['accent'] = status_cfg['accent']
            result['secondary_label'] = tier_cfg['label']  # Secondary: base tier
    return result


def get_payment_link(tier_name):
    """Return PayPal payment link for a tier. Returns empty string if none."""
    return PAYMENT_LINKS.get(tier_name, '')

# Ensure directories exist
for directory in [STORAGE_ROOT, RAW_DIR, OUTPUT_DIR, BRANDS_DIR, UPLOAD_DIR, TEMP_DIR, LOG_DIR, os.path.dirname(DB_PATH)]:
    os.makedirs(directory, exist_ok=True)

# Log resolved paths (helps debug persistence issues)
print(f"[CONFIG] DB_PATH: {DB_PATH}")
print(f"[CONFIG] STORAGE_ROOT: {STORAGE_ROOT}")
print(f"[CONFIG] RAW_DIR: {RAW_DIR}")
print(f"[CONFIG] OUTPUT_DIR: {OUTPUT_DIR}")
print(f"[CONFIG] BRANDS_DIR: {BRANDS_DIR}")

# Cookie configuration - supports Render env var bootstrap
COOKIE_DIR = os.path.join(PORTAL_ROOT, 'data')
COOKIE_FILE = os.path.join(COOKIE_DIR, 'cookies.txt')
os.makedirs(COOKIE_DIR, exist_ok=True)

# Bootstrap cookies from environment variable (for Render/Production)
INSTAGRAM_COOKIES = os.environ.get('INSTAGRAM_COOKIES', '')
if INSTAGRAM_COOKIES:
    try:
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            f.write(INSTAGRAM_COOKIES)
        print(f"[CONFIG] Cookies written to: {COOKIE_FILE}")
    except Exception as e:
        print(f"[CONFIG] Warning: Failed to write cookies: {e}")
else:
    print(f"[CONFIG] No INSTAGRAM_COOKIES env var set, using existing cookie file if present")
