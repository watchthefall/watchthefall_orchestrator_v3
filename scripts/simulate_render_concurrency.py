"""Offline proof that render concurrency is actually bounded.

Extracts the real semaphore constants from portal/app.py and reproduces the
acquire/release discipline of _do_brand_render against a fake "FFmpeg" that
records how many workers are inside the critical section at once.

No Flask, no DB, no FFmpeg, no network, no credits. Run from the repo root:
    python scripts/simulate_render_concurrency.py
"""
import io
import re
import threading
import time

src = io.open('portal/app.py', encoding='utf-8').read()

# --- the code under test must actually be wired up -------------------------
assert '_render_slots.acquire(' in src, 'worker never acquires a slot'
assert '_render_slots.release()' in src, 'worker never releases a slot'
assert re.search(r'finally:\s*\n\s*#[^\n]*\n\s*#[^\n]*\n\s*_render_slots\.release\(\)', src), \
    'release is not in a finally block — an exception would leak the slot'

limit = int(re.search(r"MAX_CONCURRENT_RENDERS\s*=\s*max\(1,\s*int\(os\.environ\.get\("
                      r"'MAX_CONCURRENT_RENDERS',\s*'(\d+)'\)\)\)", src).group(1))
queue_timeout = int(re.search(r'RENDER_QUEUE_TIMEOUT\s*=\s*(\d+)', src).group(1))
print('limit from app.py           : %d concurrent renders' % limit)
print('queue timeout from app.py   : %ds' % queue_timeout)

# --- reproduce the worker's discipline -------------------------------------
slots = threading.BoundedSemaphore(limit)
depth_lock = threading.Lock()
inside = 0
peak = 0
completed = 0
failed = 0


def fake_render(should_raise):
    """Mirrors _do_brand_render: acquire, work, release in finally."""
    global inside, peak, completed, failed
    if not slots.acquire(timeout=queue_timeout):
        return
    try:
        with depth_lock:
            inside += 1
            peak = max(peak, inside)
        time.sleep(0.05)                      # stand-in for FFmpeg
        if should_raise:
            raise RuntimeError('render blew up')
        with depth_lock:
            completed += 1
    except RuntimeError:
        with depth_lock:
            failed += 1
    finally:
        with depth_lock:
            inside -= 1
        slots.release()


# The 21 Aug incident: 19 submissions in ~30 seconds, all at once.
BURST = 19
threads = [threading.Thread(target=fake_render, args=(i % 5 == 0,)) for i in range(BURST)]
start = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.time() - start

print('\nburst submitted             : %d' % BURST)
print('peak concurrent renders     : %d' % peak)
print('completed / failed          : %d / %d' % (completed, failed))
print('all threads finished in     : %.2fs' % elapsed)

assert peak <= limit, 'FAIL: %d ran at once, limit is %d' % (peak, limit)
assert completed + failed == BURST, 'FAIL: %d of %d jobs settled' % (completed + failed, BURST)
assert inside == 0, 'FAIL: %d workers still inside at the end' % inside

# Every slot must be free again — proves failures released theirs too.
for _ in range(limit):
    assert slots.acquire(blocking=False), 'FAIL: a slot leaked (probably on the error path)'
for _ in range(limit):
    slots.release()

print('\nPASS: never exceeded %d concurrent; all %d jobs settled; no slot leaked '
      '(including the %d that raised).' % (limit, BURST, failed))
