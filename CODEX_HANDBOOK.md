# CODEX HANDBOOK — Brandr Brand Wizard (Complete Operations Guide)

## 📋 Overview
This document is for Codex (or any AI/developer) taking over work on the **Brandr Brand Creation Wizard** in the WatchTheFall Portal codebase.

The wizard was recently refactored from a 4-step flow to a **2-step simplified flow**. Codex should understand the current architecture before making changes.

---

## 📁 Key Files

### Primary Files to Work With
```
portal/templates/brands.html          ← Brand creation/editing UI (wizard)
portal/app.py                         ← Backend API routes (brands, uploads)
portal/static/css/dashboard.css       ← Dashboard styles (dark premium theme)
portal/static/js/brands.js            ← Brand listing/management JS
```

### Supporting Files
```
portal/static/watermarks/             ← Watermark PNG files
portal/image_utils.py                 ← Image processing (background removal, shapes)
portal/private/db/wtf_studio.db       ← SQLite database (brands, watermarks, users)
```

### Configuration & Deployment Files (READ-ONLY)
```
render.yaml                           ← Render deployment config
Procfile                              ← Gunicorn startup command
portal/gunicorn.conf.py               ← Gunicorn worker settings
requirements.txt                      ← Python dependencies
.gitignore                            ← Git ignore rules
runtime.txt                           ← Python version (3.14)
render-build.sh                       ← Build script (FFmpeg installation)
```

---

## 🧭 Architecture: 2-Step Brand Wizard

### Step 1: Brand Basics
- **Display Name** input → auto-generates slug
- **Upload Image** → preview thumbnail
- **Background Removal** toggle
- **Continue** button → Step 2

### Step 2: Live Composer
- **Canvas** with draggable positioned brand/watermark
- **Controls Panel**:
  - Scale slider (5% - 100%)
  - Opacity slider (0% - 100%)
  - Shape buttons (Original / Circle / Square)
  - Position reset button
- **Advanced Options** (collapsed by default):
  - **Use separate watermark** toggle
  - Watermark upload zone (if enabled)
  - Help text: "If left empty, Brandr uses your main brand image"
  - **Preview mode** selector: Main Brand / Watermark / Both
- **Save** button → creates/updates brand

### State Management
```javascript
let wizardState = {
    step: 1,
    displayName: '',
    slug: '',
    logoFile: null,
    logoPreviewUrl: null,
    logoShape: 'original',
    logoScale: 25,
    logoOpacity: 100,
    logoRotation: 0,
    logoX: 0.5,  // 0.0 - 1.0 relative to canvas
    logoY: 0.5,  // 0.0 - 1.0 relative to canvas
    bgRemovalEnabled: false,
    useSeparateWatermark: false,
    watermarkFile: null,
    watermarkPreviewUrl: null,
    previewMode: 'logo'  // 'logo' | 'watermark' | 'both'
};
```

---

## 🔗 Backend API Endpoints

### Create Brand
```
POST /api/brands
Body: { display_name: string, slug: string }
Response: { id, display_name, slug, ... }
```

### Upload Logo
```
POST /api/brands/{brandId}/upload_logo
Form: { file: image, bg_removal: boolean }
Response: { logo_path }
```

### Update Brand
```
PUT /api/brands/{brandId}
Body: {
    logo_scale: number,
    logo_opacity: number,
    logo_rotation: number,
    logo_position_x: number,  // 0.0 - 1.0
    logo_position_y: number,  // 0.0 - 1.0
    logo_shape: 'original' | 'circle' | 'square',
    logo_path: string
}
Response: { ...brand data }
```

### Upload Watermark (Optional)
```
POST /api/brands/{brandId}/upload_watermark
Form: { file: image }
Response: { watermark_path }
```

---

## 🎨 Canvas & Drag System

### Canvas Drawing
- Canvas is responsive (scales with container)
- Draws uploaded image centered at `logoX`, `logoY`
- Applies scale, opacity, rotation, and shape
- **Preview modes**: 
  - `'logo'` → draws only main brand image
  - `'watermark'` → draws only watermark (if separate enabled)
  - `'both'` → draws both (watermark at bottom-right)

### Drag Positioning
```javascript
function handleCanvasMouseDown(e) { ... }  // Start drag
function handleCanvasMouseMove(e) { ... }   // Update position
function handleCanvasMouseUp() { ... }      // End drag
```
- Position stored as relative coordinates (0.0 - 1.0)
- Converted to pixels for canvas drawing
- Updated on drag end

### Shape Application
- Original: draw image as-is
- Circle: clip to circle using `ctx.arc()`
- Square: clip to square using `ctx.rect()`

