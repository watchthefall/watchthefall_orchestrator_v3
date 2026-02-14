"""
WatchTheFall Portal - Flask Application
"""
from flask import Flask, request, jsonify, render_template, send_from_directory
import os
import json
import uuid
import time
from werkzeug.utils import secure_filename
import subprocess
import tempfile
import threading
from yt_dlp import YoutubeDL

def ensure_video_stream(path):
    import subprocess, json, os

    probe_cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", path
    ]

    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    try:
        info = json.loads(result.stdout)
        video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        return len(video_streams) > 0
    except:
        return False

# Import video processing utilities
from .video_processor import VideoProcessor, normalize_video
from .brand_loader import get_available_brands

# Import configuration
from .config import (
    SECRET_KEY, PORTAL_AUTH_KEY, OUTPUT_DIR,
    MAX_UPLOAD_SIZE, BRANDS_DIR
)
from .database import log_event


app = Flask(__name__, 
            template_folder='templates',
            static_folder='static',
            static_url_path='/static')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

# Initialize database
from .database import init_db
init_db()

# Debug endpoint to verify app is loading routes correctly
@app.route("/__debug_alive")
def debug_alive():
    return "alive", 200

@app.route("/__debug_routes")
def debug_routes():
    """List all registered routes"""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods),
            'rule': str(rule)
        })
    return jsonify({
        'status': 'ok',
        'routes': routes
    })

@app.route("/__debug_env")
def debug_env():
    """Show environment variables"""
    import os
    env_vars = {}
    for key in os.environ:
        # Hide sensitive values
        if any(sensitive in key.lower() for sensitive in ['key', 'secret', 'password', 'token']):
            env_vars[key] = '***HIDDEN***'
        else:
            env_vars[key] = os.environ[key]
    
    return jsonify({
        'status': 'ok',
        'environment': env_vars
    })

@app.route("/__debug_ffmpeg")
def debug_ffmpeg():
    """Show FFmpeg configuration"""
    from .config import FFMPEG_BIN, FFPROBE_BIN
    import subprocess
    import os
    
    ffmpeg_info = {
        'ffmpeg_bin': FFMPEG_BIN,
        'ffprobe_bin': FFPROBE_BIN,
        'ffmpeg_exists': os.path.exists(FFMPEG_BIN) or subprocess.run(['which', FFMPEG_BIN], capture_output=True).returncode == 0 if os.name != 'nt' else True,
        'ffprobe_exists': os.path.exists(FFPROBE_BIN) or subprocess.run(['which', FFPROBE_BIN], capture_output=True).returncode == 0 if os.name != 'nt' else True
    }
    
    # Try to get FFmpeg version
    try:
        result = subprocess.run([FFMPEG_BIN, '-version'], capture_output=True, text=True, timeout=5)
        ffmpeg_info['version'] = result.stdout.split('\n')[0] if result.stdout else 'Unknown'
    except Exception as e:
        ffmpeg_info['version_error'] = str(e)
    
    return jsonify({
        'status': 'ok',
        'ffmpeg': ffmpeg_info
    })

@app.route("/__debug_storage")
def debug_storage():
    """Show storage information"""
    import os
    from .config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, LOG_DIR, DB_PATH, BRANDS_DIR
    
    def get_dir_info(path):
        try:
            if os.path.exists(path):
                size = sum(os.path.getsize(os.path.join(dirpath, filename)) 
                          for dirpath, dirnames, filenames in os.walk(path) 
                          for filename in filenames)
                count = sum(len(files) for _, _, files in os.walk(path))
                return {
                    'exists': True,
                    'size_bytes': size,
                    'file_count': count,
                    'writable': os.access(path, os.W_OK)
                }
            else:
                return {
                    'exists': False,
                    'size_bytes': 0,
                    'file_count': 0,
                    'writable': False
                }
        except Exception as e:
            return {
                'exists': os.path.exists(path),
                'error': str(e)
            }
    
    storage_info = {
        'upload_dir': get_dir_info(UPLOAD_DIR),
        'output_dir': get_dir_info(OUTPUT_DIR),
        'temp_dir': get_dir_info(TEMP_DIR),
        'log_dir': get_dir_info(LOG_DIR),
        'db_path': get_dir_info(os.path.dirname(DB_PATH)),
        'brands_dir': get_dir_info(BRANDS_DIR)
    }
    
    return jsonify({
        'status': 'ok',
        'storage': storage_info
    })

@app.route("/__debug_brands")
def debug_brands():
    """Show brand information"""
    from .brand_loader import get_available_brands
    import os
    
    portal_dir = os.path.dirname(os.path.abspath(__file__))
    brands = get_available_brands(portal_dir)
    
    brand_info = []
    for brand in brands:
        brand_info.append({
            'name': brand.get('name'),
            'display_name': brand.get('display_name'),
            'assets': brand.get('assets', {}),
            'options': brand.get('options', {})
        })
    
    return jsonify({
        'status': 'ok',
        'brands': brand_info,
        'count': len(brand_info)
    })

@app.route("/__debug_health")
def debug_health():
    """Health check endpoint"""
    import os
    from .config import UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR
    
    # Check if essential directories are writable
    dirs = [UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR]
    health_checks = {}
    
    for directory in dirs:
        try:
            test_file = os.path.join(directory, '.health_check')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            health_checks[directory] = 'OK'
        except Exception as e:
            health_checks[directory] = f'ERROR: {str(e)}'
    
    # Overall health
    all_healthy = all(status == 'OK' for status in health_checks.values())
    
    return jsonify({
        'status': 'healthy' if all_healthy else 'unhealthy',
        'checks': health_checks
    })

