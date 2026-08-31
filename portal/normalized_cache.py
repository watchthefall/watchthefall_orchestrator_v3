"""Lifetime protection for normalized temp files.

WHY THIS EXISTS
---------------
`sweep_normalized_temp_files()` deletes anything matching `*_normalized_*.mp4`
older than 30 minutes, and its safety argument is that these files are
"regenerated on every render and never read again once their render settles
(max render time ~14 min << 30 min)". That holds only while every job owns a
PRIVATE normalized file, which is true today because the filename carries the
job id.

Once normalized files are shared between jobs (the command-identity cache), that
assumption breaks: a file produced for job A may be needed by job B twenty-five
minutes later, and a 30-job batch at 2 concurrent renders runs well past the
30-minute cutoff. The sweep would then delete a queued job's input.

This module is the protection, shipped BEFORE sharing so that sharing is safe to
add. On its own it changes no behaviour — nothing is shared yet, so no reference
outlives its own render.

INVARIANT: no active render can have its referenced normalized file swept away.

Lives in its own module so `app.py` (which claims) and `database.py` (which
sweeps) can both use it without importing each other.

Single-process by design: WEB_CONCURRENCY=1 and the in-memory `brand_render_jobs`
registry already assume one worker. A second worker would need this state in
SQLite or Redis, exactly as the job dict would.
"""

import os
import threading
import time

# abspath -> number of live claims. Guarded by _lock for all read/modify/write.
_refs = {}
_lock = threading.Lock()


def claim(path):
    """Take a reference on a normalized file and confirm it is really there.

    Returns True when the caller may use the path, False when the file was gone
    at claim time and must be regenerated.

    Order matters: the reference is recorded BEFORE the existence check, so a
    sweep running concurrently cannot delete the file in the gap between us
    seeing it and us protecting it. If the file turns out to be missing we undo
    the reference and report failure rather than handing a dead path to FFmpeg.
    """
    if not path:
        return False
    key = os.path.abspath(path)

    with _lock:
        _refs[key] = _refs.get(key, 0) + 1

    if not os.path.isfile(key):
        # Vanished before we got here (only reachable once files are shared).
        # Undo the reference so a missing file cannot pin a phantom entry.
        release(key)
        return False

    # Keep it clear of the age-based cutoff for the next 30 minutes even if the
    # reference is dropped and re-taken later.
    try:
        os.utime(key, None)
    except OSError:
        pass  # cosmetic only — the reference is what actually protects it
    return True


def release(path):
    """Drop one reference. Safe to call for a path that was never claimed."""
    if not path:
        return
    key = os.path.abspath(path)
    with _lock:
        remaining = _refs.get(key, 0) - 1
        if remaining > 0:
            _refs[key] = remaining
        else:
            _refs.pop(key, None)


def is_protected(path):
    """True while at least one render holds a reference to this path."""
    if not path:
        return False
    with _lock:
        return os.path.abspath(path) in _refs


def protected_paths():
    """Snapshot of every referenced path, for the sweep to skip."""
    with _lock:
        return frozenset(_refs)


def active_count():
    """Number of distinct referenced paths — observability only."""
    with _lock:
        return len(_refs)


def debug_snapshot():
    """Per-path reference counts, for an admin/debug view."""
    now = time.time()
    with _lock:
        items = list(_refs.items())
    out = []
    for path, count in items:
        try:
            age = int(now - os.path.getmtime(path))
        except OSError:
            age = None
        out.append({'file': os.path.basename(path), 'refs': count, 'age_s': age})
    return out
