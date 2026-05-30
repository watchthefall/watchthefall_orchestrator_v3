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

# Per-worker timeout (seconds)
# FFmpeg on long videos (60-120s clips) can take 5-15 min on shared CPU.
# Keep this above FFMPEG_TIMEOUT (840s) so gunicorn doesn't SIGKILL before
# the subprocess timeout fires — clean Python error is better than silent kill.
timeout = 900

# Graceful timeout for worker shutdown (seconds)
graceful_timeout = 900

# Keep-alive timeout (seconds)
keepalive = 5

# Worker class (default is sync)
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