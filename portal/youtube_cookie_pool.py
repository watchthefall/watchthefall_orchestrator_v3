"""
YouTube cookie pool - discovery, validation, least-recently-used rotation,
and per-cookie health tracking with cooldown on auth/bot-gate failures.

Env var scheme:
    YOUTUBE_COOKIES          base cookie file contents
    YOUTUBE_COOKIES_1 .. _10 numbered pool members

The environment remains the source of truth. Values are written to runtime
cookie files for yt-dlp's cookiefile option and are never logged or returned.
"""
import os
import threading
import time

MAX_POOL = 10
COOLDOWN_SECONDS = 30 * 60

_lock = threading.Lock()
_pool = []
_health = {}


def _cookie_lines(text):
    """Non-comment Netscape cookie lines."""
    for line in (text or '').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '\t' in line:
            yield line


def _looks_valid(text):
    """Validate format, not liveness.

    Browser exports for YouTube commonly include both youtube.com and google.com
    domains, and the exact auth cookie names vary. Requiring a tab-separated
    Netscape cookie line scoped to either domain keeps malformed values out
    without hardcoding a brittle list of cookie names.
    """
    for line in _cookie_lines(text):
        domain = line.split('\t', 1)[0].lower()
        if 'youtube.com' in domain or 'google.com' in domain:
            return True
    return False


NETSCAPE_HEADER = '# Netscape HTTP Cookie File'


def _with_netscape_header(text):
    """Guarantee the header line yt-dlp insists on.

    yt-dlp refuses a cookie file whose first line is not the Netscape header:
    "does not look like a Netscape format cookies file". A value can therefore
    hold perfectly good cookie lines, pass _looks_valid(), enter the pool, and
    then fail EVERY fetch -- and because that failure is not an auth failure it
    would not even rotate. Environment variables lose leading lines easily
    (a copy-paste that starts at the first cookie, a here-doc, a dashboard field
    that trims), so this is a realistic input, not a hypothetical one.

    Adding the header when it is missing is safe: the header is a constant, it
    carries no data, and a value that already has it is left byte-identical.
    """
    stripped = (text or '').lstrip()
    if stripped.startswith('# Netscape HTTP Cookie File'):
        return text
    return NETSCAPE_HEADER + '\n' + (text or '')


def bootstrap_pool(pool_dir):
    """Write valid YOUTUBE_COOKIES* env vars to runtime files and register them."""
    global _pool, _health
    out_dir = os.path.join(pool_dir, 'youtube_pool')
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        print(f"[YOUTUBE COOKIE POOL] could not create pool dir {out_dir}: {e}")
        return []

    slots = [('base', 'YOUTUBE_COOKIES')]
    slots += [(str(i), f'YOUTUBE_COOKIES_{i}') for i in range(1, MAX_POOL + 1)]

    pool = []
    health = {}
    for slot, env_name in slots:
        raw = os.environ.get(env_name, '')
        if not raw.strip():
            continue
        if not _looks_valid(raw):
            print(f"[YOUTUBE COOKIE POOL] {env_name} set but no youtube/google "
                  f"Netscape cookie lines found - skipping")
            continue
        path = os.path.join(out_dir, f'cookies_{slot}.txt')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(_with_netscape_header(raw))
        except OSError as e:
            print(f"[YOUTUBE COOKIE POOL] could not write {path}: {e}")
            continue
        pool.append(path)
        health[path] = {'last_used': 0.0, 'cooldown_until': 0.0, 'fails': 0}
        print(f"[YOUTUBE COOKIE POOL] loaded {env_name} -> {os.path.basename(path)}")

    with _lock:
        _pool = pool
        _health = health
    print(f"[YOUTUBE COOKIE POOL] {len(pool)} cookie(s) in pool")
    return pool


def pool_size():
    with _lock:
        return len(_pool)


def candidates_lru():
    """Usable cookie paths, least-recently-used first."""
    now = time.time()
    with _lock:
        usable = [p for p in _pool if _health[p]['cooldown_until'] <= now]
        return sorted(usable, key=lambda p: _health[p]['last_used'])


def mark_used(path):
    with _lock:
        h = _health.get(path)
        if h:
            h['last_used'] = time.time()


def mark_success(path):
    with _lock:
        h = _health.get(path)
        if h:
            h['fails'] = 0
            h['cooldown_until'] = 0.0


def mark_bad(path):
    with _lock:
        h = _health.get(path)
        if h:
            h['fails'] += 1
            h['cooldown_until'] = time.time() + COOLDOWN_SECONDS
            fails = h['fails']
        else:
            fails = 0
    if fails:
        print(f"[YOUTUBE COOKIE ALERT] {os.path.basename(path)} failed auth "
              f"({fails} total) - cooling down {COOLDOWN_SECONDS // 60}min")


_AUTH_SIGNATURES = (
    'sign in to confirm',
    "confirm you're not a bot",
    'confirm you are not a bot',
    'not a bot',
    'use --cookies',
    'use --cookies-from-browser',
    'cookies from browser',
    'login required',
    'login_required',
    'http error 401',
    'http error 403',
    'forbidden',
    'this video may be inappropriate',
    'age-restricted',
    'age restricted',
    # Not an auth failure, but unambiguously a THIS-COOKIE failure: yt-dlp
    # rejected the cookie file itself. _with_netscape_header() should make it
    # unreachable; it stays here as depth, so a cookie file that is unusable for
    # any reason rotates past rather than killing the whole fetch with a raw
    # server filesystem path in the user-facing error.
    'does not look like a netscape format cookies file',
)


def is_auth_failure(error_text):
    """True when another YouTube cookie may plausibly fix this yt-dlp failure."""
    if not error_text:
        return False
    low = error_text.lower()
    return any(sig in low for sig in _AUTH_SIGNATURES)


def health_snapshot():
    now = time.time()
    with _lock:
        return [
            {
                'name': os.path.basename(p),
                'fails': _health[p]['fails'],
                'cooling_down': _health[p]['cooldown_until'] > now,
                'cooldown_remaining_s': max(0, int(_health[p]['cooldown_until'] - now)),
                'last_used_ago_s': (int(now - _health[p]['last_used'])
                                    if _health[p]['last_used'] else None),
            }
            for p in _pool
        ]
