# Gunicorn configuration for Render deployment
# This ensures consistent timeout settings regardless of how Render starts the app

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

# For Render Free Tier compatibility
# Remove Pro-specific settings that may cause issues on Free Tier