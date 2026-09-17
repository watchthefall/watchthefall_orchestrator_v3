"""
Offline harness for the entitlement-resolution architecture: base tier
(users.tier) + temporary overlay (users.bonus_tier / bonus_tier_until) +
founding_status (independent, non-expiring) -> effective tier, plus the
active-beta predicate that drives the Discord Beta Tester role.

Deterministic, no network, no Flask, no real Discord credentials.

get_user_tier() and get_beta_tester_active() live in portal/app.py, which
also defines the Flask app and does real work at import time (init_db()
against the real DB_PATH, plus optional heavy dependencies for the video
pipeline) -- importing it here would be both slow and fragile. Instead this
harness AST-extracts just those two function bodies out of the real
app.py source and executes them against a real temporary SQLite database,
exactly the same way simulate_tier_commercials.py extracts pricing logic
out of config.py. This tests the REAL, shipped code, not a reimplementation
of it.

Run:  python scripts/simulate_entitlement.py     (exit 0 = all pass)
"""
import ast
import io
import os
import sys
import types
import tempfile
import sqlite3
from datetime import datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Fake 'portal' package, real portal.database module (mirrors
# simulate_beta_discord.py's boot sequence).
# ---------------------------------------------------------------------------
_pkg = types.ModuleType('portal')
_pkg.__path__ = [os.path.join(_ROOT, 'portal')]
sys.modules['portal'] = _pkg

_TMP = tempfile.mkdtemp(prefix='brandr_entitlement_sim_')
os.environ['DB_PATH'] = os.path.join(_TMP, 'boot.db')
_c = sqlite3.connect(os.environ['DB_PATH'])
_c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
_c.commit(); _c.close()

from portal import database as db  # noqa: E402

# ---------------------------------------------------------------------------
# AST-extract get_user_tier() and get_beta_tester_active() from the real
# app.py source, and exec them bound to __package__='portal' so their own
# "from .database import get_connection" relative imports resolve against
# the real portal.database module above.
# ---------------------------------------------------------------------------
APP_SRC = io.open(os.path.join(_ROOT, 'portal', 'app.py'), encoding='utf-8').read()
_tree = ast.parse(APP_SRC)
_wanted = {'get_user_tier', 'get_beta_tester_active'}
_found = {}
for node in _tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in _wanted:
        _found[node.name] = node
missing = _wanted - set(_found)
if missing:
    print(f"FATAL: could not find {missing} in portal/app.py -- did the function get renamed?")
    sys.exit(1)

_mod = ast.Module(body=list(_found.values()), type_ignores=[])
_ns = {
    '__name__': 'portal.app',
    '__package__': 'portal',
    'sqlite3': sqlite3,
    'DEFAULT_TIER': 'Explorer',
    'datetime': datetime,
    'timedelta': timedelta,
    '_log_disk_health_warning': lambda: None,
}
exec(compile(_mod, filename='<extracted app.py>', mode='exec'), _ns)
get_user_tier = _ns['get_user_tier']
get_beta_tester_active = _ns['get_beta_tester_active']


def _check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _check.failed += 1
_check.failed = 0


def _mk_user(conn, tier='Explorer', bonus_tier=None, bonus_tier_until=None,
             founding_status=0, is_beta_tester=0, special_status=None,
             discord_user_id=None):
    c = conn.cursor()
    c.execute(
        '''INSERT INTO users (email, password_hash, tier, bonus_tier, bonus_tier_until,
                               founding_status, is_beta_tester, special_status, discord_user_id)
           VALUES (?, 'x', ?, ?, ?, ?, ?, ?, ?)''',
        (f'user{os.urandom(4).hex()}@example.com', tier, bonus_tier, bonus_tier_until,
         founding_status, is_beta_tester, special_status, discord_user_id)
    )
    conn.commit()
    return c.lastrowid


def main():
    tmp = tempfile.mkdtemp(prefix='brandr_entitlement_sim_')
    db.DB_PATH = os.path.join(tmp, 'test.db')
    # Fresh file, no pre-existing users table -- init_db()'s own
    # CREATE TABLE IF NOT EXISTS must be the one that defines email/
    # password_hash/tier, or those columns never get created (they are not
    # part of the later ALTER-loop, which only adds columns after the fact).
    db.init_db()

    FUTURE = (datetime.utcnow() + timedelta(days=30)).isoformat()
    PAST = (datetime.utcnow() - timedelta(days=1)).isoformat()

    with db.get_connection() as conn:
        print("\n1) Example A -- normal free user, no temp entitlement")
        u = _mk_user(conn, tier='Explorer')
        _check("effective tier == Explorer", get_user_tier(u) == 'Explorer')
        _check("beta not active", get_beta_tester_active(u) is False)

        print("\n2) Example B -- beta user, active: base Explorer, temp Platinum")
        u = _mk_user(conn, tier='Explorer', bonus_tier='Platinum',
                     bonus_tier_until=FUTURE, is_beta_tester=1)
        _check("effective tier == Platinum while active", get_user_tier(u) == 'Platinum')
        _check("beta active == True", get_beta_tester_active(u) is True)

        print("\n3) Example B continued -- same user after the bonus expires")
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET bonus_tier_until = ? WHERE id = ?', (PAST, u))
            c2.commit()
        _check("effective tier reverts to base Explorer (NOT hard-reset elsewhere)",
               get_user_tier(u) == 'Explorer')
        _check("beta active == False after expiry", get_beta_tester_active(u) is False)
        _check("is_beta_tester historical flag untouched (still 1)",
               conn.execute('SELECT is_beta_tester FROM users WHERE id=?', (u,)).fetchone()[0] == 1)

        print("\n4) Example C -- beta user subscribes to Creator DURING the beta window")
        u = _mk_user(conn, tier='Explorer', bonus_tier='Platinum',
                     bonus_tier_until=FUTURE, is_beta_tester=1)
        _check("effective tier == Platinum while beta active", get_user_tier(u) == 'Platinum')
        # Simulate a Stripe/admin subscription write: only touches base tier.
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET tier = ? WHERE id = ?', ('Creator', u))
            c2.commit()
        _check("still Platinum while beta still active (bonus wins)", get_user_tier(u) == 'Platinum')
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET bonus_tier_until = ? WHERE id = ?', (PAST, u))
            c2.commit()
        _check("resolves to Creator after expiry -- subscription preserved",
               get_user_tier(u) == 'Creator')

        print("\n5) Example D -- existing Creator user receives temp Platinum promo")
        u = _mk_user(conn, tier='Creator', bonus_tier='Platinum', bonus_tier_until=FUTURE)
        _check("effective == Platinum while active", get_user_tier(u) == 'Platinum')
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET bonus_tier_until = ? WHERE id = ?', (PAST, u))
            c2.commit()
        _check("effective == Creator after expiry (not downgraded further)",
               get_user_tier(u) == 'Creator')

        print("\n6) Example E -- existing Platinum subscriber gets a (no-op) temp Platinum promo")
        u = _mk_user(conn, tier='Platinum', bonus_tier='Platinum', bonus_tier_until=FUTURE)
        _check("effective == Platinum while active", get_user_tier(u) == 'Platinum')
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET bonus_tier_until = ? WHERE id = ?', (PAST, u))
            c2.commit()
        _check("still Platinum after expiry -- never downgraded", get_user_tier(u) == 'Platinum')

        print("\n7) Example F -- Founding + Creator user receives temp Platinum")
        u = _mk_user(conn, tier='Creator', bonus_tier='Platinum', bonus_tier_until=FUTURE,
                     founding_status=1)
        _check("effective == Platinum while active", get_user_tier(u) == 'Platinum')
        _check("founding_status untouched (still 1)",
               conn.execute('SELECT founding_status FROM users WHERE id=?', (u,)).fetchone()[0] == 1)
        with db.get_connection() as c2:
            c2.execute('UPDATE users SET bonus_tier_until = ? WHERE id = ?', (PAST, u))
            c2.commit()
        _check("resolves to Creator after expiry, Founding status intact",
               get_user_tier(u) == 'Creator')
        _check("founding_status still 1 -- never destroyed",
               conn.execute('SELECT founding_status FROM users WHERE id=?', (u,)).fetchone()[0] == 1)

        print("\n8) A missing/NULL bonus_tier_until never grants a phantom bonus")
        u = _mk_user(conn, tier='Studio', bonus_tier='Platinum', bonus_tier_until=None)
        _check("effective == base tier when bonus_tier_until is NULL", get_user_tier(u) == 'Studio')
        _check("beta not active", get_beta_tester_active(u) is False)

        print("\n9) is_beta_tester=1 alone (no active bonus) is NOT active beta --")
        print("   this is the 'eternal boolean' bug this change specifically fixes")
        u = _mk_user(conn, tier='Explorer', is_beta_tester=1, bonus_tier=None, bonus_tier_until=None)
        _check("beta active == False (historical marker only, no live entitlement)",
               get_beta_tester_active(u) is False)

    print("\n10) Discord role targets before/after expiry (target_role_ids, real function)")
    from portal import discord_integration as di
    ROLES = {
        'verified': 'r_verified', 'beta_tester': 'r_beta_tester', 'founding': 'r_founding',
        'tier': {'Explorer': 'r_explorer', 'Creator': 'r_creator', 'Studio': 'r_studio',
                 'Platinum': 'r_platinum'},
    }
    before = di.target_role_ids('Platinum', founding_status=False, beta_tester=True, role_ids=ROLES)
    _check("active beta: Verified + Beta Tester + Platinum",
           before == {'r_verified', 'r_beta_tester', 'r_platinum'})
    after = di.target_role_ids('Creator', founding_status=False, beta_tester=False, role_ids=ROLES)
    _check("expired beta: Beta Tester gone, Verified retained, tier role == underlying tier",
           after == {'r_verified', 'r_creator'})
    _check("Beta Tester role specifically removed", 'r_beta_tester' not in after)
    _check("Verified role specifically retained", 'r_verified' in after)

    print("\n11) sync_roles_for_user wiring: reads the LIVE predicate, not the raw column")
    # Monkeypatch sync_member_roles to capture the beta_tester arg it would
    # send to Discord, instead of making a real network call.
    captured = {}
    _orig_sync_member_roles = di.sync_member_roles
    def _fake_sync_member_roles(discord_user_id, tier, founding_status, beta_tester=False):
        captured['tier'] = tier
        captured['founding_status'] = founding_status
        captured['beta_tester'] = beta_tester
        return True, 'fake'
    di.sync_member_roles = _fake_sync_member_roles
    # sync_roles_for_user does "from .app import get_user_tier, get_beta_tester_active"
    # -- install a fake portal.app module carrying our extracted, DB-backed
    # versions of exactly those two functions so that import resolves without
    # needing the real Flask app.
    _fake_app = types.ModuleType('portal.app')
    _fake_app.get_user_tier = get_user_tier
    _fake_app.get_beta_tester_active = get_beta_tester_active
    sys.modules['portal.app'] = _fake_app
    _pkg.app = _fake_app
    try:
        with db.get_connection() as conn:
            u_active = _mk_user(conn, tier='Explorer', bonus_tier='Platinum',
                                 bonus_tier_until=FUTURE, is_beta_tester=1,
                                 discord_user_id='discord_active')
            u_expired = _mk_user(conn, tier='Creator', bonus_tier='Platinum',
                                  bonus_tier_until=PAST, is_beta_tester=1,
                                  discord_user_id='discord_expired')
        ok, detail = di.sync_roles_for_user(u_active)
        _check("sync ok for active beta user", ok is True)
        _check("beta_tester passed through as True while active", captured['beta_tester'] is True)
        _check("tier passed through as effective Platinum", captured['tier'] == 'Platinum')

        ok, detail = di.sync_roles_for_user(u_expired)
        _check("sync ok for expired beta user", ok is True)
        _check("beta_tester passed through as False once expired -- role sync will DROP it",
               captured['beta_tester'] is False)
        _check("tier passed through as base Creator, not stale Platinum",
               captured['tier'] == 'Creator')
    finally:
        di.sync_member_roles = _orig_sync_member_roles

    print("\n12) Duplicate Discord identity across two Brandr accounts is rejected")
    with db.get_connection() as conn:
        first = _mk_user(conn, tier='Explorer')
        second = _mk_user(conn, tier='Explorer')
    r1 = db.link_discord_account(first, 'discord_shared', 'alice')
    _check("first link succeeds", r1 == 'ok')
    r2 = db.link_discord_account(second, 'discord_shared', 'alice_alt')
    _check("second account linking the SAME discord id is rejected", r2 == 'duplicate')
    link = db.get_discord_link(second)
    _check("second account's link is still empty", link is None)
    # Re-linking the SAME account to the SAME id is still allowed (idempotent).
    r3 = db.link_discord_account(first, 'discord_shared', 'alice')
    _check("re-linking the same account to its own id still works", r3 == 'ok')

    print("\n13) Unique index exists on users.discord_user_id (backstop, not just app-level check)")
    with db.get_connection() as conn:
        idx = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_users_discord_user_id'"
        ).fetchone()
    _check("idx_users_discord_user_id exists", idx is not None)
    _check("... and is UNIQUE", idx is not None and 'UNIQUE' in idx[0].upper())

    print("\n14) Static check: no code path in the beta flow sets special_status='beta_tester'")
    beta_related = []
    for name in ('_apply_beta_package', 'register_user'):
        for node in _tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                beta_related.append(ast.get_source_segment(APP_SRC, node) or '')
    joined = '\n'.join(beta_related)
    bad_patterns = ["special_status = 'beta_tester'", 'special_status="beta_tester"',
                     "special_status='beta_tester'"]
    _check("no literal special_status = 'beta_tester' assignment in register_user/_apply_beta_package",
           not any(p in joined for p in bad_patterns))
    _check("register_user still hardcodes special_status = None", 'special_status = None' in joined)

    print(f"\n{'='*60}")
    if _check.failed:
        print(f"FAILED: {_check.failed} check(s) failed")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == '__main__':
    main()
