# Gunicorn configuration for Render deployment
# This ensures consistent timeout settings regardless of how Render starts the app

import os
import sys

# Bind to PORT environment variable (required by Render)
# No fallback - Render always provides PORT
port = os.environ.get('PORT', '10000')
bind_address = f"0.0.0.0:{port}"

print(f"[GUNICORN CONFIG] PORT from env: {port}", file=sys.stderr)
print(f"[GUNICORN CONFIG] Binding to: {bind_address}", file=sys.stderr)

bind = bind_address

# Number of worker processes.
# MUST stay at 1 — Phase 18 stores async render jobs in brand_render_jobs,
# an in-process dict. Multiple workers = separate memory spaces = job_ids
# created in worker A are invisible to worker B → instant 404 on poll.
# When job state moves to SQLite/Redis this constraint can be relaxed.
# WEB_CONCURRENCY env var is ignored intentionally — hardcoded here to
# prevent accidental bumps via Render dashboard or platform defaults.
_requested = int(os.environ.get('WEB_CONCURRENCY', 1))
if _requested > 1:
    print(
        f"[GUNICORN CONFIG] WARNING: WEB_CONCURRENCY={_requested} requested "
        "but overriding to workers=1 — in-memory brand_render_jobs dict "
        "requires a single worker (Phase 18). Move job state to DB first.",
        file=sys.stderr,
    )
workers = 1

# Per-worker timeout (seconds).
# History: this was 900 to protect in-request FFmpeg renders — but renders
# and fetches both run in BACKGROUND THREADS now (job + poll), so no request
# should legitimately run long. 900s meant one wedged request took the whole
# single-worker site down for 15 minutes before the arbiter killed it
# (observed repeatedly 2026-08-21: [CRITICAL] WORKER TIMEOUT after every
# deploy switchover — the dying instance contends the shared SQLite disk and
# the fresh worker's first DB-touching request hangs). 300s bounds any wedge
# at 5 minutes while still allowing slow big-file downloads through Flask.
timeout = 300

# Graceful timeout for worker shutdown (seconds).
# Was 900: at deploy switchover the OLD instance lingered up to 15 minutes
# finishing wedged requests while holding locks on the shared /var/data
# SQLite — wedging the NEW instance's first requests. 30s makes the old
# instance release everything promptly; deploys are deliberate, and any
# in-flight render lost to a deploy shows as a clean "job lost, retry".
graceful_timeout = 30

# Keep-alive timeout (seconds)
keepalive = 5

# Worker class: sync. gthread was tried 2026-08-21 (deploy 850deb2) and
# HUNG every handler that does real work — only trivial 302/404s completed;
# full pages and DB-touching requests never returned. Rolled back the same
# hour. Root cause not yet isolated (suspect a lock or connection shared
# across request threads under preload_app). Do NOT re-enable gthread
# without reproducing and fixing that locally first. The fetch/render
# paths run in their own background threads, so the sync worker only
# handles short requests now that fetching is async (job + poll).
worker_class = "sync"

# Log level
loglevel = "info"

# Preload application code before forking worker processes
preload_app = True

# Worker tmp dir - /dev/shm not available on Render Free tier
# Comment out or use /tmp instead
# worker_tmp_dir = "/dev/shm"  # Only for Render Pro+

# Increase worker connections for better throughput
worker_connections = 1000