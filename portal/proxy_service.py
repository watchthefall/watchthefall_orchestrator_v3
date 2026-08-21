"""
DataImpulse residential proxy — isolated helper for Meta (Instagram/Threads) fetching.

WHY: Instagram rate-limits / blocks Render's datacenter IP. Routing ONLY the
Meta fetch traffic through a rotating residential gateway is the durable fix.

SCOPE — deliberately narrow:
  * Meta (Instagram + Threads) source fetching only.
  * YouTube and every other provider keep their existing paths untouched.
  * Normal Brandr traffic (the web app itself) is never proxied.

ROTATION: DataImpulse's gateway rotates the exit IP per connection, so Brandr
implements NO rotation of its own. A sticky session is only used if a caller
explicitly asks for one (see session_id) — the Instagram flow does not need it,
because each fetch is a single independent extract+download.

SECURITY: credentials live in env vars only, are never logged, never returned
by any endpoint, never written to the DB, and never sent to the frontend.
``redact()`` scrubs them from third-party error strings before they surface.

This module imports no project modules, so it is import-safe from config.py.
"""
import os
import re

GATEWAY_HOST = os.environ.get('DATAIMPULSE_HOST', 'gw.dataimpulse.com').strip()
GATEWAY_PORT = os.environ.get('DATAIMPULSE_PORT', '823').strip()
DEFAULT_COUNTRY = 'gb'


def _clean(name):
    return (os.environ.get(name, '') or '').strip()


def is_configured():
    """True when both DataImpulse credentials are present."""
    return bool(_clean('DATAIMPULSE_USERNAME') and _clean('DATAIMPULSE_PASSWORD'))


def get_country():
    """Target country code, lowercase. Empty string disables country targeting."""
    raw = _clean('DATAIMPULSE_COUNTRY')
    if raw == '':
        # Unset -> default GB. Explicit empty via DATAIMPULSE_COUNTRY=" " is
        # normalised to '' by strip(), which we treat as "no targeting".
        return DEFAULT_COUNTRY if 'DATAIMPULSE_COUNTRY' not in os.environ else ''
    return raw.lower()


def _build_username(session_id=None):
    """DataImpulse encodes targeting options as suffixes on the username:
        <user>__cr.<country>        country targeting
        <user>__sid.<id>            sticky session (same exit IP)
    Without a suffix the gateway rotates the exit IP on every connection."""
    username = _clean('DATAIMPULSE_USERNAME')
    country = get_country()
    if country:
        username += '__cr.' + country
    if session_id:
        safe = re.sub(r'[^A-Za-z0-9]', '', str(session_id))[:32]
        if safe:
            username += '__sid.' + safe
    return username


def get_proxy_url(session_id=None):
    """Full proxy URL for yt-dlp / requests, or None when unconfigured.

    CONTAINS CREDENTIALS — never log or return this value. Use describe()
    for anything human-visible.
    """
    if not is_configured():
        return None
    from urllib.parse import quote
    user = quote(_build_username(session_id), safe='')
    pwd = quote(_clean('DATAIMPULSE_PASSWORD'), safe='')
    return f"http://{user}:{pwd}@{GATEWAY_HOST}:{GATEWAY_PORT}"


def get_meta_proxy(session_id=None):
    """Proxy for Instagram/Threads fetching, with graceful degradation:
      1. DataImpulse residential when configured
      2. legacy IG_PROXY env var (preserves pre-existing behaviour)
      3. None -> caller fetches directly, exactly as before
    """
    url = get_proxy_url(session_id)
    if url:
        return url
    legacy = _clean('IG_PROXY')
    return legacy or None


def describe():
    """Credential-free description, safe for logs and admin responses."""
    if is_configured():
        country = get_country() or 'any'
        return f"DataImpulse residential {GATEWAY_HOST}:{GATEWAY_PORT} country={country}"
    if _clean('IG_PROXY'):
        return "legacy IG_PROXY"
    return "none (direct)"


def redact(text):
    """Strip any credential material out of third-party error text.

    yt-dlp and urllib happily echo the full proxy URL in exceptions, which
    would otherwise put the password straight into the logs.
    """
    if not text:
        return text
    out = str(text)
    # user:pass@host -> ***@host  (any scheme, any host)
    out = re.sub(r'([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+:[^\s/@]+@', r'\1***:***@', out)
    for name in ('DATAIMPULSE_USERNAME', 'DATAIMPULSE_PASSWORD'):
        secret = _clean(name)
        if secret:
            out = out.replace(secret, '***')
    return out


# Proxy-transport failure signatures. These mean "the tunnel broke", NOT
# "Instagram rejected the content" — only these justify a direct retry.
# Deliberately excludes 403/login-required/empty-media, which are genuine
# Instagram responses and must keep their existing cookie-rotation handling.
_PROXY_ERROR_PATTERNS = (
    'proxy',
    'tunnel connection failed',
    'cannot connect to proxy',
    'connection aborted',
    'connection reset by peer',
    'econnreset',
    'bad gateway',
    'timed out',
    'timeout',
    'temporary failure in name resolution',
    'failed to establish a new connection',
)


def is_proxy_transport_error(text):
    """True when the failure looks like the proxy hop itself, not the content."""
    if not text:
        return False
    low = str(text).lower()
    # A 407 is unambiguously the proxy demanding auth.
    if '407' in low and 'proxy' in low:
        return True
    return any(p in low for p in _PROXY_ERROR_PATTERNS)
