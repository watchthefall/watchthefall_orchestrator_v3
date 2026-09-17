"""
Discord OAuth2 linking + one-way role sync.

Brandr is the source of truth (see the "Brandr <-> Discord Access Model" spec
doc): a user's tier, Founding status and special_status live in Brandr's own
`users` table, and this module only ever pushes them OUT to Discord as roles.
Nothing here reads a Discord role back into Brandr -- if someone hand-edits
roles in the Discord server, the next sync silently overwrites it, by design.

Two independent things happen against the Discord API:
  1. OAuth2 (identify scope only) -- lets a Brandr user prove which Discord
     account is theirs. Uses DISCORD_CLIENT_ID/SECRET, the user's own consent.
  2. Role sync -- uses the BOT token against the guild directly. This is what
     actually adds/removes roles, and does not require the user to grant
     anything beyond having already linked their identity once.

`target_role_ids()` is the pure piece: given tier/founding/special_status it
returns which of OUR managed roles a member should hold, with no network
call, so it's exactly what scripts/simulate_discord.py exercises offline.
"""
import requests

from . import config

DISCORD_API = 'https://discord.com/api/v10'
_TIER_ROLE_KEYS = ('Explorer', 'Creator', 'Studio', 'Platinum', 'Elite')


def discord_configured():
    """True once enough of Discord is wired up to attempt OAuth linking."""
    return bool(config.DISCORD_CLIENT_ID and config.DISCORD_CLIENT_SECRET
                and config.DISCORD_REDIRECT_URI)


def role_sync_configured():
    """True once the bot can actually write roles to the guild (a separate,
    stricter bar than discord_configured() -- linking can work before this
    does; sync_member_roles() just becomes a no-op with a clear reason)."""
    return bool(config.DISCORD_BOT_TOKEN and config.DISCORD_GUILD_ID)


# ---------------------------------------------------------------------------
# OAuth2 (user-facing linking)
# ---------------------------------------------------------------------------

def oauth_authorize_url(state):
    """The URL to send a user to for Discord's consent screen."""
    from urllib.parse import urlencode
    params = {
        'client_id': config.DISCORD_CLIENT_ID,
        'redirect_uri': config.DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope': config.DISCORD_OAUTH_SCOPES,
        'state': state,
        'prompt': 'consent',
    }
    return f'https://discord.com/oauth2/authorize?{urlencode(params)}'


