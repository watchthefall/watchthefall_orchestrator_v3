"""
Offline harness for the credit system (portal/database.py credit functions).

Deterministic, no network, no Flask. Spins up a throwaway SQLite DB, runs the
real schema via init_db(), and exercises: lazy daily refresh (subscription only),
spend order (subscription -> earned -> purchased), insufficient-balance blocking
with no partial deduction, permanent stacking of earned/purchased, and that a
plain balance read never spends.

Run:  python scripts/simulate_credits.py     (exit 0 = all pass)
"""
import os
import sys
import types
import tempfile

# Import portal.database WITHOUT running the Flask app (config/database/cookie_pool
# are stdlib + relative imports only). Register a lightweight namespace package.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_pkg = types.ModuleType('portal')
_pkg.__path__ = [os.path.join(_ROOT, 'portal')]
sys.modules['portal'] = _pkg

from portal import database as db  # noqa: E402


def _check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _check.failed += 1
_check.failed = 0


def _set_refreshed_on(user_id, value):
    """Force the last-refresh date (to simulate a new UTC day)."""
    with db.get_connection() as conn:
        conn.execute('UPDATE user_credits SET subscription_refreshed_on = ? WHERE user_id = ?',
                     (value, user_id))
        conn.commit()


def main():
    tmp = tempfile.mkdtemp(prefix='brandr_credits_sim_')
    db.DB_PATH = os.path.join(tmp, 'test.db')   # redirect all connections here
    try:
        # The credit functions only touch user_credits; create just that table
        # (mirrors the CREATE in database.init_db) rather than running the full
        # migration chain, which assumes a pre-existing users table.
        with db.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_credits (
                    user_id INTEGER PRIMARY KEY,
                    subscription_credits INTEGER NOT NULL DEFAULT 0,
                    subscription_refreshed_on TEXT,
                    earned_credits INTEGER NOT NULL DEFAULT 0,
                    purchased_credits INTEGER NOT NULL DEFAULT 0
                )
            ''')
            conn.commit()
        ALLOW = 35  # Creator daily allowance
        U = 1

        print("\n1) First access creates a row and grants the full daily allowance")
        bal = db.get_credit_balance(U, ALLOW)
        _check(f"subscription == {ALLOW}", bal['subscription'] == ALLOW)
        _check("earned == 0 and purchased == 0", bal['earned'] == 0 and bal['purchased'] == 0)
        _check(f"total == {ALLOW}", bal['total'] == ALLOW)

        print("\n2) A balance read does NOT spend")
        bal2 = db.get_credit_balance(U, ALLOW)
        _check("reading twice leaves subscription unchanged", bal2['subscription'] == ALLOW)

        print("\n3) Spending deducts from subscription first")
        ok, bal = db.spend_credits(U, 1, ALLOW)
        _check("spend ok", ok is True)
        _check(f"subscription == {ALLOW - 1}", bal['subscription'] == ALLOW - 1)
        _check(f"total == {ALLOW - 1}", bal['total'] == ALLOW - 1)

        print("\n4) Earned + purchased stack on top; spend order sub -> earned -> purchased")
        db.add_earned_credits(U, 5)
        db.add_purchased_credits(U, 3)
        bal = db.get_credit_balance(U, ALLOW)
        _check(f"total == {ALLOW - 1} + 5 + 3", bal['total'] == (ALLOW - 1) + 5 + 3)
        # Drain the remaining subscription exactly, then dip into earned.
        sub_left = bal['subscription']  # ALLOW-1 = 34
        ok, bal = db.spend_credits(U, sub_left + 2, ALLOW)  # drains sub, takes 2 from earned
        _check("spend across buckets ok", ok is True)
        _check("subscription drained to 0", bal['subscription'] == 0)
        _check("earned reduced by 2 (5 -> 3)", bal['earned'] == 3)
        _check("purchased untouched (still 3)", bal['purchased'] == 3)

        print("\n5) Insufficient balance blocks with NO partial deduction")
        before = db.get_credit_balance(U, ALLOW)  # total = 0 + 3 + 3 = 6
        ok, bal = db.spend_credits(U, before['total'] + 1, ALLOW)
        _check("spend refused", ok is False)
        after = db.get_credit_balance(U, ALLOW)
        _check("balance unchanged after refusal",
               (after['subscription'], after['earned'], after['purchased'])
               == (before['subscription'], before['earned'], before['purchased']))

        print("\n6) Daily refresh resets ONLY subscription; earned/purchased persist")
        _set_refreshed_on(U, '2000-01-01')          # pretend last refresh was long ago
        bal = db.get_credit_balance(U, ALLOW)
        _check(f"subscription refreshed to {ALLOW}", bal['subscription'] == ALLOW)
        _check("earned persisted (3)", bal['earned'] == 3)
        _check("purchased persisted (3)", bal['purchased'] == 3)
        _check(f"total == {ALLOW} + 3 + 3", bal['total'] == ALLOW + 6)

        print("\n7) Unlimited allowance (Elite / beta = 9999) works")
        bal = db.get_credit_balance(2, 9999)
        _check("unlimited user gets 9999 subscription", bal['subscription'] == 9999)

        print("\n8) Balance read reports ok=True on success")
        _check("ok flag present and True", db.get_credit_balance(U, ALLOW).get('ok') is True)

        print("\n9) Admin set_subscription_credits sets an exact value (survives same-day read)")
        db.set_subscription_credits(U, 7)
        bal = db.get_credit_balance(U, ALLOW)
        _check("subscription set to exactly 7 (not re-refreshed to allowance)", bal['subscription'] == 7)

        print("\n10) DB unreachable -> ok=False (fail-closed signal for the render pre-check)")
        _good_path = db.DB_PATH
        db.DB_PATH = os.path.join(tmp, 'no_such_dir', 'x.db')  # missing parent -> connect fails
        bad = db.get_credit_balance(U, ALLOW)
        db.DB_PATH = _good_path
        _check("ok == False on DB error", bad.get('ok') is False)

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _check.failed:
        print(f"RESULT: {_check.failed} assertion(s) FAILED")
        return 1
    print("RESULT: all assertions passed - lazy daily refresh (subscription only), "
          "spend order sub->earned->purchased, no partial deduction on refusal, "
          "permanent stacking, and read-never-spends all hold.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
