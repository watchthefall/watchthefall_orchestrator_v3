"""One account must not be able to occupy the whole renderer.

THE GAP. MAX_CONCURRENT_RENDERS=2 protects the BOX -- CPU, RAM, FFmpeg
processes -- but says nothing about whose renders those are. With no
per-account control, one account could hold both machine slots for hours (a
Platinum job may legally queue max_outputs_per_job=150 renders, ~110 minutes on
one slot at the measured ~44s median) while every other account waited the full
RENDER_QUEUE_TIMEOUT and then failed. A fairness failure, not a capacity one.

This suite runs the REAL admission helpers, AST-extracted out of portal/app.py
and executed, rather than a re-implementation that would only agree with itself.

No Flask, no DB, no FFmpeg, no network, no credits:
    python scripts/simulate_account_fairness.py
"""
import ast
import io
import os
import re
import threading
import time

APP = io.open(os.path.join('portal', 'app.py'), encoding='utf-8').read()

PASS = 0


def ok(label, extra=''):
    global PASS
    PASS += 1
    print('  ok  %-56s %s' % (label, extra))


# --- load the real helpers --------------------------------------------------
ns = {'threading': threading, 'time': time, 'os': os}
for node in ast.parse(APP).body:
    nm = None
    if isinstance(node, ast.FunctionDef):
        nm = node.name
    elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        nm = node.targets[0].id
    if nm in ('MAX_RENDERS_PER_ACCOUNT', '_account_render_cv', '_account_render_active',
              '_acquire_account_render_slot', '_release_account_render_slot'):
        exec(ast.get_source_segment(APP, node), ns)

acquire_acct = ns['_acquire_account_render_slot']
release_acct = ns['_release_account_render_slot']
PER_ACCOUNT = ns['MAX_RENDERS_PER_ACCOUNT']
ACTIVE = ns['_account_render_active']

MACHINE = int(re.search(r"MAX_CONCURRENT_RENDERS = max\(1, int\(os\.environ\.get\("
                        r"'MAX_CONCURRENT_RENDERS',\s*'(\d+)'\)\)\)", APP).group(1))
QUEUE_TIMEOUT = int(re.search(r'RENDER_QUEUE_TIMEOUT = (\d+)', APP).group(1))
print('machine slots      : %d' % MACHINE)
print('per-account slots  : %d' % PER_ACCOUNT)
print('queue timeout      : %ds (unchanged)' % QUEUE_TIMEOUT)


# --- part 1: the wiring in _do_brand_render ---------------------------------
print('\n[admission order and release discipline]')
body = APP[APP.index('def _do_brand_render('):APP.index("@app.route('/api/videos/process_brands'")]

i_acct = body.index('_acquire_account_render_slot(user_id')
i_mach = body.index('_render_slots.acquire(')
assert i_acct < i_mach, 'machine slot is taken before the account slot'
ok('account slot is acquired BEFORE the machine slot',
   'reverse order would hold a scarce slot while self-blocked')

assert 'RENDER_QUEUE_TIMEOUT - (time.monotonic() - _budget_started)' in body
ok('both acquisitions share ONE budget', 'total wait is unchanged at %ds' % QUEUE_TIMEOUT)
assert '_render_slots.acquire(timeout=RENDER_QUEUE_TIMEOUT)' not in body
ok('the machine acquire no longer gets its own full timeout')

# Every exit must release: the two queue-timeout returns, and the finally that
# covers success, render/normalization/validation failure, timeout, exception.
assert body.count('_release_account_render_slot(user_id)') == 2
ok('released on the machine-timeout path and in the finally')
tail = body[body.index('_render_slots.release()'):]
assert '_release_account_render_slot(user_id)' in tail
ok('finally releases machine slot then account slot', 'reverse of acquisition')
fin = body.rfind('finally:', 0, body.index('_render_slots.release()'))
between = body[fin:body.index('_render_slots.release()')]
assert 'def ' not in between and 'except ' not in between
ok('the release really is inside the finally block')

# The account-slot-timeout path returns BEFORE holding anything, so it must not
# release a slot it never took.
acct_fail = body[body.index('if not got_account:'):body.index('# 2) A machine slot')]
assert '_release_account_render_slot' not in acct_fail
assert '_render_slots.release()' not in acct_fail
ok('the account-timeout path releases nothing it never acquired')


# --- part 2: behaviour, against real threads --------------------------------
print('\n[6 jobs from account A, 1 from account B, all submitted at once]')

machine = threading.BoundedSemaphore(MACHINE)
lock = threading.Lock()
per_user_now, per_user_peak = {}, {}
overall_now = overall_peak = 0
events = []          # (t, user, 'start'|'end')
settled = {'ok': 0, 'failed': 0}
RENDER = 0.15


def job(user, should_raise):
    global overall_now, overall_peak
    if not acquire_acct(user, QUEUE_TIMEOUT):
        return
    try:
        if not machine.acquire(timeout=QUEUE_TIMEOUT):
            return
        try:
            with lock:
                per_user_now[user] = per_user_now.get(user, 0) + 1
                per_user_peak[user] = max(per_user_peak.get(user, 0), per_user_now[user])
                overall_now += 1
                overall_peak = max(overall_peak, overall_now)
                events.append((time.monotonic(), user, 'start'))
            time.sleep(RENDER)                     # stand-in for FFmpeg
            if should_raise:
                raise RuntimeError('render blew up')
            with lock:
                settled['ok'] += 1
        except RuntimeError:
            with lock:
                settled['failed'] += 1
        finally:
            with lock:
                per_user_now[user] -= 1
                overall_now -= 1
                events.append((time.monotonic(), user, 'end'))
            machine.release()
    finally:
        release_acct(user)


