"""
Offline harness for render telemetry (portal/database.py render_events functions).

Deterministic, no network, no Flask. Spins up a throwaway SQLite DB, creates the
real render_events schema, logs a known set of render events, and verifies that
get_render_stats (fleet aggregates + cost math) and get_user_render_stats
(per-user, heaviest first) compute the expected numbers. Also checks that
logging never raises and tolerates a NULL output size.

Run:  python scripts/simulate_render_events.py     (exit 0 = all pass)
"""
import os
import sys
import types
import tempfile

# Import portal.database WITHOUT running the Flask app.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_pkg = types.ModuleType('portal')
_pkg.__path__ = [os.path.join(_ROOT, 'portal')]
sys.modules['portal'] = _pkg

# database.py runs init_db() at import; point DB_PATH at a throwaway DB with a
# bare users table so the import doesn't touch the real dev DB.
import sqlite3  # noqa: E402
_TMP = tempfile.mkdtemp(prefix='brandr_render_sim_')
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
    tmp = tempfile.mkdtemp(prefix='brandr_render_sim_')
    db.DB_PATH = os.path.join(tmp, 'test.db')
    try:
        # Create just the render_events table (mirrors init_db's CREATE).
        with db.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS render_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    job_id TEXT,
                    brand_id INTEGER,
                    brand_name TEXT,
                    output_format TEXT,
                    render_seconds REAL,
                    output_kb INTEGER,
                    brand_count INTEGER,
                    created_on TEXT,
                    created_at TEXT
                )
            ''')
            conn.commit()

        # Known dataset:
        #   user 1: 10s/1000kb, 20s/2000kb, 60s/3000kb   (3 renders)
        #   user 2: 100s/10000kb, 400s/20000kb           (2 renders)
        # secs=[10,20,60,100,400] -> median 60, mean 118, p95 400, total 590s
        print("\n1) Logging returns True and never raises")
        events = [
            (1, 'jobA', 11, 'england',    'vertical_9_16', 10.0, 1000, 1),
            (1, 'jobB', 12, 'scotlandwtf','vertical_9_16', 20.0, 2000, 1),
            (1, 'jobC', 13, 'britainwtf', 'square_1_1',    60.0, 3000, 1),
            (2, 'jobD', 21, 'europewtf',  'vertical_9_16', 100.0, 10000, 2),
            (2, 'jobD', 22, 'germany',    'vertical_9_16', 400.0, 20000, 2),
        ]
        for e in events:
            ok = db.log_render_event(user_id=e[0], job_id=e[1], brand_id=e[2],
                                     brand_name=e[3], output_format=e[4],
                                     render_seconds=e[5], output_kb=e[6],
                                     brand_count=e[7])
            _check(f"log user={e[0]} {e[5]}s ok", ok is True)

        print("\n2) A NULL output size is tolerated (render still logged)")
        _check("log with output_kb=None ok",
               db.log_render_event(1, 'jobE', 14, 'wtf', 'vertical_9_16', 5.0, None, 1) is True)

        print("\n3) Fleet stats: counts, duration distribution")
        s = db.get_render_stats(days=30, cost_per_month_gbp=20.0)
        _check("renders == 6", s['renders'] == 6)
        _check("unique_users == 2", s['unique_users'] == 2)
        _check("renders_per_user == 3.0", s['renders_per_user'] == 3.0)
        # secs including the 5.0 NULL-size render: [5,10,20,60,100,400]
        _check("median == 40.0", s['median_render_seconds'] == 40.0)   # (20+60)/2
        _check("mean == 99.2", s['mean_render_seconds'] == 99.2)        # 595/6
        _check("p95 == 400.0", s['p95_render_seconds'] == 400.0)
        _check("total_compute_hours ~ 0.17", abs(s['total_compute_hours'] - 0.17) < 0.01)

        print("\n4) Output-size average ignores the NULL row")
        # kbs = [1000,2000,3000,10000,20000] -> 36000/5 = 7200
        _check("avg_output_kb == 7200", s['avg_output_kb'] == 7200)

        print("\n5) Cost math: loaded falls out of volume, marginal from render seconds")
        # 6 renders over 30 days -> 6/month; loaded = 20/6 = 3.3333
        _check("est_renders_per_month == 6", s['est_renders_per_month'] == 6)
        _check("loaded_cost_per_render ~ 3.3333", abs(s['loaded_cost_per_render_gbp'] - 3.3333) < 0.001)
        # marginal = mean_secs(99.1667) * (20 / 2,592,000)
        exp_marg = round((595.0 / 6) * (20.0 / (30 * 86400)), 5)
        _check(f"marginal_cost_per_render == {exp_marg}",
               s['marginal_cost_per_render_gbp'] == exp_marg)

        print("\n6) Per-user breakdown, heaviest first")
        u = db.get_user_render_stats(days=30)
        _check("two users returned", len(u) == 2)
        _check("heaviest is user 1 (4 renders)", u[0]['user_id'] == 1 and u[0]['renders'] == 4)
        _check("user 1 total compute == 95s", u[0]['total_compute_seconds'] == 95.0)  # 10+20+60+5
        _check("user 2 has 2 renders", u[1]['user_id'] == 2 and u[1]['renders'] == 2)
        _check("user 2 avg == 250s", u[1]['avg_render_seconds'] == 250.0)
        _check("user 2 total output == 29.3MB", u[1]['total_output_mb'] == round(30000/1024.0, 1))

        print("\n7) Window filter excludes old rows")
        # Backdate every row 60 days; a 30-day window should now see nothing.
        with db.get_connection() as conn:
            conn.execute("UPDATE render_events SET created_on = '2000-01-01'")
            conn.commit()
        s2 = db.get_render_stats(days=30)
        _check("no renders in 30-day window after backdating", s2['renders'] == 0)
        _check("empty-window stats don't crash (median None)", s2['median_render_seconds'] is None)

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_TMP, ignore_errors=True)

    print()
    if _check.failed:
        print(f"RESULT: {_check.failed} assertion(s) FAILED")
        return 1
    print("RESULT: all assertions passed - per-render logging (NULL-tolerant), fleet "
          "aggregates (median/mean/p95/total), loaded+marginal cost math, heaviest-user "
          "breakdown, and the lookback-window filter all hold.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
