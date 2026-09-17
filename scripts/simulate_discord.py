"""
Offline harness for the Discord role-sync logic (portal/discord_integration.py).

Deterministic, no network, no Flask, no real Discord credentials. Exercises
target_role_ids() -- the pure function that decides which of OUR managed
roles an account should hold, given tier/founding_status and a role-id
fixture map. This is deliberately the only piece of discord_integration.py
testable without live Discord credentials; OAuth exchange, identity fetch
and the actual guild role PUT/DELETE calls all require a real bot token and
guild, and are exercised by hand against the real server instead.

Run:  python scripts/simulate_discord.py     (exit 0 = all pass)
"""
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_pkg = types.ModuleType('portal')
_pkg.__path__ = [os.path.join(_ROOT, 'portal')]
sys.modules['portal'] = _pkg

from portal import discord_integration as di  # noqa: E402


def _check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _check.failed += 1
_check.failed = 0


ROLES = {
    'verified': 'r_verified',
    'tier': {
        'Explorer': 'r_explorer',
        'Creator': 'r_creator',
        'Studio': 'r_studio',
        'Platinum': 'r_platinum',
    },
    'founding': 'r_founding',
}


def main():
    print("\n1) A plain Explorer gets verified + their tier role, nothing else")
    got = di.target_role_ids('Explorer', founding_status=False, role_ids=ROLES)
    _check("== {verified, explorer}", got == {'r_verified', 'r_explorer'})

    print("\n2) Founding stacks WITH the tier role, doesn't replace it")
    got = di.target_role_ids('Studio', founding_status=True, role_ids=ROLES)
    _check("== {verified, studio, founding}", got == {'r_verified', 'r_studio', 'r_founding'})

    print("\n3) Founding=False never adds the founding role, whatever the tier")
    got = di.target_role_ids('Platinum', founding_status=False, role_ids=ROLES)
    _check("no founding role present", 'r_founding' not in got)
    _check("== {verified, platinum}", got == {'r_verified', 'r_platinum'})

    print("\n4) Unknown/unmapped tier contributes no tier role (never crashes)")
    got = di.target_role_ids('SomeFutureTier', founding_status=False, role_ids=ROLES)
    _check("== {verified} only", got == {'r_verified'})

    print("\n5) A blank role id (not yet configured on the real server) is skipped, not synced as ''")
    partial = {'verified': '', 'tier': {'Explorer': 'r_explorer'}, 'founding': ''}
    got = di.target_role_ids('Explorer', founding_status=True, role_ids=partial)
    _check("== {explorer} -- no blank strings, no founding (blank)", got == {'r_explorer'})

    print("\n6) Nothing configured at all -> empty set, not an error")
    empty = {'verified': '', 'tier': {}, 'founding': ''}
    got = di.target_role_ids('Explorer', founding_status=True, role_ids=empty)
    _check("== empty set", got == set())

    print("\n7) _all_managed_role_ids() covers verified + every tier + founding, and only those")
    managed = di._all_managed_role_ids(ROLES)
    _check("== every configured id, 6 total",
           managed == {'r_verified', 'r_explorer', 'r_creator', 'r_studio', 'r_platinum', 'r_founding'})

    print("\n8) discord_configured() / role_sync_configured() are False with no env set")
    _check("discord_configured() False without client id/secret/redirect",
           di.discord_configured() is False)
    _check("role_sync_configured() False without bot token/guild id",
           di.role_sync_configured() is False)

    print()
    if _check.failed:
        print(f"RESULT: {_check.failed} assertion(s) FAILED")
        return 1
    print("RESULT: all assertions passed - target_role_ids() computes the right managed-role "
          "set for tier + Founding stacking, ignores blank/unmapped roles cleanly, and the "
          "configured-ness checks correctly report unset in this environment.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