start_gate = threading.Barrier(7)


def runner(user, raises):
    start_gate.wait()
    job(user, raises)


threads = [threading.Thread(target=runner, args=('A', i == 2)) for i in range(6)]
threads.append(threading.Thread(target=runner, args=('B', False)))
t0 = time.monotonic()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.monotonic() - t0

a_peak = per_user_peak.get('A', 0)
b_peak = per_user_peak.get('B', 0)
print('  peak concurrent for A : %d' % a_peak)
print('  peak concurrent for B : %d' % b_peak)
print('  peak overall          : %d' % overall_peak)
print('  settled ok/failed     : %d / %d' % (settled['ok'], settled['failed']))
print('  wall clock            : %.2fs' % elapsed)

assert a_peak <= PER_ACCOUNT, 'account A ran %d at once, cap is %d' % (a_peak, PER_ACCOUNT)
ok('account A never exceeded its cap', '%d concurrent' % a_peak)
assert overall_peak <= MACHINE
ok('machine limit still respected', '%d concurrent' % overall_peak)
assert settled['ok'] + settled['failed'] == 7
ok('every job settled', '%d ok, %d failed' % (settled['ok'], settled['failed']))

# THE FAIRNESS PROPERTY: B got in while A was busy, rather than queueing behind
# all six of A's renders.
b_start = next(t for t, u, k in events if u == 'B' and k == 'start')
b_end = next(t for t, u, k in events if u == 'B' and k == 'end')
a_done_before_b = sum(1 for t, u, k in events if u == 'A' and k == 'end' and t < b_start)

# Overlap measured across B's WHOLE render, not at the instant it started. An
# earlier version asserted that some A render was already in flight the moment B
# began -- which failed because B won the race and started FIRST. That is a
# better outcome than the one being asserted, so the assertion was wrong, not
# the code. What actually matters is that two DIFFERENT accounts held the two
# machine slots at the same time.
a_intervals = []
open_a = {}
for t, u, k in events:
    if u != 'A':
        continue
    if k == 'start':
        open_a[t] = t
    else:
        st = min(open_a) if open_a else t
        open_a.pop(st, None)
        a_intervals.append((st, t))
overlapped = any(st < b_end and en > b_start for st, en in a_intervals)

print('  A renders finished before B started : %d of 6' % a_done_before_b)
print('  A and B held the two slots together : %s' % overlapped)
assert overlapped, 'A and B never rendered concurrently'
ok('two different accounts held the two slots at once',
   'the second slot stayed reachable')
assert a_done_before_b <= 1, 'B waited behind %d of A\'s renders' % a_done_before_b
ok('B did not queue behind A\'s backlog', '%d of 6 finished first' % a_done_before_b)

# Without the cap, A would have taken both slots and B would have waited behind
# roughly half of A's six renders. Same six-plus-one shape, no account control:
mach2 = threading.BoundedSemaphore(MACHINE)
ev2, lk2 = [], threading.Lock()


def job_nocap(user):
    mach2.acquire()
    try:
        with lk2:
            ev2.append((time.monotonic(), user, 'start'))
        time.sleep(RENDER)
    finally:
        with lk2:
            ev2.append((time.monotonic(), user, 'end'))
        mach2.release()


gate2 = threading.Barrier(7)


def runner2(user):
    gate2.wait()
    if user == 'B':
        time.sleep(0.01)          # B arrives a hair later, as a latecomer would
    job_nocap(user)


ts2 = [threading.Thread(target=runner2, args=('A',)) for _ in range(6)]
ts2.append(threading.Thread(target=runner2, args=('B',)))
for t in ts2:
    t.start()
for t in ts2:
    t.join()
b2 = next(t for t, u, k in ev2 if u == 'B' and k == 'start')
a_done_before_b2 = sum(1 for t, u, k in ev2 if u == 'A' and k == 'end' and t < b2)
print('\n  WITHOUT the cap, A renders finished before B started: %d of 6' % a_done_before_b2)
if a_done_before_b2 > a_done_before_b:
    ok('the cap measurably improved B\'s position',
       '%d -> %d' % (a_done_before_b2, a_done_before_b))
else:
    print('      (scheduling did not separate the two runs this time; the '
          'guarantees asserted above are what matters)')


# --- part 3: no leaks -------------------------------------------------------
print('\n[nothing leaks]')
assert not ACTIVE, 'account table still holds %r' % ACTIVE
ok('account table is empty', 'keys are popped, not left at zero')
for _ in range(MACHINE):
    assert machine.acquire(blocking=False), 'a machine slot leaked'
for _ in range(MACHINE):
    machine.release()
ok('all machine slots free again', 'including after the render that raised')

assert acquire_acct(None, 1) is True
release_acct(None)
ok('a job with no user_id is admitted', 'machine semaphore still governs it')

assert acquire_acct('C', 5) is True
t_start = time.monotonic()
assert acquire_acct('C', 0.3) is False
waited = time.monotonic() - t_start
release_acct('C')
ok('a second render for one account times out cleanly', 'waited %.2fs' % waited)
assert not ACTIVE, ACTIVE
ok('and releases leave no residue')

print('\n%d assertions passed.' % PASS)
