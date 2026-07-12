"""
Offline test harness for the Instagram cookie pool (portal/cookie_pool.py).

Deterministic, no network, no yt-dlp. It exercises discovery, validation, LRU
rotation, auth-failure detection, and the failover/cooldown contract by
replaying the exact decision logic app.py uses around a fetch.

Run:  python scripts/simulate_cookie_rotation.py     (exit 0 = all pass)
"""
import os
import sys
import tempfile

# cookie_pool.py imports only stdlib, so we can load it directly from portal/
# without pulling in the Flask app.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'portal'))
import cookie_pool  # noqa: E402

# A minimal well-formed Netscape cookie for account N (has sessionid+csrftoken).
def _cookie_text(uid):
    return (
        "# Netscape HTTP Cookie File\n"
        f".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\t{uid}%3Aabc\n"
        f".instagram.com\tTRUE\t/\tTRUE\t9999999999\tcsrftoken\ttok{uid}\n"
    )


def _check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _check.failed += 1
_check.failed = 0


def _simulate_fetch(auth_fail_for):
    """Replay app.py's rotation loop. ``auth_fail_for`` is a set of cookie
    basenames that should 'auth-fail'; the first cookie not in that set
    'succeeds'. Returns (succeeded_path, tried_bad_paths, all_failed)."""
    candidates = cookie_pool.candidates_lru()
    tried_bad = []
    for path in candidates:
        cookie_pool.mark_used(path)
        name = os.path.basename(path)
        if name in auth_fail_for:                    # simulate auth failure
            if path is not candidates[-1]:
                tried_bad.append(path)
                continue
            # last candidate also failed -> all failed
            return None, tried_bad, True
        # success
        cookie_pool.mark_success(path)
        for b in tried_bad:
            cookie_pool.mark_bad(b)
        return path, tried_bad, False
    return None, tried_bad, True


def main():
    tmp = tempfile.mkdtemp(prefix='brandr_cookie_sim_')
    saved_env = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith('INSTAGRAM_COOKIES'):
                del os.environ[k]

        print("\n1) Single cookie (only INSTAGRAM_COOKIES) - legacy behaviour preserved")
        os.environ['INSTAGRAM_COOKIES'] = _cookie_text('1001')
        cookie_pool.bootstrap_pool(tmp)
        _check("pool size == 1", cookie_pool.pool_size() == 1)
        _check("candidate is cookies_base.txt",
               [os.path.basename(p) for p in cookie_pool.candidates_lru()] == ['cookies_base.txt'])

        print("\n2) Discovery of INSTAGRAM_COOKIES_1..3 + validation (bad one skipped)")
        os.environ['INSTAGRAM_COOKIES_1'] = _cookie_text('2002')
        os.environ['INSTAGRAM_COOKIES_2'] = _cookie_text('3003')
        os.environ['INSTAGRAM_COOKIES_3'] = "# Netscape HTTP Cookie File\n(missing auth cookies)\n"
        cookie_pool.bootstrap_pool(tmp)
        names = [os.path.basename(p) for p in cookie_pool.candidates_lru()]
        _check("pool size == 3 (base,1,2; the malformed _3 skipped)", cookie_pool.pool_size() == 3)
        _check("cookies_3.txt excluded (invalid)", 'cookies_3.txt' not in names)

        print("\n3) is_auth_failure - signal vs content errors")
        _check("'empty media response' -> auth failure",
               cookie_pool.is_auth_failure("Instagram sent an empty media response"))
        _check("'HTTP Error 403' -> auth failure",
               cookie_pool.is_auth_failure("HTTP Error 403: Forbidden"))
        _check("'video is private' -> NOT auth failure",
               not cookie_pool.is_auth_failure("The video is private"))

        print("\n4) LRU rotation - least-recently-used goes first")
        cookie_pool.bootstrap_pool(tmp)  # reset last_used
        first = os.path.basename(cookie_pool.candidates_lru()[0])
        cookie_pool.mark_used(cookie_pool.candidates_lru()[0])  # touch base
        nxt = [os.path.basename(p) for p in cookie_pool.candidates_lru()]
        _check("after using the first, it moves to the back of LRU order",
               nxt[-1] == first)

        print("\n5) Failover: first cookie auth-fails, next succeeds")
        cookie_pool.bootstrap_pool(tmp)
        order = [os.path.basename(p) for p in cookie_pool.candidates_lru()]
        won, bad, allfail = _simulate_fetch(auth_fail_for={order[0]})
        _check("a later cookie succeeded", won is not None and not allfail)
        _check("the failed first cookie was cooled down (dropped from candidates)",
               order[0] not in [os.path.basename(p) for p in cookie_pool.candidates_lru()])
        _check("the winner is still available",
               os.path.basename(won) in [os.path.basename(p) for p in cookie_pool.candidates_lru()])

        print("\n6) All cookies fail -> pool NOT nuked (nobody cooled), friendly error path")
        cookie_pool.bootstrap_pool(tmp)
        allnames = {os.path.basename(p) for p in cookie_pool.candidates_lru()}
        won, bad, allfail = _simulate_fetch(auth_fail_for=allnames)
        _check("all-fail reported", won is None and allfail)
        _check("every cookie still available next request (no cooldown on ambiguous all-fail)",
               cookie_pool.pool_size() == len(cookie_pool.candidates_lru()))

        print("\n7) Circuit breaker: closed by default, opens on trip, closes on reset")
        cookie_pool.reset_breaker()
        _check("breaker starts closed", cookie_pool.breaker_open() is False)
        cookie_pool.trip_breaker()
        _check("breaker open after all-fail trip", cookie_pool.breaker_open() is True)
        _check("breaker_remaining > 0", cookie_pool.breaker_remaining() > 0)
        cookie_pool.reset_breaker()
        _check("breaker closed after a successful fetch (reset)", cookie_pool.breaker_open() is False)

    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _check.failed:
        print(f"RESULT: {_check.failed} assertion(s) FAILED")
        return 1
    print("RESULT: all assertions passed - pool discovers, validates, rotates LRU, "
          "fails over on auth errors, and never nukes the pool on an ambiguous all-fail.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
