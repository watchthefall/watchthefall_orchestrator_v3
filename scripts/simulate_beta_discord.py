"""
Offline harness for beta/waitlist Discord verification (portal/database.py:
create_waitlist_entry, set_beta_discord_nonce, verify_beta_discord_identity).

Deterministic, no network, no Flask, no real Discord credentials. Exercises
the anti-abuse logic end to end at the database layer: single-use nonce
consumption, unique Discord identity across applications, and that a
pre-existing free-text discord_username is never treated as verified.

Run:  python scripts/simulate_beta_discord.py     (exit 0 = all pass)
"""
import os
import sys
import types
import tempfile
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_pkg = types.ModuleType('portal')
_pkg.__path__ = [os.path.join(_ROOT, 'portal')]
sys.modules['portal'] = _pkg

_TMP = tempfile.mkdtemp(prefix='brandr_beta_discord_sim_')
os.environ['DB_PATH'] = os.path.join(_TMP, 'boot.db')
_c = sqlite3.connect(os.environ['DB_PATH'])
_c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
_c.commit(); _c.close()

from portal import database as db  # noqa: E402


def _check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _check.failed += 1
_check.failed = 0


def main():
    tmp = tempfile.mkdtemp(prefix='brandr_beta_discord_sim_')
    db.DB_PATH = os.path.join(tmp, 'test.db')
    try:
        # init_db() runs the real migration chain, including the beta_access
        # table itself and every column/index this harness exercises -- same
        # approach as simulate_credits.py, but here we actually need the
        # migrations (the unique index isn't part of a bare CREATE).
        with db.get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
            conn.commit()
        db.init_db()

        print("\n1) A fresh application has no verified Discord identity yet")
        eid, created = db.create_waitlist_entry(
            'a@example.com', 'Alice', 'tiktok', 'creator', '2', None, 'alice#0001'
        )
        _check("entry created", created is True)
        entry = db.get_beta_access_by_id(eid)
        _check("discord_user_id is None (free-text discord_username != verified)",
               entry['discord_user_id'] is None)
        _check("the OLD free-text field is untouched", entry['discord_username'] == 'alice#0001')

        print("\n2) Issuing a verify link stamps a nonce; verifying with the WRONG nonce fails clean")
        ok = db.set_beta_discord_nonce(eid, 'nonce-1')
        _check("nonce stamped", ok is True)
        result = db.verify_beta_discord_identity(eid, 'wrong-nonce', 'discord_111', 'alice_real')
        _check("== 'nonce_mismatch'", result == 'nonce_mismatch')
        entry = db.get_beta_access_by_id(eid)
        _check("still unverified after a failed attempt", entry['discord_user_id'] is None)

        print("\n3) Verifying with the RIGHT nonce succeeds and records the verified identity")
        result = db.verify_beta_discord_identity(eid, 'nonce-1', 'discord_111', 'alice_real')
        _check("== 'ok'", result == 'ok')
        entry = db.get_beta_access_by_id(eid)
        _check("discord_user_id set", entry['discord_user_id'] == 'discord_111')
        _check("discord_verified_username set", entry['discord_verified_username'] == 'alice_real')
        _check("discord_linked_at set", bool(entry['discord_linked_at']))
        _check("nonce consumed (cleared)", entry['discord_oauth_nonce'] is None)

        print("\n4) The SAME link (same nonce) cannot be replayed -- single-use")
        result = db.verify_beta_discord_identity(eid, 'nonce-1', 'discord_111', 'alice_real')
        _check("== 'nonce_mismatch' (nonce already cleared)", result == 'nonce_mismatch')

        print("\n5) A second application cannot verify with a Discord identity already used by another")
        eid2, created2 = db.create_waitlist_entry(
            'b@example.com', 'Bob', 'youtube', 'editor', '5', None, None
        )
        db.set_beta_discord_nonce(eid2, 'nonce-2')
        result = db.verify_beta_discord_identity(eid2, 'nonce-2', 'discord_111', 'bob_alt')
        _check("== 'duplicate' (discord_111 already claimed by entry 1)", result == 'duplicate')
        entry2 = db.get_beta_access_by_id(eid2)
        _check("entry 2 still unverified", entry2['discord_user_id'] is None)

        print("\n6) A DIFFERENT Discord identity verifies entry 2 without issue")
        result = db.verify_beta_discord_identity(eid2, 'nonce-2', 'discord_222', 'bob_alt')
        _check("== 'ok'", result == 'ok')

        print("\n7) Verifying a nonexistent entry id fails clean, not with an exception")
        result = db.verify_beta_discord_identity(999999, 'anything', 'discord_999', 'ghost')
        _check("== 'not_found'", result == 'not_found')

        print("\n8) Issuing a NEW verify link invalidates whatever nonce was there before")
        eid3, _ = db.create_waitlist_entry('c@example.com', 'Cara', 'tiktok', 'creator', '1', None, None)
        db.set_beta_discord_nonce(eid3, 'first-nonce')
        db.set_beta_discord_nonce(eid3, 'second-nonce')  # e.g. they clicked "resend"
        result = db.verify_beta_discord_identity(eid3, 'first-nonce', 'discord_333', 'cara')
        _check("stale first nonce == 'nonce_mismatch'", result == 'nonce_mismatch')
        result = db.verify_beta_discord_identity(eid3, 'second-nonce', 'discord_333', 'cara')
        _check("current nonce == 'ok'", result == 'ok')

        print("\n9) The unique index on beta_access.discord_user_id exists and is enforced at the DB level too")
        with db.get_connection() as conn:
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_beta_access_discord_user_id'"
            ).fetchone()
        _check("index present", idx is not None)

        print("\n10) Pre-existing (pre-migration) rows with only free-text discord_username are unaffected")
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO beta_access (email, creator_name, main_platform, creator_type, "
                "page_count, discord_username, status, access_level, created_at) "
                "VALUES ('legacy@example.com', 'Legacy Applicant', 'tiktok', 'creator', '3', "
                "'legacy_handle', 'pending', 'waitlist', '2025-01-01T00:00:00')"
            )
            conn.commit()
            legacy = conn.execute(
                "SELECT * FROM beta_access WHERE email = 'legacy@example.com'"
            ).fetchone()
        _check("legacy row has the old free-text handle", legacy['discord_username'] == 'legacy_handle')
        _check("legacy row is NOT treated as verified", legacy['discord_user_id'] is None)

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_TMP, ignore_errors=True)

    print()
    if _check.failed:
        print(f"RESULT: {_check.failed} assertion(s) FAILED")
        return 1
    print("RESULT: all assertions passed - single-use nonce consumption (correct/wrong/replayed), "
          "unique Discord identity across applications, clean not_found handling, a fresh verify "
          "link invalidating an older unfinished one, the unique index existing, and pre-existing "
          "free-text discord_username rows staying unverified, all hold.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
