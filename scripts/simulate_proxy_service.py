"""Offline assertions for portal/proxy_service.py (no network, no Flask).

Run: python scripts/simulate_proxy_service.py
Covers: URL construction, country targeting, sticky sessions, graceful
fallback chain, credential redaction, and proxy-vs-content error triage.
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import proxy_service standalone — it imports no project modules by design.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'proxy_service',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'portal', 'proxy_service.py'))
ps = importlib.util.module_from_spec(_spec)
sys.modules['proxy_service'] = ps
_spec.loader.exec_module(ps)

PASS = 0


def check(label, cond):
    global PASS
    assert cond, 'FAIL: ' + label
    PASS += 1
    print('  ok  ' + label)


def env(**kw):
    for k in ('DATAIMPULSE_USERNAME', 'DATAIMPULSE_PASSWORD',
              'DATAIMPULSE_COUNTRY', 'IG_PROXY'):
        os.environ.pop(k, None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = v


print('\n[1] Unconfigured -> direct')
env()
check('is_configured False', ps.is_configured() is False)
check('get_proxy_url None', ps.get_proxy_url() is None)
check('get_meta_proxy None', ps.get_meta_proxy() is None)
check('describe says direct', 'direct' in ps.describe())

print('\n[2] Legacy IG_PROXY fallback preserved')
env(IG_PROXY='http://legacy:pw@host:8080')
check('falls back to IG_PROXY', ps.get_meta_proxy() == 'http://legacy:pw@host:8080')
check('describe names legacy', ps.describe() == 'legacy IG_PROXY')

print('\n[3] DataImpulse configured, default country GB')
env(DATAIMPULSE_USERNAME='user123', DATAIMPULSE_PASSWORD='secretpw')
url = ps.get_proxy_url()
check('is_configured True', ps.is_configured() is True)
check('gateway host+port', 'gw.dataimpulse.com:823' in url)
check('country suffix __cr.gb', '__cr.gb' in url)
check('country default gb', ps.get_country() == 'gb')
check('username present', 'user123' in url)
check('scheme http', url.startswith('http://'))

print('\n[4] DataImpulse takes precedence over legacy IG_PROXY')
env(DATAIMPULSE_USERNAME='u', DATAIMPULSE_PASSWORD='p', IG_PROXY='http://legacy:pw@h:1')
check('prefers DataImpulse', 'dataimpulse' in ps.get_meta_proxy())

print('\n[5] Country configurable')
env(DATAIMPULSE_USERNAME='u', DATAIMPULSE_PASSWORD='p', DATAIMPULSE_COUNTRY='us')
check('country us', '__cr.us' in ps.get_proxy_url())
env(DATAIMPULSE_USERNAME='u', DATAIMPULSE_PASSWORD='p', DATAIMPULSE_COUNTRY='DE')
check('country lowercased', '__cr.de' in ps.get_proxy_url())
env(DATAIMPULSE_USERNAME='u', DATAIMPULSE_PASSWORD='p', DATAIMPULSE_COUNTRY='')
check('empty country = no targeting', '__cr.' not in ps.get_proxy_url())

print('\n[6] Rotation default vs sticky session')
env(DATAIMPULSE_USERNAME='u', DATAIMPULSE_PASSWORD='p')
check('no session id by default (gateway rotates)', '__sid.' not in ps.get_proxy_url())
check('sticky session when asked', '__sid.abc123' in ps.get_proxy_url(session_id='abc123'))
check('session id sanitised', '__sid.evil' in ps.get_proxy_url(session_id='evil/../!'))

print('\n[7] Credential redaction')
env(DATAIMPULSE_USERNAME='myuser', DATAIMPULSE_PASSWORD='mypassword')
leak = 'ERROR: unable to connect to proxy http://myuser__cr.gb:mypassword@gw.dataimpulse.com:823'
red = ps.redact(leak)
check('password removed', 'mypassword' not in red)
check('username removed', 'myuser' not in red)
check('masked marker present', '***' in red)
check('describe leaks nothing', 'mypassword' not in ps.describe() and 'myuser' not in ps.describe())
check('redact handles None', ps.redact(None) is None)

print('\n[8] Proxy transport errors vs genuine Instagram errors')
for t in ['Cannot connect to proxy', 'Tunnel connection failed: 407 Proxy Authentication Required',
          'Connection reset by peer', 'Read timed out', 'Bad gateway']:
    check('proxy error detected: ' + t[:34], ps.is_proxy_transport_error(t) is True)
for t in ['HTTP Error 403: Forbidden', 'Requested content is not available, rate-limit reached',
          'login required', 'empty media response', 'Video unavailable']:
    check('content error NOT proxy: ' + t[:32], ps.is_proxy_transport_error(t) is False)

print('\n%d assertions passed.' % PASS)
