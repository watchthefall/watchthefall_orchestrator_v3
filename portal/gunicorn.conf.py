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

# Number of worker processes
workers = 1

# Per-worker timeout (seconds)
timeout = 300

# Graceful timeout for worker shutdown (seconds)
graceful_timeout = 300

# Keep-alive timeout (seconds)
keepalive = 5

# Worker class (default is sync)
worker_class = "sync"

# Log level
loglevel = "info"

# Preload application code before forking worker processes
preload_app = True

# Take advantage of Render Pro resources
# Enable worker tmp dir for better performance
worker_tmp_dir = "/dev/shm"

# Increase worker connections for better throughput
worker_connections = 1000