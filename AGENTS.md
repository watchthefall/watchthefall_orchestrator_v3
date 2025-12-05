# AGENTS.md — Qoder Project Rules for WatchTheFall Portal

This document defines the architecture, constraints, coding rules, and
operational expectations for the WTF Portal backend. Qoder must always follow
these rules unless explicitly instructed otherwise.

===================================================
# 1. CODEBASE — PROJECT OVERVIEW
===================================================
- Language: Python 3.10+
- Framework: Flask (Web API)
- Deployment: Render Web Service
- Web server: Gunicorn (configured via portal/gunicorn.conf.py)
- Media tools: yt-dlp + FFmpeg
- Purpose: 
  - Download videos from TikTok, Instagram, YouTube, X/Twitter, Facebook
  - Process videos using brand-specific watermark templates
  - Export final MP4s
  - Serve generated files via download endpoint

===================================================
# 2. DIRECTORY ARCHITECTURE (DO NOT RESTRUCTURE)
===================================================
The directory layout is intentional and stable:

portal/
 ├── app.py                # Flask app entrypoint
 ├── video_processor.py    # FFmpeg processing logic
 ├── brand_loader.py       # Loads brand configs & templates
 ├── brand_config.json     # Brand definitions
 ├── config.py             # Path configuration
 ├── gunicorn.conf.py      # Deploy config
 ├── uploads/              # Raw downloads
 ├── temp/                 # Intermediate files
 ├── outputs/              # Final MP4 outputs (single flat directory)
 ├── static/               # Frontend assets
 └── templates/            # Dashboard UI

Rules:
- DO NOT add brand subdirectories inside outputs/.
- DO NOT move uploads/temp/outputs.
- DO NOT rename app.py or video_processor.py.
- DO NOT restructure static/ or templates/.

===================================================
# 3. DEPENDENCIES — REQUIRED LIBRARIES
===================================================
- Flask: Web API framework
- yt-dlp: Downloading videos from supported platforms
- FFmpeg: Processing and watermarking videos
- Gunicorn: Render deployment
- Standard Python libs (logging, os, subprocess, etc.)

yt-dlp must always be configured with:
format: "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
merge_output_format: "mp4"

===================================================
# 4. CONFIGURATION FILES
===================================================
- render.yaml must always call Gunicorn with:
  gunicorn -c portal/gunicorn.conf.py portal.app:app

- Procfile must remain consistent with the above.

- No environment variable changes unless explicitly requested.

===================================================
# 5. PIPELINE RULES (CRITICAL)
===================================================
The pipeline must ALWAYS follow this sequence:

1. Download → portal/uploads/
2. Process with FFmpeg → portal/temp/
3. Export MP4 → portal/outputs/
4. Serve via /api/videos/download/<filename>

Rules:
- No dynamic path guessing.
- No brand subfolder scanning.
- No changing where outputs go.
- Final outputs MUST be MP4 with merged audio+video.

===================================================
# 6. FFmpeg CONFIGURATION RULES
===================================================
Defaults when on Render Pro:
-threads 4
-filter_threads 2
-bufsize 256M
-preset fast

ALWAYS include:
-map "[vout]"
-map "0:a?"

Video must NEVER be exported audio-only unless source has no video.

===================================================
# 7. BACKING SERVICES
===================================================
- File storage: Render ephemeral filesystem (temporary per deployment)
- No external DB required
- Logs via Gunicorn + print-safe debugging

===================================================
# 8. FRONTEND RULES
===================================================
dashboard.js or similar files MUST:
- Treat download responses as binary blobs
- NEVER call .json() on download endpoints
- Always trigger file downloads using blob → objectURL → <a download>

===================================================
# 9. DEPLOYMENT RULES
===================================================
Render auto-deploy MUST:
- Detect pushes to main branch
- Build using render.yaml
- Run using gunicorn.conf.py

NEVER break autodetection by modifying:
- render.yaml structure
- Procfile
- start command formatting

===================================================
# 10. STABILITY RULES — WHAT QODER MUST NOT TOUCH
===================================================
Unless explicitly instructed:
- Do NOT change output directory structure.
- Do NOT modify brand_config.json schema.
- Do NOT rewrite video_processor.py architecture.
- Do NOT add/remove FFmpeg filters beyond requested.
- Do NOT change how yt-dlp stores temporary files.
- Do NOT introduce async frameworks.
- Do NOT migrate to another web framework.
- Do NOT introduce concurrency that risks Render worker crashes.

===================================================
# 11. LOGGING RULES
===================================================
Use Python logging or print-safe debug. Avoid noisy logs.

===================================================
# 12. ALWAYS OUTPUT A DIFF SUMMARY
===================================================
For every modification, Qoder must output:
- Files changed
- Lines added/removed
- Explanation of why

===================================================
END OF AGENTS.md
===================================================