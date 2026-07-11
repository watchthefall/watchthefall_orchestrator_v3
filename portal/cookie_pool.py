"""
Instagram cookie pool — discovery, validation, least-recently-used rotation,
and per-cookie health tracking with cooldown on auth failure.

Replaces the never-wired ``cookie_utils.py``. Instagram requires session cookies
to download; a single cookie is a single point of failure (when its ``sessionid``
expires, ALL Instagram fetches break). This module lets Brandr hold several
cookies (ideally from different accounts) and rotate across them.

Env var scheme (discovered once, at bootstrap):
    INSTAGRAM_COOKIES          base / legacy single cookie (behaviour preserved)
    INSTAGRAM_COOKIES_1 .. _10 numbered pool members

If only ``INSTAGRAM_COOKIES`` is set, the pool has one member and behaviour is
identical to before (one cookie, no rotation).

Rotation contract (enforced by the caller in app.py):
  * pick the least-recently-used cookie that isn't cooling down
  * on an auth failure (403 / "empty media response" / login-required), try the
    next cookie
  * a cookie is only cooled down when it fails AND a different cookie then
    SUCCEEDS on the same request — so a private/deleted post (every cookie
    fails) never knocks the whole pool offline
  * if every cookie fails, the caller shows one friendly error

Safe on a single Gunicorn worker (WEB_CONCURRENCY=1). A lock still guards the
in-memory health map for the case where one fetch downloads several URLs.
"""
import os
import time
import threading

MAX_POOL = 10
COOLDOWN_SECONDS = 30 * 60   # a cookie proven dead rests this long before retry

_lock = threading.Lock()
_pool = []      # ordered list of cookie file paths on disk
# path -> {'last_used': float, 'cooldown_until': float, 'fails': int}
_health = {}


def _looks_valid(text):
    """Usable if it's a Netscape cookie file carrying the Instagram auth cookies.

    We check for ``sessionid`` + ``csrftoken`` (what yt-dlp needs to authenticate).
    This validates FORMAT, not liveness — an expired-but-well-formed cookie still
    passes here and is weeded out at runtime by the rotation/cooldown logic."""
    if not text:
        return False
    return 'sessionid' in text and 'csrftoken' in text


def bootstrap_pool(pool_dir):
    """Read ``INSTAGRAM_COOKIES`` and ``INSTAGRAM_COOKIES_1..10`` from the
    environment, write each valid one to ``<pool_dir>/pool/cookies_<slot>.txt``,
    and register it. Returns the list of pool file paths.

    Called from config.py at import. Rewrites files every boot (the env is the
    source of truth), which is correct on Render where the disk is ephemeral.
    """
    global _pool, _health
    out_dir = os.path.join(pool_dir, 'pool')
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        print(f"[COOKIE POOL] could not create pool dir {out_dir}: {e}")
        return []

    # Base first (legacy), then the numbered slots.
    slots = [('base', 'INSTAGRAM_COOKIES')]
    slots += [(str(i), f'INSTAGRAM_COOKIES_{i}') for i in range(1, MAX_POOL + 1)]

    pool = []
    health = {}
    for slot, env_name in slots:
        raw = os.environ.get(env_name, '')
        if not raw.strip():
            continue
        if not _looks_valid(raw):
            print(f"[COOKIE POOL] {env_name} set but missing sessionid/csrftoken "
                  f"— skipping")
            continue
        path = os.path.join(out_dir, f'cookies_{slot}.txt')
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(raw)
        except OSError as e:
            print(f"[COOKIE POOL] could not write {path}: {e}")
            continue
        pool.append(path)
        health[path] = {'last_used': 0.0, 'cooldown_until': 0.0, 'fails': 0}
        print(f"[COOKIE POOL] loaded {env_name} -> {os.path.basename(path)}")

    with _lock:
        _pool = pool
        _health = health
    print(f"[COOKIE POOL] {len(pool)} cookie(s) in pool")
    return pool


def pool_size():
    with _lock:
        return len(_pool)


def candidates_lru():
    """Snapshot of usable cookie paths (cooldown elapsed), least-recently-used
    first. Empty list means the pool is empty or every cookie is cooling down."""
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
    """A cookie worked — clear its failure state."""
    with _lock:
        h = _health.get(path)
        if h:
            h['fails'] = 0
            h['cooldown_until'] = 0.0


def mark_bad(path):
    """A cookie is proven dead (it failed and another cookie then succeeded) —
    cool it down so rotation skips it until it (maybe) recovers or is refreshed."""
    with _lock:
        h = _health.get(path)
        if h:
            h['fails'] += 1
            h['cooldown_until'] = time.time() + COOLDOWN_SECONDS
            fails = h['fails']
    if h:
        print(f"[COOKIE ALERT] {os.path.basename(path)} failed auth "
              f"({fails} total) — cooling down {COOLDOWN_SECONDS // 60}min")


# Substrings that indicate an Instagram auth/cookie problem (rotate to another
# cookie) rather than a content problem like a private/removed post (don't
# rotate — no cookie will fix it). Instagram's "empty media response" is the
# signature seen when a sessionid is expired/invalidated.
_AUTH_SIGNATURES = (
    'empty media response',
    'login required',
    'login_required',
    'http error 403',
    'forbidden',
    'checkpoint_required',
    'challenge_required',
    'rate limit',
    'rate-limit',
    'please wait a few minutes',
)


def is_auth_failure(error_text):
    """True if a yt-dlp error looks like an authentication/cookie problem."""
    if not error_text:
        return False
    low = error_text.lower()
    return any(sig in low for sig in _AUTH_SIGNATURES)


def health_snapshot():
    """Observability: per-cookie state for an admin/debug view."""
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
