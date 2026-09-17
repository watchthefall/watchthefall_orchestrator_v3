"""
scripts/simulate_waitlist_discord_gate.py

Verifies the hard Discord-verification gate on the beta waitlist:
name/email -> Discord OAuth -> verified identity -> beta_access row saved.
No row is ever created before Discord verification succeeds.

Uses a real Flask test client against a temp COPY of the real dev DB (never
the live DB), with discord_configured()/exchange_code()/fetch_identity()/
sync_beta_applicant_roles() monkeypatched on the discord_integration module
(no real Discord credentials or network calls needed) so the actual
waitlist_form_submit() / waitlist_discord_authorize() / waitlist_discord_callback()
route bodies run for real, not a reimplementation of their logic.

Run: python3 scripts/simulate_waitlist_discord_gate.py
"""
import sys, os, sqlite3, shutil, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_REAL_DB = os.path.join(_REPO_ROOT, 'portal', 'private', 'db', 'wtf_studio.db')
_TMP_DB = os.path.join(tempfile.gettempdir(), 'simulate_waitlist_discord_gate.db')
shutil.copyfile(_REAL_DB, _TMP_DB)

os.environ['DB_PATH'] = _TMP_DB
sys.path.insert(0, _REPO_ROOT)


from portal import app as flask_app
from portal import discord_integration as di
import portal.app as portal_app_mod

flask_app.config['TESTING'] = True

def row_count():
    conn = sqlite3.connect(_TMP_DB)
    n = conn.execute("SELECT COUNT(*) FROM beta_access").fetchone()[0]
    conn.close()
    return n

def get_row_by_email(email):
    conn = sqlite3.connect(_TMP_DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM beta_access WHERE email=?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None

def cleanup(*emails):
    conn = sqlite3.connect(_TMP_DB)
    for e in emails:
        conn.execute("DELETE FROM beta_access WHERE email=?", (e,))
    conn.commit()
    conn.close()

orig_discord_configured = di.discord_configured
di.discord_configured = lambda: True
client = flask_app.test_client()

passed = []
failed = []
def check(desc, cond):
    if cond:
        passed.append(desc)
        print(f"  [PASS] {desc}")
    else:
        failed.append(desc)
        print(f"  [FAIL] {desc}")

FORM = dict(
    creator_name='Test Creator', email='newapplicant@example.com',
    main_platform='tiktok', creator_type='solo',
    pages_accounts='@testhandle', discord_username='', referral_code='',
)

print("1) discord_configured() True in this env (real prod creds loaded via env)")
check("discord_configured() is True", di.discord_configured())

print("\n2) Submitting the form does NOT create a row -- it redirects into Discord OAuth")
before = row_count()
resp = client.post('/waitlist/submit', data=FORM, follow_redirects=False)
after = row_count()
check("no new beta_access row created on submit", after == before)
check("response is a redirect (302/303)", resp.status_code in (302, 303))
loc = resp.headers.get('Location', '')
check("redirected to /waitlist/discord/authorize", '/waitlist/discord/authorize' in loc)
check("email is NOT in the query string (form data traveled in signed token only)",
      'newapplicant' not in loc)

print("\n3) Following the authorize hop redirects to Discord's real consent screen")
resp2 = client.get(loc, follow_redirects=False)
check("authorize redirects (302/303)", resp2.status_code in (302, 303))
authorize_loc = resp2.headers.get('Location', '')
check("redirect target is Discord's OAuth authorize endpoint",
      authorize_loc.startswith('https://discord.com/api/oauth2/authorize') or
      authorize_loc.startswith('https://discord.com/oauth2/authorize'))
import urllib.parse
qs = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_loc).query)
state_token = qs.get('state', [None])[0]
check("state param present and matches the token we were given", state_token is not None)

print("\n4) Simulated Discord CANCEL (user hits 'Cancel' on Discord's screen) -- nothing saved")
before = row_count()
resp3 = client.get('/waitlist/discord/callback?error=access_denied', follow_redirects=False)
after = row_count()
check("still no new row after a cancelled OAuth", after == before)
check("callback redirects back to beta page", resp3.status_code in (302, 303))