---

## 💾 Save Flow

### Create New Brand
1. User clicks "Continue" in Step 1
2. POST `/api/brands` → creates brand record
3. POST `/api/brands/{id}/upload_logo` → uploads main image
4. If `useSeparateWatermark` enabled:
   - POST `/api/brands/{id}/upload_watermark` → uploads watermark
5. PUT `/api/brands/{id}` → saves composer settings
6. Redirect to `/brands` list

### Edit Existing Brand
1. Load brand data into `wizardState`
2. User makes changes in composer
3. PUT `/api/brands/{id}` → updates settings
4. If new watermark uploaded, upload it first
5. Redirect to `/brands` list

---

## ⚠️ Critical Rules for Codex

### DO NOT
- Reintroduce the old 4-step wizard
- Remove the "Use separate watermark" toggle
- Change the backend field naming (`logo_*` vs `wm_*`)
- Modify the database schema without approval
- Touch `portal/app.py` or `portal/video_processor.py` without explicit permission
- Change the dark premium UI theme
- Add new steps to the wizard

### DO
- Keep the 2-step flow intact
- Preserve all existing drag, scale, opacity, shape functionality
- Maintain the "Advanced Options" collapsible section
- Keep watermark upload optional
- Use `logoX`/`logoY` as relative coordinates (0.0 - 1.0)
- Test drag functionality after any canvas changes
- Verify save flow works end-to-end

---

## 🧪 Testing Checklist

After making changes, verify:
- [ ] Step 1: Name input works, slug auto-generates
- [ ] Step 1: Image upload shows preview
- [ ] Step 1: Background removal toggle works
- [ ] Step 2: Canvas displays uploaded image
- [ ] Step 2: Drag positioning works
- [ ] Step 2: Scale slider updates image size
- [ ] Step 2: Opacity slider updates transparency
- [ ] Step 2: Shape buttons change image shape
- [ ] Step 2: "Reset Position" works
- [ ] Step 2: Advanced Options expand/collapse
- [ ] Step 2: "Use separate watermark" toggle shows upload zone
- [ ] Step 2: Watermark upload works
- [ ] Step 2: Preview mode switches (logo/watermark/both)
- [ ] Save creates brand successfully
- [ ] Save uploads logo (and watermark if enabled)
- [ ] Redirect to brands list works
- [ ] Edit brand loads data correctly
- [ ] Database records are saved properly

---

## 🐛 Common Issues & Solutions

### Canvas Not Drawing Image
- Check `wizardLogoImage.onload` callback
- Verify image URL is valid
- Check canvas dimensions are set

### Drag Not Working
- Ensure `handleCanvasMouseDown/Move/Up` are attached
- Check canvas offset calculation
- Verify relative coordinate conversion

### Save Fails
- Check API endpoint URLs
- Verify form data format (JSON vs FormData)
- Check backend validation errors
- Ensure brand ID exists before upload endpoints

### Watermark Not Showing
- Check `useSeparateWatermark` state
- Verify `watermarkFile` is set
- Check `previewMode` value
- Verify watermark upload endpoint works

---

## 📝 Style Guidelines

### UI Theme
- Dark charcoal background: `#0f0f0f`, `#1a1a1a`, `#2a2a2a`
- Neon green accent: `#39ff14`
- Clean white text: `#ffffff`
- Subtle borders: `#333333`
- Modern sans-serif fonts
- Smooth transitions (0.2s ease)

### Layout
- 3-column composer layout (controls | canvas | preview)
- Responsive design (mobile-first)
- Collapsible sections
- Clean form controls with labels

---

## 🚀 Quick Start for Codex

1. **Read this document** thoroughly
2. **Open `brands.html`** and scan the structure
3. **Identify the task** you're assigned
4. **Check affected files** before editing
5. **Make changes** following the rules above
6. **Test thoroughly** using the checklist
7. **Report changes** in the format:
   ```
   Files changed: [...]
   What changed: [...]
   Why: [...]
   Risks: [...]
   Test status: [...]
   ```

---

## 🚀 Deployment to Render (CRITICAL)

### How to Deploy
The project uses **Render** for hosting with **auto-deploy from GitHub**. Here's the complete flow:

#### 1. Git Workflow
```bash
# Make your changes locally
git add .
git commit -m "Describe your changes"
git push origin main
```

**That's it.** Render automatically detects pushes to `main` branch and triggers a deployment.

#### 2. Render Auto-Deploy Process
When you push to `main`:
1. Render detects the push via webhook
2. Runs `buildCommand` from `render.yaml`:
   ```bash
   pip install -r requirements.txt
   ```