def exchange_code(code):
    """Trade an OAuth2 authorization code for an access token. Returns the
    token response dict, or None on any failure (network, bad code, Discord
    outage) -- callers treat None as "linking didn't complete, ask them to
    retry" rather than surfacing Discord's own error shape."""
    try:
        resp = requests.post(
            f'{DISCORD_API}/oauth2/token',
            data={
                'client_id': config.DISCORD_CLIENT_ID,
                'client_secret': config.DISCORD_CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': config.DISCORD_REDIRECT_URI,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[DISCORD] token exchange failed: {resp.status_code} {resp.text[:200]}", flush=True)
            return None
        return resp.json()
    except Exception as e:
        print(f"[DISCORD] token exchange error: {e}", flush=True)
        return None


def fetch_identity(access_token):
    """The linking user's own Discord identity (id + username), using the
    access token THEY just granted -- never the bot token. Returns None on
    failure."""
    try:
        resp = requests.get(
            f'{DISCORD_API}/users/@me',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[DISCORD] identity fetch failed: {resp.status_code} {resp.text[:200]}", flush=True)
            return None
        data = resp.json()
        return {'id': data.get('id'), 'username': data.get('username')}
    except Exception as e:
        print(f"[DISCORD] identity fetch error: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Role sync (bot-driven, one-way: Brandr -> Discord)
# ---------------------------------------------------------------------------

def target_role_ids(tier, founding_status, special_status=None, role_ids=None):
    """The set of OUR managed Discord role ids this account should hold right
    now, computed purely from Brandr's own facts -- no network call, so this
    is what scripts/simulate_discord.py exercises directly.

    Pass role_ids to test against a fixture map; defaults to the real
    config.DISCORD_ROLE_IDS. Blank/unconfigured role ids are dropped rather
    than synced as empty strings.
    """
    role_ids = role_ids if role_ids is not None else config.DISCORD_ROLE_IDS
    target = set()

    verified = role_ids.get('verified', '')
    if verified:
        target.add(verified)

    tier_role = role_ids.get('tier', {}).get(tier, '')
    if tier_role:
        target.add(tier_role)

    # Founding is permanent and additive -- it stacks with the current tier
    # role rather than replacing it, and (per the spec) is never removed by
    # a billing change. It IS removed here if founding_status itself is 0,
    # which only happens through an explicit admin correction, never a
    # subscription lapse -- see database.revoke_founding_status.
    founding_role = role_ids.get('founding', '')
    if founding_status and founding_role:
        target.add(founding_role)

    return {r for r in target if r}


def _all_managed_role_ids(role_ids=None):
    """Every role id this integration is allowed to add/remove. Used to work
    out what to REMOVE: a role sync only ever touches roles in this set --
    Winner, Ambassador, Staff and anything else a human assigned in Discord
    are never added or removed by Brandr."""
    role_ids = role_ids if role_ids is not None else config.DISCORD_ROLE_IDS
    ids = {role_ids.get('verified', '')}
    ids.update(role_ids.get('tier', {}).values())
    ids.add(role_ids.get('founding', ''))
    return {r for r in ids if r}


def _guild_member_roles(discord_user_id):
    """Current role ids this member holds in the guild, or None on failure
    (not a member yet, bad token, Discord outage)."""
    try:
        resp = requests.get(
            f'{DISCORD_API}/guilds/{config.DISCORD_GUILD_ID}/members/{discord_user_id}',
            headers={'Authorization': f'Bot {config.DISCORD_BOT_TOKEN}'},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        return set(resp.json().get('roles', []))
    except Exception as e:
        print(f"[DISCORD] guild member fetch error for discord_user={discord_user_id}: {e}", flush=True)
        return None


def _set_member_role(discord_user_id, role_id, add):
    method = requests.put if add else requests.delete
    try:
        resp = method(
            f'{DISCORD_API}/guilds/{config.DISCORD_GUILD_ID}/members/{discord_user_id}/roles/{role_id}',
            headers={'Authorization': f'Bot {config.DISCORD_BOT_TOKEN}'},
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"[DISCORD] role {'add' if add else 'remove'} error "
              f"discord_user={discord_user_id} role={role_id}: {e}", flush=True)
        return False


def sync_member_roles(discord_user_id, tier, founding_status, special_status=None):
    """Push this account's computed roles onto the guild member. Adds
    whatever's missing from target_role_ids(), removes whatever managed role
    the member holds that ISN'T in that set (e.g. their old tier role after
    an upgrade). Returns (ok: bool, detail: str) -- ok=False covers "not
    configured yet" and "not a guild member yet" the same way, since both
    just mean sync will succeed later, not that anything is broken.
    """
    if not role_sync_configured():
        return False, 'Discord role sync is not configured yet'

    current = _guild_member_roles(discord_user_id)
    if current is None:
        return False, 'not a member of the Brandr Discord server yet (or sync is temporarily unavailable)'

    target = target_role_ids(tier, founding_status, special_status)
    managed = _all_managed_role_ids()

    to_add = target - current
    to_remove = (current & managed) - target

    ok = True
    for role_id in to_add:
        ok = _set_member_role(discord_user_id, role_id, add=True) and ok
    for role_id in to_remove:
        ok = _set_member_role(discord_user_id, role_id, add=False) and ok

    if not ok:
        return False, 'some roles could not be updated (see server logs)'
    return True, f'{len(to_add)} added, {len(to_remove)} removed'


def sync_roles_for_user(user_id):
    """Convenience wrapper: pulls tier/founding/special_status/discord link
    for a Brandr user id and syncs. Returns (ok, detail) as sync_member_roles."""
    from .app import get_user_tier
    from .database import get_user_special_status, get_discord_link, get_connection

    link = get_discord_link(user_id)
    if not link:
        return False, 'no Discord account linked'

    tier = get_user_tier(user_id)
    special_status = get_user_special_status(user_id)
    try:
        with get_connection() as conn:
            row = conn.execute(
                'SELECT COALESCE(founding_status, 0) as fs FROM users WHERE id = ?', (user_id,)
            ).fetchone()
        founding_status = bool(row['fs']) if row else False
    except Exception as e:
        print(f"[DISCORD] founding_status lookup failed for user={user_id}: {e}", flush=True)
        founding_status = False

    return sync_member_roles(link['discord_user_id'], tier, founding_status, special_status)