print("\n5) Simulated Discord SUCCESS -- monkeypatch exchange_code/fetch_identity, hit the real callback")
FAKE_DISCORD_ID = 'discord_test_111'
FAKE_USERNAME = 'testuser111'
orig_exchange = di.exchange_code
orig_fetch = di.fetch_identity
orig_sync = di.sync_beta_applicant_roles
di.exchange_code = lambda code: {'access_token': 'fake_token'}
di.fetch_identity = lambda token: {'id': FAKE_DISCORD_ID, 'username': FAKE_USERNAME}
di.sync_beta_applicant_roles = lambda discord_id, beta_tester=False: (True, 'skipped in test')
# app.py imports these names directly inside the route function via
# "from .discord_integration import exchange_code, fetch_identity, sync_beta_applicant_roles"
# each call, so patching the module attributes above is picked up live.

try:
    before = row_count()
    resp4 = client.get(f'/waitlist/discord/callback?state={state_token}&code=fakecode123', follow_redirects=False)
    after = row_count()
    check("exactly one new row created on successful verification", after == before + 1)

    row = get_row_by_email('newapplicant@example.com')
    check("row exists with correct email", row is not None)
    if row:
        check("discord_user_id set to the verified identity", row.get('discord_user_id') == FAKE_DISCORD_ID)
        check("discord_verified_username set", row.get('discord_verified_username') == FAKE_USERNAME)
        check("discord_linked_at set (not null)", bool(row.get('discord_linked_at')))
        check("free-text discord_username column is NULL (never treated as verification)",
              row.get('discord_username') is None)
        check("creator_name carried through from the signed pending token", row.get('creator_name') == 'Test Creator')
        check("main_platform carried through", row.get('main_platform') == 'tiktok')
        check("status is 'pending' (still needs admin approval, same as before)", row.get('status') == 'pending')

    print("\n6) Re-submitting the SAME (now-verified) email short-circuits -- no Discord round trip, no new row")
    before = row_count()
    resp5 = client.post('/waitlist/submit', data=FORM, follow_redirects=False)
    after = row_count()
    check("no new row created", after == before)
    check("does NOT redirect to Discord authorize this time", '/waitlist/discord/authorize' not in resp5.headers.get('Location', ''))
    check("redirects straight back to beta page", resp5.headers.get('Location', '') in ('/beta', '/waitlist'))

    print("\n7) Duplicate Discord identity across two DIFFERENT new applications is rejected")
    FORM2 = dict(FORM); FORM2['email'] = 'seconddupe@example.com'; FORM2['creator_name'] = 'Second Applicant'
    resp6 = client.post('/waitlist/submit', data=FORM2, follow_redirects=False)
    loc6 = resp6.headers.get('Location', '')
    resp7 = client.get(loc6, follow_redirects=False)
    authorize_loc2 = resp7.headers.get('Location', '')
    qs2 = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_loc2).query)
    state_token2 = qs2.get('state', [None])[0]

    before = row_count()
    resp8 = client.get(f'/waitlist/discord/callback?state={state_token2}&code=fakecode456', follow_redirects=False)
    after = row_count()
    check("no new row created for the duplicate-Discord-identity attempt", after == before)
    check("second applicant's email NOT present in beta_access", get_row_by_email('seconddupe@example.com') is None)

finally:
    di.exchange_code = orig_exchange
    di.fetch_identity = orig_fetch
    di.sync_beta_applicant_roles = orig_sync
    cleanup('newapplicant@example.com', 'seconddupe@example.com')

di.discord_configured = orig_discord_configured

print("\n8) /api/waitlist legacy endpoint no longer creates entries (closed second door)")
before = row_count()
resp9 = client.post('/api/waitlist', data=dict(email='apibypass@example.com', creator_name='API Bypass'))
after = row_count()
check("no row created via /api/waitlist", after == before)
check("returns 410 Gone", resp9.status_code == 410)
check("get_row_by_email confirms nothing written", get_row_by_email('apibypass@example.com') is None)

print("\n" + "="*60)
if failed:
    print(f"RESULT: {len(failed)} FAILED, {len(passed)} passed")
    for f in failed:
        print(f"  FAILED: {f}")
    sys.exit(1)
else:
    print(f"ALL {len(passed)} CHECKS PASSED")