3. Runs `startCommand` from `render.yaml`:
   ```bash
   gunicorn -c portal/gunicorn.conf.py portal.app:app
   ```
4. Service restarts with new code
5. Old version is replaced (zero-downtime if successful)

#### 3. Render Configuration (render.yaml)
```yaml
services:
  - type: web
    name: watchthefall-orchestrator-v3
    env: python
    plan: free
    buildCommand: "pip install -r requirements.txt"
    startCommand: "gunicorn -c portal/gunicorn.conf.py portal.app:app"
    envVars:
      - key: PYTHON_VERSION
        value: 3.10.6
      - key: RENDER
        value: "true"
      - key: ENV
        value: production
    headers:
      - type: global
        forward:
          - Cf-Access-Jwt-Assertion
```

**CRITICAL RULES:**
- ✅ **NEVER** modify `render.yaml` structure without explicit approval
- ✅ **NEVER** change the `startCommand` format
- ✅ **NEVER** modify `Procfile` (must match render.yaml)
- ✅ **NEVER** change deployment environment variables without approval

#### 4. Gunicorn Configuration (portal/gunicorn.conf.py)
```python
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = 1
timeout = 300
graceful_timeout = 300
keepalive = 5
worker_class = "sync"
loglevel = "info"
preload_app = True
worker_connections = 1000
```

**Key Settings:**
- `workers = 1`: Single worker to stay within Render Free tier RAM (512MB)
- `timeout = 300`: 5-minute timeout for FFmpeg video processing
- `preload_app = True`: Load app code before forking (faster restarts)

#### 5. Environment Variables (Set in Render Dashboard)
```
WTF_PORTAL_KEY=<your-secret-auth-key>
WTF_SECRET_KEY=<flask-session-secret>
FFMPEG_PATH=/usr/bin/ffmpeg
DB_PATH=/var/data/wtf_studio.db          # Persistent disk
STORAGE_ROOT=/var/data/storage           # Persistent disk
RENDER=true
ENV=production
PYTHON_VERSION=3.10.6
```

**⚠️ PERSISTENT DISK IS CRITICAL:**
- Mount path: `/var/data`
- Without this, ALL DATA IS LOST on each deploy (ephemeral filesystem)
- Ensures:
  - ✅ User accounts survive deploys
  - ✅ Downloaded videos persist
  - ✅ Branded outputs persist
  - ✅ User brand assets persist
  - ✅ SQLite database persists

#### 6. Deployment Checklist
Before pushing to production:
- [ ] Code tested locally
- [ ] All wizard flows work (create, edit, save)
- [ ] No console errors in browser
- [ ] Database migrations applied (if any)
- [ ] Environment variables set in Render dashboard
- [ ] Persistent disk attached and mounted
- [ ] Git committed and pushed to `main`

#### 7. Monitoring Deployment
After pushing:
1. Go to Render dashboard: https://dashboard.render.com/
2. Select your service: `watchthefall-orchestrator-v3`
3. Click "Logs" tab to watch deployment progress
4. Look for:
   - `Build successful`
   - `Service deployed`
   - No error messages

**Common Deployment Issues:**
- ❌ `Build failed` → Check `requirements.txt` syntax
- ❌ `Timeout` → FFmpeg not installed (should be in render-build.sh)
- ❌ `502 Bad Gateway` → Gunicorn not starting, check logs
- ❌ `Database errors` → Persistent disk not mounted

---

## 💾 Database Management

### Database Location
- **Local**: `portal/private/db/wtf_studio.db`
- **Production**: `/var/data/wtf_studio.db` (persistent disk)

### Database Schema
Key tables:
- `users` - User accounts, tier levels
- `brands` - Brand configurations (logo, watermark, settings)
- `watermarks` - Watermark presets
- `jobs` - Video processing jobs
- `events` - Activity logs

### Working with the Database
```python
# In app.py or scripts
import sqlite3
db_path = os.environ.get('DB_PATH', 'portal/private/db/wtf_studio.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Example: Get all brands
cursor.execute("SELECT * FROM brands WHERE user_id = ?", (user_id,))
brands = cursor.fetchall()

# Example: Update brand
cursor.execute(
    "UPDATE brands SET logo_scale = ?, logo_opacity = ? WHERE id = ?",
    (scale, opacity, brand_id)
)
conn.commit()
```

**⚠️ DATABASE RULES:**
- NEVER modify schema without explicit approval
- ALWAYS use parameterized queries (prevent SQL injection)
- ALWAYS commit transactions
- NEVER delete production data without backup
- Test migrations locally first