# Global conversion lock - only one FFmpeg process at a time (Render free tier 512MB RAM)
conversion_lock = threading.Lock()
conversion_in_progress = {'active': False, 'start_time': None}

# Job status dictionary for async watermark conversions
watermark_jobs = {}

# ============================================================================

@app.route('/')
def index():
    return jsonify({
        "message": "WTF Portal running",
        "status": "ok",
        "api_endpoints": [
            "POST /api/videos/process_brands",
            "POST /api/videos/fetch",
            "GET /api/videos/download/<filename>",
            "GET /api/brands/list",
            "POST /api/videos/convert-watermark",
            "GET /api/videos/convert-status/<job_id>",
            "GET /api/debug/brand-integrity",
            "GET /api/debug/build-filter/<brand_name>"
        ]
    })

@app.route('/api')
def api_root():
    """API root endpoint listing available API routes"""
    return jsonify({
        "status": "ok",
        "message": "WatchTheFall Portal API",
        "endpoints": [
            {
                "route": "/api/videos/process_brands",
                "method": "POST",
                "description": "Process video with selected brands"
            },
            {
                "route": "/api/videos/fetch",
                "method": "POST",
                "description": "Fetch video from URL"
            },
            {
                "route": "/api/videos/download/<filename>",
                "method": "GET",
                "description": "Download processed video"
            },
            {
                "route": "/api/brands/list",
                "method": "GET",
                "description": "List available brands"
            },
            {
                "route": "/api/videos/convert-watermark",
                "method": "POST",
                "description": "Convert video with watermark"
            },
            {
                "route": "/api/videos/convert-status/<job_id>",
                "method": "GET",
                "description": "Get conversion status"
            },
            {
                "route": "/api/debug/brand-integrity",
                "method": "GET",
                "description": "Check brand asset integrity"
            },
            {
                "route": "/api/debug/build-filter/<brand_name>",
                "method": "GET",
                "description": "Dry-run FFmpeg filter generation"
            }
        ]
    })

# ============================================================================
# FRONTEND ROUTES
# ============================================================================

@app.route('/portal/')
def dashboard():
    """Main portal dashboard"""
    return render_template('clean_dashboard.html')

@app.route("/portal/downloader_dashboard")
def downloader_dashboard():
    return render_template("downloader_dashboard.html")

@app.route("/portal")
def portal_home():
    return dashboard()  # Use new canvas UI

@app.route('/portal/test')
def test_page():
    """Test page to verify portal is online"""
    return jsonify({
        'status': 'online',
        'message': 'WatchTheFall Portal is running',
        'endpoints': [
            '/portal/',
            '/api/videos/fetch',
            '/api/videos/download/<filename>',
            '/api/videos/convert-watermark',
            '/api/videos/convert-status/<job_id>',

        ]
    })

# ============================================================================
# API: VIDEO PROCESSING
# ============================================================================

