#!/usr/bin/env python3
"""
Hardcoded Instagram Test Script

This script tests Instagram video downloading using the same approach as the portal.
"""

import os
import json
import subprocess
from yt_dlp import YoutubeDL

# Configuration
OUTPUT_DIR = "./test_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def test_instagram_download():
    """Test Instagram download with the same configuration as the portal."""
    # Use a public Instagram reel for testing
    url = "https://www.instagram.com/reel/C8uXzXkM2yQ/"
    print(f"Testing Instagram download: {url}")
    
    # Use the same configuration as the portal
    ydl_opts = {
        'outtmpl': os.path.join(OUTPUT_DIR, '%(id)s.%(ext)s'),
        'merge_output_format': 'mp4',
        'format': 'mp4',
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
    
    # Add cookie support if available
    cookie_file = './portal/data/cookies.txt'
    if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    lines = content.split('\n')
                    has_cookie_data = False
                    for line in lines:
                        if line and not line.startswith('#'):
                            # Check if this looks like a cookie line (has tab-separated values)
                            if '\t' in line:
                                has_cookie_data = True
                                break
                    if has_cookie_data:
                        ydl_opts['cookiefile'] = cookie_file
                        print(f"Using cookie file: {cookie_file}")
        except Exception as e:
            print(f"Error reading cookie file: {e}")
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            print("Downloading...")
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            print(f"Download successful: {filename}")
            
            # Check file size
            if os.path.exists(filename):
                file_size = os.path.getsize(filename) / (1024 * 1024)  # MB
                print(f"File size: {file_size:.2f} MB")
                
                # Run ffprobe to check streams
                cmd = [
                    'ffprobe', '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_streams', '-show_format',
                    filename
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    probe_result = json.loads(result.stdout)
                    streams = probe_result.get("streams", [])
                    video_streams = [s for s in streams if s.get("codec_type") == "video"]
                    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
                    
                    print(f"Video streams: {len(video_streams)}")
                    print(f"Audio streams: {len(audio_streams)}")
                    
                    if video_streams:
                        print("SUCCESS: Video stream detected")
                        return True
                    else:
                        print("FAILED: No video stream detected (audio-only)")
                        return False
                else:
                    print(f"ffprobe failed: {result.stderr}")
                    return False
            else:
                print("Download failed: File not found")
                return False
    except Exception as e:
        print(f"Download failed: {e}")
        return False

if __name__ == "__main__":
    test_instagram_download()