---

## 🧪 Local Development Setup

### Prerequisites
- Python 3.10+ (or 3.14)
- FFmpeg installed and in PATH
- Git

### Quick Setup (Windows PowerShell)
```powershell
# Run setup script
.\setup_local.ps1
```

### Manual Setup
```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate (Windows PowerShell)
venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create required directories
mkdir portal\uploads
mkdir portal\outputs
mkdir portal\temp
mkdir portal\logs
mkdir portal\db

# 5. Run the app
python run_portal.py
```

### Access Local App
- Dashboard: http://localhost:5000/portal/
- Test endpoint: http://localhost:5000/portal/test

### Local Testing
```bash
# Test the portal is running
curl http://localhost:5000/portal/test

# Expected response:
# {"status": "online", "message": "WatchTheFall Portal is running", ...}
```

---

## 🔧 Debugging & Troubleshooting

### Check Render Logs
```
Dashboard → Service → Logs tab
```
Look for:
- Python tracebacks
- Database connection errors
- FFmpeg processing failures
- Gunicorn worker crashes

### Common Issues & Solutions

#### Canvas Not Drawing Image
- Check `wizardLogoImage.onload` callback
- Verify image URL is valid
- Check canvas dimensions are set

#### Drag Not Working
- Ensure `handleCanvasMouseDown/Move/Up` are attached
- Check canvas offset calculation
- Verify relative coordinate conversion

#### Save Fails
- Check API endpoint URLs
- Verify form data format (JSON vs FormData)
- Check backend validation errors
- Ensure brand ID exists before upload endpoints

#### Watermark Not Showing
- Check `useSeparateWatermark` state
- Verify `watermarkFile` is set
- Check `previewMode` value
- Verify watermark upload endpoint works

#### Deployment Fails
- Check Render logs for build errors
- Verify `requirements.txt` syntax
- Ensure `render.yaml` is valid YAML
- Check persistent disk is mounted

#### Database Locked
- Only one process can write to SQLite
- Close other connections
- Check for unclosed transactions
- Restart Gunicorn if needed

---

## 📝 Style Guidelines

### UI Theme
- Dark charcoal background: `#0f0f0f`, `#1a1a1a`, `#2a2a2a`
- Neon green accent: `#39ff14`
- Clean white text: `#ffffff`
- Subtle borders: `#333333`
- Modern sans-serif fonts
- Smooth transitions (0.2s ease)

### Layout
- 3-column composer layout (controls | canvas | preview)
- Responsive design (mobile-first)
- Collapsible sections
- Clean form controls with labels

---

## 🚀 Quick Start for Codex

1. **Read this document** thoroughly
2. **Open `brands.html`** and scan the structure
3. **Identify the task** you're assigned
4. **Check affected files** before editing
5. **Make changes** following the rules above
6. **Test locally** using setup instructions
7. **Push to `main`** to deploy to Render
8. **Monitor logs** in Render dashboard
9. **Report changes** in the format:
   ```
   Files changed: [...]
   What changed: [...]
   Why: [...]
   Risks: [...]
   Test status: [...]
   ```

---

## 📞 When in Doubt

- **Ask for clarification** instead of guessing
- **Preserve existing functionality** over new features
- **Document your changes** clearly
- **Stop after requested work** (don't continue into next phases)
- **Reference `AGENTS.md`** for additional rules

---

## 🔐 Security Notes

### Sensitive Files (NEVER Commit)
```
cookies.txt
cookies/cookies.txt
portal/data/cookies.txt
.env
*.log
__pycache__/
*.pyc
```

### Authentication
- Current: Shared key (`WTF_PORTAL_KEY`)
- Future: JWT authentication (stubs in place)
- Cloudflare Access: JWT assertion forwarded via headers

### Production Checklist
- [ ] Change `WTF_PORTAL_KEY` from default
- [ ] Set `WTF_SECRET_KEY` for Flask sessions
- [ ] Configure max upload size in web server
- [ ] Set up log rotation for `portal/logs/`
- [ ] Configure automatic temp file cleanup
- [ ] Test FFmpeg is accessible
- [ ] Verify write permissions on upload/output directories
- [ ] Set up SSL/HTTPS (Render handles this)
- [ ] Configure firewall rules
- [ ] Set up monitoring/alerts

---

*Last updated: 2026-04-11*
*Wizard version: 2-step with optional watermark*
*Deployment: Render (auto-deploy from main branch)*
*Contact: Refer to project AGENTS.md for escalation*