@app.route('/api/videos/process_brands', methods=['POST'])
def process_branded_videos():
    """Process video with selected brand overlays"""
    try:
        data = request.get_json(force=True) or {}
        url = data.get('url')
        selected_brands = data.get('brands', [])
        
        # Optional branding configuration overrides
        watermark_scale = data.get('watermark_scale', 1.15)
        watermark_opacity = data.get('watermark_opacity', 0.4)
        logo_scale = data.get('logo_scale', 0.15)
        logo_padding = data.get('logo_padding', 40)

        # NEW: accept source_path for local downloaded files
        source_path = data.get("source_path")
        if source_path and not url:
            url = source_path

        print(f"[PROCESS BRANDS] ========== NEW REQUEST ==========")
        print(f"[PROCESS BRANDS] Raw request data: {data}")
        print(f"[PROCESS BRANDS] URL: {url}")
        print(f"[PROCESS BRANDS] Selected brands: {selected_brands}")
        print(f"[PROCESS BRANDS] ========================================")

        if not url:
            print(f"[PROCESS BRANDS] ERROR: No URL provided")
            return jsonify({'success': False, 'error': 'URL or source_path is required'}), 400

        if not selected_brands:
            print(f"[PROCESS BRANDS] ERROR: No brands selected")
            return jsonify({'success': False, 'error': 'At least one brand must be selected'}), 400
        
        print(f"[PROCESS BRANDS] URL: {url[:50]}...")
        print(f"[PROCESS BRANDS] Selected brands: {selected_brands}")
        
        # Check if url is actually a local file path (doesn't start with http)
        if url.startswith('http'):
            # 1. Download the video from URL
            def download_video(url_input):
                try:
                    # Configure yt_dlp with enhanced fallback options for Instagram
                    ydl_opts = {
                        'outtmpl': os.path.join(OUTPUT_DIR, '%(id)s.%(ext)s'),
                        'merge_output_format': 'mp4',
                        'format': 'bv*+ba/best',  # Better fallback for Instagram
                        'prefer_ffmpeg': True,
                        'retries': 5,
                        'fragment_retries': 5,
                        'socket_timeout': 300,
                        'http_headers': {
                            'User-Agent': 'Instagram 271.1.0.21.84 Android',
                            'X-IG-App-ID': '567067343352427',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.5',
                            'Accept-Encoding': 'gzip, deflate',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        }
                    }
                    
                    # Only add cookiefile if the file exists and is readable
                    cookie_file = './portal/data/cookies.txt'
                    try:
                        if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
                            # Test if file is readable and has valid content
                            with open(cookie_file, 'r', encoding='utf-8') as f:
                                content = f.read().strip()
                                # Check if file has actual cookie data (not just comments)
                                # File is valid if it has content and either:
                                # 1. Doesn't start with the header (unlikely but possible), OR
                                # 2. Has more than one line (indicating actual cookie data beyond header)
                                # Additional check: look for actual cookie data patterns
                                has_cookie_data = False
                                if content:
                                    lines = content.split('\n')
                                    # Check if we have more than just header lines
                                    # Look for lines that contain actual cookie data (domain, flag, path, etc.)
                                    for line in lines:
                                        line = line.strip()
                                        # Skip empty lines and comments
                                        if line and not line.startswith('#'):
                                            # Check if this looks like a cookie line (has tab-separated values)
                                            if '\t' in line:
                                                has_cookie_data = True
                                                break
                            
                            if has_cookie_data:
                                ydl_opts['cookiefile'] = cookie_file
                                print(f"[PROCESS BRANDS] Using cookie file: {cookie_file}")
                            else:
                                print(f"[PROCESS BRANDS] Cookie file exists but appears to be empty or only contains header: {cookie_file}")
                        else:
                            print(f"[PROCESS BRANDS] Cookie file not found or not readable: {cookie_file}")
                    except Exception as cookie_error:
                        print(f"[PROCESS BRANDS] Warning: Could not use cookie file {cookie_file}: {cookie_error}")
                        # Continue without cookies
                    
                    with YoutubeDL(ydl_opts) as ydl:
                        print(f"[PROCESS BRANDS] Downloading: {url_input[:50]}...")
                        try:
                            info = ydl.extract_info(url_input, download=True)
                            filename = ydl.prepare_filename(info)
                        except Exception as download_error:
                            print(f"[PROCESS BRANDS ERROR] Download failed for {url_input}: {str(download_error)}")
                            import traceback
                            traceback.print_exc()
                            return {
                                'error': str(download_error),
                                'success': False,
                                'details': traceback.format_exc()
                            }
                    
                    # Ensure .mp4 extension
                    if not filename.endswith('.mp4'):
                        base, _ = os.path.splitext(filename)
                        filename = base + '.mp4'
                    
                    name = os.path.basename(filename)
                    file_exists = os.path.exists(filename)
                    file_size_mb = os.path.getsize(filename) / (1024 * 1024) if file_exists else 0
                    
                    if not file_exists or file_size_mb == 0:
                        print(f"[PROCESS BRANDS WARNING] File may not have downloaded properly: {filename} (exists: {file_exists}, size: {file_size_mb:.2f}MB)")
                        # Check if we have error information in the info dict
                        if info and 'error' in info:
                            print(f"[PROCESS BRANDS ERROR DETAIL] yt-dlp error: {info['error']}")
                        # Also check for other error fields
                        elif info and 'errors' in info:
                            print(f"[PROCESS BRANDS ERROR DETAIL] yt-dlp errors: {info['errors']}")
                    
                    print(f"[PROCESS BRANDS] Success: {name} ({file_size_mb:.2f}MB)")
                    return {
                        'filename': name,
                        'filepath': filename,
                        'size_mb': round(file_size_mb, 2),
                        'success': file_exists and file_size_mb > 0
                    }
                except Exception as e:
                    print(f"[PROCESS BRANDS ERROR] {url_input}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    # Try to get more detailed error information
                    error_details = str(e)
                    if hasattr(e, 'msg'):
                        error_details += f"; msg: {e.msg}"
                    if hasattr(e, 'reason'):
                        error_details += f"; reason: {e.reason}"
                    return {
                        'error': error_details,
                        'success': False,
                        'details': traceback.format_exc()
                    }
            # Download the video
            download_result = download_video(url)
            if not download_result.get('success'):
                return jsonify({
                    'success': False,
                    'error': 'Failed to download video',
                    'details': download_result.get('error')
                }), 500
            
            video_filepath = download_result['filepath']
            video_id = os.path.splitext(download_result['filename'])[0]
        else:
            # URL is actually a local file path
            print(f"[PROCESS BRANDS] Processing local file: {url}")
            # Construct the full path to the file in OUTPUT_DIR
            video_filepath = os.path.join(OUTPUT_DIR, url)
            video_id = os.path.splitext(url)[0]
            
            # Check if file exists
            if not os.path.exists(video_filepath):
                return jsonify({
                    'success': False,
                    'error': f'File not found: {video_filepath}'
                }), 404
            
            print(f"[PROCESS BRANDS] Found local file: {video_filepath}")
        
        # 2. Load brand configurations
        brand_configs = get_available_brands(os.path.dirname(os.path.abspath(__file__)))
        
        # Filter to only selected brands
        selected_brand_configs = [brand for brand in brand_configs if brand['name'] in selected_brands]
        
        if not selected_brand_configs:
            return jsonify({
                'success': False,
                'error': 'No valid brands selected',
                'available_brands': [brand['name'] for brand in brand_configs]
            }), 400
        
        print(f"[PROCESS BRANDS] Processing {len(selected_brand_configs)} brands sequentially")
        
        # Normalize video timestamps to fix corrupted Instagram videos
        print(f"[PROCESS BRANDS] Normalizing video timestamps: {video_filepath}")
        normalized_video_path = normalize_video(video_filepath)
        print(f"[PROCESS BRANDS] Using normalized video: {normalized_video_path}")
        
        # 3. Process video with selected brands ONE AT A TIME
        processor = VideoProcessor(normalized_video_path, OUTPUT_DIR)
        
        # Apply branding configuration overrides from UI
        processor.WATERMARK_SCALE = watermark_scale
        processor.WATERMARK_OPACITY = watermark_opacity
        processor.LOGO_SCALE = logo_scale
        processor.LOGO_PADDING = logo_padding
        print(f"[PROCESS BRANDS] Branding config: scale={watermark_scale}, opacity={watermark_opacity}, logo_scale={logo_scale}, logo_padding={logo_padding}")
        
        output_paths = []
        
        total_brands = len(selected_brand_configs)
        for i, brand_config in enumerate(selected_brand_configs, 1):
            brand_name = brand_config.get('name', 'Unknown')
            print(f"[PROCESS BRANDS] PROCESSING BRAND {i} of {total_brands}: {brand_name}")
            
            try:
                output_path = processor.process_brand(brand_config, video_id=video_id)
                output_paths.append(output_path)
                print(f"[PROCESS BRANDS] FINISHED BRAND {i}: {brand_name}")
            except Exception as e:
                error_message = str(e)
                if "audio-only" in error_message or "no valid video stream" in error_message:
                    # Handle audio-only video error specifically
                    print(f"[PROCESS BRANDS] AUDIO-ONLY VIDEO DETECTED FOR BRAND {i}: {brand_name}")
                    return jsonify({
                        'success': False,
                        'error': 'The downloaded file contains no video stream (audio-only). Instagram served audio-only content. Try again or use a different video.',
                        'details': error_message
                    }), 400
                else:
                    print(f"[PROCESS BRANDS] FAILED BRAND {i}: {brand_name} - {str(e)}")
                    import traceback
                    traceback.print_exc()
        
        print(f"[PROCESS BRANDS] ALL BRANDS COMPLETED: {len(output_paths)} successful")
        
        # 4. Generate download URLs
        download_urls = []
        for output_path in output_paths:
            filename = os.path.basename(output_path)
            # Extract brand name from filename (format: {video_id}_{brand_name}.mp4)
            # Split by underscore and take the last part before .mp4
            name_parts = filename.replace('.mp4', '').split('_')
            brand_name = name_parts[-1] if len(name_parts) > 1 else 'unknown'
            download_urls.append({
                'brand': brand_name,
                'filename': filename,
                'download_url': f'/api/videos/download/{filename}'
            })
        
        # Clean up original video (only if we downloaded it, not if it was local)
        if url.startswith('http'):
            try:
                os.remove(video_filepath)
            except Exception as e:
                print(f"[PROCESS BRANDS] Warning: Could not remove original video: {e}")
        
        print(f"[PROCESS BRANDS] ========== RESPONSE ==========")
        print(f"[PROCESS BRANDS] Success: True")
        print(f"[PROCESS BRANDS] Output count: {len(output_paths)}")
        print(f"[PROCESS BRANDS] Output files:")
        for i, dl in enumerate(download_urls, 1):
            print(f"[PROCESS BRANDS]   {i}. {dl['filename']} (brand: {dl['brand']})")
        print(f"[PROCESS BRANDS] ========================================")
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed video for {len(output_paths)} brands',
            'outputs': download_urls
        })
        
    except Exception as e:
        import traceback
        print(f"[PROCESS BRANDS EXCEPTION]:")
        traceback.print_exc()
        log_event('error', None, f'Brand processing failed: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/videos/fetch', methods=['POST'])
def fetch_videos_from_urls():
    """Download videos from URLs (TikTok, Instagram, X) - up to 5 at a time"""
    try:
        if not YoutubeDL:
            return jsonify({'success': False, 'error': 'yt-dlp not installed'}), 500
        
        data = request.get_json(force=True) or {}
        urls = data.get('urls') or []
        
        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({'success': False, 'error': 'Provide JSON: {"urls": ["url1", "url2", ...]}'}), 400
        
        if len(urls) > 5:
            return jsonify({'success': False, 'error': 'Maximum 5 URLs at a time (Render free tier limit)'}), 400
        
        print(f"[FETCH] Downloading {len(urls)} videos from URLs")
        log_event('info', None, f'Fetching {len(urls)} URLs')
        
        def download_one(url_input):
            try:
                # Configure yt_dlp with enhanced fallback options for Instagram
                ydl_opts = {
                    'outtmpl': os.path.join(OUTPUT_DIR, '%(id)s.%(ext)s'),
                    'merge_output_format': 'mp4',
                    'format': 'bv*+ba/best',  # Better fallback for Instagram
                    'prefer_ffmpeg': True,
                    'retries': 5,
                    'fragment_retries': 5,
                    'socket_timeout': 300,
                    'http_headers': {
                        'User-Agent': 'Instagram 271.1.0.21.84 Android',
                        'X-IG-App-ID': '567067343352427',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Accept-Encoding': 'gzip, deflate',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    }
                }
                
                # Only add cookiefile if the file exists and is readable
                cookie_file = './portal/data/cookies.txt'
                try:
                    if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
                        # Test if file is readable and has valid content
                        with open(cookie_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            # Check if file has actual cookie data (not just comments)
                            # File is valid if it has content and either:
                            # 1. Doesn't start with the header (unlikely but possible), OR
                            # 2. Has more than one line (indicating actual cookie data beyond header)
                            # Additional check: look for actual cookie data patterns
                            has_cookie_data = False
                            if content:
                                lines = content.split('\n')
                                # Check if we have more than just header lines
                                # Look for lines that contain actual cookie data (domain, flag, path, etc.)
                                for line in lines:
                                    line = line.strip()
                                    # Skip empty lines and comments
                                    if line and not line.startswith('#'):
                                        # Check if this looks like a cookie line (has tab-separated values)
                                        if '\t' in line:
                                            has_cookie_data = True
                                            break
                            
                            if has_cookie_data:
                                ydl_opts['cookiefile'] = cookie_file
                                print(f"[FETCH] Using cookie file: {cookie_file}")
                            else:
                                print(f"[FETCH] Cookie file exists but appears to be empty or only contains header: {cookie_file}")
                    else:
                        print(f"[FETCH] Cookie file not found or not readable: {cookie_file}")
                except Exception as cookie_error:
                    print(f"[FETCH] Warning: Could not use cookie file {cookie_file}: {cookie_error}")
                    # Continue without cookies
                
                with YoutubeDL(ydl_opts) as ydl:
                    print(f"[FETCH] Downloading: {url_input[:50]}...")
                    try:
                        info = ydl.extract_info(url_input, download=True)
                        filename = ydl.prepare_filename(info)
                    except Exception as download_error:
                        print(f"[FETCH ERROR] Download failed for {url_input}: {str(download_error)}")
                        import traceback
                        traceback.print_exc()
                        return {
                            'url': url_input,
                            'error': str(download_error),
                            'success': False,
                            'details': traceback.format_exc()
                        }
                
                # Ensure .mp4 extension
                if not filename.endswith('.mp4'):
                    base, _ = os.path.splitext(filename)
                    filename = base + '.mp4'
                
                # Check if downloaded file has valid video stream, if not try fallback
                if not ensure_video_stream(filename):
                    print(f"[FETCH] No valid video stream found in {filename}, attempting fallback extraction...")
                    # fallback extraction using yt-dlp (bundled)
                    fixed_path = filename.replace(".mp4", "_fixed.mp4")
                    ytdlp_cmd = [
                        "yt-dlp",
                        url_input,
                        "-f", "mp4",
                        "-o", fixed_path
                    ]
                    subprocess.run(ytdlp_cmd)
                    if os.path.exists(fixed_path):
                        filename = fixed_path
                        print(f"[FETCH] Fallback extraction successful: {filename}")
                    else:
                        print(f"[FETCH] Fallback extraction failed for {url_input}")
                
                name = os.path.basename(filename)
                file_exists = os.path.exists(filename)
                file_size_mb = os.path.getsize(filename) / (1024 * 1024) if file_exists else 0                
                if not file_exists or file_size_mb == 0:
                    print(f"[FETCH WARNING] File may not have downloaded properly: {filename} (exists: {file_exists}, size: {file_size_mb:.2f}MB)")
                    # Check if we have error information in the info dict
                    if info and 'error' in info:
                        print(f"[FETCH ERROR DETAIL] yt-dlp error: {info['error']}")
                    # Also check for other error fields
                    elif info and 'errors' in info:
                        print(f"[FETCH ERROR DETAIL] yt-dlp errors: {info['errors']}")
                
                print(f"[FETCH] Success: {name} ({file_size_mb:.2f}MB)")
                return {
                    'url': url_input,
                    'filename': name,
                    'local_path': filename,  # Return full path
                    'download_url': f'/api/videos/download/{name}',
                    'size_mb': round(file_size_mb, 2),
                    'success': file_exists and file_size_mb > 0
                }
            except Exception as e:
                print(f"[FETCH ERROR] {url_input}: {str(e)}")
                import traceback
                traceback.print_exc()
            # Try to get more detailed error information
            error_details = str(e)
            if hasattr(e, 'msg'):
                error_details += f"; msg: {e.msg}"
            if hasattr(e, 'reason'):
                error_details += f"; reason: {e.reason}"
            return {
                'url': url_input,
                'error': error_details,
                'success': False,
                'details': traceback.format_exc()
            }
        
        # Download sequentially to keep memory low
        results = []
        for url in urls:
            results.append(download_one(url))
        
        success_count = sum(1 for r in results if r.get('success'))
        log_event('info', None, f'Fetch complete: {success_count}/{len(urls)} successful')
        
        return jsonify({
            'success': True,
            'total': len(urls),
            'successful': success_count,
            'results': results
        })
        
    except Exception as e:
        import traceback
        print(f"[FETCH EXCEPTION]:")
        traceback.print_exc()
        log_event('error', None, f'Fetch failed: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500

# Process endpoint removed - using client-side Canvas watermarking only

# Status endpoint removed - no server-side job queue

@app.route('/api/videos/download/<filename>', methods=['GET'])
def download_video(filename):
    """Download processed video"""
    try:
        # Look for the file in the main output directory only
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        print(f"[DEBUG] Looking for file at: {filepath}")
        
        # Check if file exists
        if not os.path.exists(filepath):
            print(f"[DOWNLOAD ERROR] File not found: {filepath}")
            # List contents of output directory for debugging
            try:
                if os.path.exists(OUTPUT_DIR):
                    output_contents = os.listdir(OUTPUT_DIR)
                    print(f"[DOWNLOAD DEBUG] Contents of OUTPUT_DIR ({OUTPUT_DIR}): {output_contents}")
                else:
                    print(f"[DOWNLOAD DEBUG] OUTPUT_DIR does not exist: {OUTPUT_DIR}")
            except Exception as e:
                print(f"[DOWNLOAD DEBUG] Could not list contents of OUTPUT_DIR: {e}")
            return jsonify({'error': 'File not found', 'path': filepath, 'filename': filename}), 404
        
        file_size = os.path.getsize(filepath)
        print(f"[DOWNLOAD] Serving file: {filename} ({file_size} bytes)")
        
        # Send file with proper headers for downloads folder
        response = send_from_directory(os.path.dirname(filepath), filename, as_attachment=True)
        
        # Add Content-Disposition header to suggest Downloads folder
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        # Mobile-friendly headers
        response.headers['Content-Type'] = 'video/mp4'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        print(f"[DOWNLOAD] Headers set for {filename}")
        return response
    except Exception as e:
        print(f"[DOWNLOAD EXCEPTION] {filename}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'File not found', 'details': str(e), 'filename': filename, 'filepath': filepath}), 404

# Recent videos endpoint removed - using localStorage history only

# ============================================================================
# API: PREVIEW - Frame extraction and asset serving for canvas preview
# ============================================================================

@app.route('/api/preview/extract-frame', methods=['POST'])
def extract_frame():
    """Extract first frame from video for canvas preview"""
    import base64
    from .config import FFMPEG_BIN, FFPROBE_BIN
    
    try:
        data = request.get_json(force=True) or {}
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided'}), 400
        
        # Find the video file
        video_path = os.path.join(OUTPUT_DIR, filename)
        if not os.path.exists(video_path):
            return jsonify({'success': False, 'error': f'File not found: {filename}'}), 404
        
        # Get video dimensions with ffprobe
        probe_cmd = [
            FFPROBE_BIN, '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams', video_path
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        probe_data = json.loads(probe_result.stdout)
        
        width, height = 720, 1280  # Default
        for stream in probe_data.get('streams', []):
            if stream.get('codec_type') == 'video':
                width = stream.get('width', 720)
                height = stream.get('height', 1280)
                break
        
        # Extract first frame as JPEG (small, fast)
        temp_frame = os.path.join(tempfile.gettempdir(), f'frame_{uuid.uuid4().hex}.jpg')
        
        extract_cmd = [
            FFMPEG_BIN, '-y',
            '-i', video_path,
            '-vframes', '1',
            '-q:v', '5',  # Quality 2-31 (lower = better, 5 is good balance)
            temp_frame
        ]
        
        subprocess.run(extract_cmd, capture_output=True)
        
        if not os.path.exists(temp_frame):
            return jsonify({'success': False, 'error': 'Failed to extract frame'}), 500
        
        # Read frame and encode as base64
        with open(temp_frame, 'rb') as f:
            frame_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Clean up temp file
        os.remove(temp_frame)
        
        return jsonify({
            'success': True,
            'frame_data': f'data:image/jpeg;base64,{frame_data}',
            'width': width,
            'height': height,
            'aspect_ratio': width / height
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/preview/watermark/<brand_name>')
def get_watermark_preview(brand_name):
    """Serve watermark PNG for canvas preview"""
    from .video_processor import VideoProcessor
    
    # Get orientation from query param (default Vertical_HD)
    orientation = request.args.get('orientation', 'Vertical_HD')
    
    # Clean brand name
    clean_brand = brand_name.replace('WTF', '').strip()
    
    # Build watermark path
    watermark_dir = os.path.join(VideoProcessor.WATERMARKS_DIR, orientation)
    
    patterns = [
        f"{clean_brand}_watermark.png",
        f"{clean_brand.lower()}_watermark.png",
        f"{clean_brand.capitalize()}_watermark.png",
        f"{brand_name}_watermark.png",
    ]
    
    for pattern in patterns:
        path = os.path.join(watermark_dir, pattern)
        if os.path.exists(path):
            return send_from_directory(os.path.dirname(path), os.path.basename(path))
    
    return jsonify({'error': 'Watermark not found', 'brand': brand_name}), 404


@app.route('/api/preview/logo/<brand_name>')
def get_logo_preview(brand_name):
    """Serve logo PNG for canvas preview"""
    from .video_processor import VideoProcessor
    
    logo_filename = f"{brand_name}_logo.png"
    logo_path = os.path.join(VideoProcessor.LOGOS_DIR, logo_filename)
    
    if os.path.exists(logo_path):
        return send_from_directory(os.path.dirname(logo_path), logo_filename)
    
    return jsonify({'error': 'Logo not found', 'brand': brand_name}), 404

# ============================================================================
# API: BRANDS (Static JSON)
# ============================================================================

@app.route('/api/brands/list', methods=['GET'])
def list_brands():
    """Get list of available brands"""
    try:
        brand_configs = get_available_brands(os.path.dirname(os.path.abspath(__file__)))
        brands = [{'name': brand['name'], 'display_name': brand['display_name']} 
                 for brand in brand_configs]
        
        return jsonify({
            'success': True,
            'brands': brands
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ============================================================================
# API: WATERMARK CONVERSION (WebM to MP4)
# ============================================================================

@app.route('/api/videos/convert-watermark', methods=['POST'])
def convert_watermark():
    """Queue a watermark conversion job (async, non-blocking)"""
    try:
        if 'video' not in request.files:
            return jsonify({'error': 'No video file provided', 'reason': 'no_file'}), 400
        
        file = request.files['video']
        
        if file.filename == '':
            return jsonify({'error': 'Empty filename', 'reason': 'empty_filename'}), 400
        
        # Generate job ID
        job_id = uuid.uuid4().hex
        
        # Save WebM file temporarily
        webm_filename = secure_filename(file.filename)
        temp_webm = os.path.join(tempfile.gettempdir(), f"{job_id}_{webm_filename}")
        file.save(temp_webm)
        
        # Generate output MP4 filename
        mp4_filename = webm_filename.replace('.webm', '.mp4')
        if not mp4_filename.endswith('.mp4'):
            mp4_filename = os.path.splitext(mp4_filename)[0] + '.mp4'
        
        output_path = os.path.join(OUTPUT_DIR, mp4_filename)
        
        # Initialize job status
        watermark_jobs[job_id] = {
            'status': 'queued',
            'filename': mp4_filename,
            'webm_path': temp_webm,
            'output_path': output_path,
            'created_at': time.time(),
            'message': 'Waiting for conversion worker...'
        }
        
        print(f"[CONVERT] Job {job_id[:8]} queued: {webm_filename} → {mp4_filename}")
        log_event('info', None, f'Watermark conversion queued: {job_id[:8]} - {webm_filename}')
        
        # Start background conversion thread
        thread = threading.Thread(
            target=_watermark_conversion_worker,
            args=(job_id, temp_webm, output_path, mp4_filename),
            daemon=True
        )
        thread.start()
        
        # Return immediately with job ID
        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': 'queued',
            'filename': mp4_filename,
            'message': 'Conversion job queued. Poll /api/videos/convert-status/<job_id> for progress.'
        })
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[CONVERT QUEUE EXCEPTION]: {error_trace}")
        log_event('error', None, f'Conversion queue error: {str(e)}')
        return jsonify({
            'error': str(e),
            'reason': 'exception',
            'message': f'Failed to queue conversion: {str(e)}'
        }), 500


def _watermark_conversion_worker(job_id, temp_webm, output_path, mp4_filename):
    """Background worker for FFmpeg watermark conversion (runs in separate thread)"""
    try:
        # Update status to processing
        watermark_jobs[job_id]['status'] = 'processing'
        watermark_jobs[job_id]['message'] = 'Converting WebM to MP4...'
        watermark_jobs[job_id]['started_at'] = time.time()
        
        print(f"[CONVERT] Job {job_id[:8]} started: {mp4_filename}")
        
        # FFmpeg command: MAXIMUM SPEED for Render free tier
        # Sacrificing quality for speed to avoid timeouts
        cmd = [
            'ffmpeg',
            '-analyzeduration', '500000',    # Reduced analysis time
            '-probesize', '500000',          # Reduced probe size
            '-i', temp_webm,
            '-map', '0:v:0',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',          # FASTEST preset (was veryfast)
            '-tune', 'fastdecode',           # Optimize for fast decode
            '-threads', '0',                 # Use all available threads (was 1)
            '-crf', '28',                    # Higher = lower quality but MUCH faster (was 23)
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '96k',                   # Lower audio bitrate (was 128k)
            '-ar', '44100',
            '-shortest',
            '-fflags', '+genpts',
            '-movflags', '+faststart',
            '-max_muxing_queue_size', '512', # Reduced queue (was 1024)
            '-y',
            output_path
        ]
        
        # Run FFmpeg conversion (background thread won't block Gunicorn worker)
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300  # 5 minute timeout
        )
        
        # Clean up temp WebM
        try:
            os.remove(temp_webm)
        except:
            pass
        
        if result.returncode != 0:
            stderr_output = result.stderr.decode('utf-8', errors='ignore')
            error_preview = stderr_output[:500] if len(stderr_output) > 500 else stderr_output
            print(f"[CONVERT] Job {job_id[:8]} FAILED (exit {result.returncode}): {error_preview}")
            
            watermark_jobs[job_id]['status'] = 'failed'
            watermark_jobs[job_id]['error'] = 'FFmpeg conversion failed'
            watermark_jobs[job_id]['stderr_preview'] = error_preview
            watermark_jobs[job_id]['exit_code'] = result.returncode
            watermark_jobs[job_id]['message'] = 'Video conversion failed. Try a shorter video.'
            log_event('error', None, f'Conversion {job_id[:8]} failed: {error_preview[:100]}')
            return
        
        # Success
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        elapsed = time.time() - watermark_jobs[job_id]['started_at']
        
        watermark_jobs[job_id]['status'] = 'completed'
        watermark_jobs[job_id]['download_url'] = f'/api/videos/download/{mp4_filename}'
        watermark_jobs[job_id]['size_mb'] = round(file_size_mb, 2)
        watermark_jobs[job_id]['conversion_time'] = round(elapsed, 1)
        watermark_jobs[job_id]['message'] = 'Video converted to MP4 successfully'
        watermark_jobs[job_id]['completed_at'] = time.time()
        
        print(f"[CONVERT] Job {job_id[:8]} SUCCESS: {mp4_filename} ({file_size_mb:.2f}MB) in {elapsed:.1f}s")
        log_event('info', None, f'Conversion {job_id[:8]} complete: {mp4_filename} ({file_size_mb:.2f}MB, {elapsed:.1f}s)')
        
    except subprocess.TimeoutExpired:
        print(f"[CONVERT] Job {job_id[:8]} TIMEOUT (>5min)")
        watermark_jobs[job_id]['status'] = 'failed'
        watermark_jobs[job_id]['error'] = 'Conversion timeout'
        watermark_jobs[job_id]['message'] = 'Video took too long to convert (>5min). Try a shorter video.'
        log_event('error', None, f'Conversion {job_id[:8]} timeout')
        
        # Clean up
        try:
            os.remove(temp_webm)
        except:
            pass
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[CONVERT] Job {job_id[:8]} EXCEPTION: {error_trace}")
        
        watermark_jobs[job_id]['status'] = 'failed'
        watermark_jobs[job_id]['error'] = str(e)
        watermark_jobs[job_id]['message'] = f'Unexpected error: {str(e)}'
        log_event('error', None, f'Conversion {job_id[:8]} exception: {str(e)}')
        
        # Clean up
        try:
            os.remove(temp_webm)
        except:
            pass


@app.route('/api/videos/convert-status/<job_id>', methods=['GET'])
def get_conversion_status(job_id):
    """Poll conversion job status (non-blocking)"""
    if job_id not in watermark_jobs:
        return jsonify({
            'error': 'Job not found',
            'job_id': job_id,
            'message': 'Invalid job ID or job expired.'
        }), 404
    
    job = watermark_jobs[job_id]
    
    # Build response based on status
    response = {
        'job_id': job_id,
        'status': job['status'],
        'filename': job['filename'],
        'message': job.get('message', '')
    }
    
    if job['status'] == 'completed':
        response['download_url'] = job['download_url']
        response['size_mb'] = job['size_mb']
        response['conversion_time'] = job['conversion_time']
    elif job['status'] == 'failed':
        response['error'] = job.get('error', 'Unknown error')
        if 'stderr_preview' in job:
            response['stderr_preview'] = job['stderr_preview']
        if 'exit_code' in job:
            response['exit_code'] = job['exit_code']
    
    return jsonify(response)

# Stub endpoints removed - focus on core watermarking functionality


# ================================================================
# DEBUG ENDPOINT: BRAND INTEGRITY CHECK
# ================================================================
@app.route('/api/debug/brand-integrity', methods=['GET'])
def debug_brand_integrity():
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imports", "brands")

    if not os.path.exists(base):
        return jsonify({
            "success": False,
            "error": "Brand directory not found",
            "path": base
        }), 500

    brands = {}
    for brand in os.listdir(base):
        folder = os.path.join(base, brand)
        if os.path.isdir(folder):
            brands[brand] = {
                "template.png": os.path.exists(os.path.join(folder, "template.png")),
                "logo.png": os.path.exists(os.path.join(folder, "logo.png")),
                "watermark.png": os.path.exists(os.path.join(folder, "watermark.png")),
                "path": folder
            }

    return jsonify({
        "success": True,
        "brands": brands
    })

# ================================================================
# DEBUG ENDPOINT: FFmpeg FILTER COMPLEX DRY-RUN
# ================================================================
@app.route('/api/debug/build-filter/<brand_name>', methods=['GET'])
def debug_build_filter(brand_name):
    from .video_processor import VideoProcessor
    from .brand_loader import get_available_brands
    import os

    portal_dir = os.path.dirname(os.path.abspath(__file__))
    brands = get_available_brands(portal_dir)

    # Find the brand config by name (case-insensitive)
    brand = next((b for b in brands if b["name"].lower() == brand_name.lower()), None)

    if not brand:
        return jsonify({
            "success": False,
            "error": f"Brand '{brand_name}' not found",
            "brands_available": [b['name'] for b in brands]
        }), 404

    # Generate a dry-run filter without needing a real video
    # Use os.devnull to avoid FFprobe errors
    vp = VideoProcessor(video_path=os.devnull)
    filter_complex = vp.build_filter_complex(brand)

    return jsonify({
        "success": True,
        "brand": brand_name,
        "filter_complex": filter_complex
    })


# ================================================================
# WTF DOWNLOADER ENDPOINTS
# ================================================================

@app.route('/api/detect-platform', methods=['POST'])
def api_detect_platform():
    """Detect the platform from a URL."""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        from downloader.platform_detector import detect_platform
        platform = detect_platform(url)
        return jsonify({"url": url, "platform": platform})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download', methods=['POST'])
def api_download_video():
    """Download a single video."""
    try:
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Import and run the async download function
        import asyncio
        from downloader.batch_downloader import download_single_video
        result = asyncio.run(download_single_video(url))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/batch', methods=['POST'])
def api_download_batch():
    """Download multiple videos."""
    try:
        data = request.get_json()
        urls = data.get('urls', [])
        
        if not urls:
            return jsonify({"error": "At least one URL is required"}), 400
        
        # Import and run the async batch download function
        import asyncio
        from downloader.batch_downloader import download_batch
        results = asyncio.run(download_batch(urls))
        return jsonify({"downloads": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/portal/download_file/<path:filename>")
def download_file(filename):
    """
    Serve downloaded video files. Checks actual storage paths used by downloader.
    """
    from flask import send_file, jsonify
    import os

    # Absolute paths for safety
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    storage_raw = os.path.join(base_dir, 'storage', 'raw')
    portal_outputs = os.path.join(base_dir, 'portal', 'outputs')

    # Construct actual expected file paths
    candidates = [
        os.path.join(storage_raw, filename),        # Primary location used by downloader
        os.path.join(portal_outputs, filename),     # Legacy location
    ]

    # Find file that exists
    for path in candidates:
        if os.path.isfile(path):
            return send_file(
                path,
                as_attachment=True,
                mimetype='video/mp4',
                download_name=filename,
                conditional=True
            )

    # If none exist, return helpful error
    return jsonify({
        "success": False,
        "error": "File not found",
        "searched": candidates
    }), 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
