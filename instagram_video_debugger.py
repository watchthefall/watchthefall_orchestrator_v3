#!/usr/bin/env python3
"""
Instagram Video Debugger Script

This script tests various yt-dlp configurations to determine:
- Whether IG is serving audio-only content
- Which User-Agents unlock video streams
- Whether ffprobe detects video streams after download
- Whether normalization restores the video track
- Whether Instagram is blocking video from server IP ranges
"""

import os
import json
import subprocess
import shutil
from yt_dlp import YoutubeDL

# Configuration - UPDATE THIS WITH A REAL INSTAGRAM REEL URL FOR TESTING
REEL_URL = input("Enter Instagram Reel URL for testing (or press Enter for default): ").strip()
if not REEL_URL:
    REEL_URL = "https://www.instagram.com/reel/C8uXzXkM2yQ/"  # Replace with actual URL for testing

OUTPUT_DIR = "./debug_output"
FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"
COOKIE_FILE = "./portal/data/cookies.txt"  # Use the same cookie file as the portal

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

def has_valid_cookies():
    """Check if cookie file exists and has valid content."""
    if os.path.exists(COOKIE_FILE) and os.path.isfile(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
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
                    return has_cookie_data
        except Exception as e:
            print(f"[COOKIE] Error reading cookie file: {e}")
    return False

def run_ffprobe(file_path):
    """Run ffprobe on a file and return the JSON output."""
    cmd = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        file_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            print(f"ffprobe failed: {result.stderr}")
            return None
    except Exception as e:
        print(f"Error running ffprobe: {e}")
        return None

def analyze_ffprobe_result(probe_result):
    """Analyze ffprobe result and return a summary."""
    if not probe_result:
        return "ffprobe failed"
    
    streams = probe_result.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    
    summary = {
        "total_streams": len(streams),
        "video_streams": len(video_streams),
        "audio_streams": len(audio_streams),
        "video_codec": video_streams[0].get("codec_name") if video_streams else None,
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        "duration": probe_result.get("format", {}).get("duration", "unknown")
    }
    
    return summary

def normalize_video(input_path):
    """Normalize video timestamps to fix corrupted Instagram video files."""
    try:
        fixed_path = input_path.replace(".mp4", "_normalized.mp4")
        print(f"[NORMALIZE] Normalizing video timestamps: {input_path}")
        
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", input_path,
            "-vf", "fps=25",
            "-c:v", "copy",
            "-c:a", "aac",
            "-fflags", "+genpts",
            fixed_path
        ]
        
        print(f"[NORMALIZE] Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(fixed_path):
            file_size = os.path.getsize(fixed_path) / (1024 * 1024)
            print(f"[NORMALIZE] Successfully normalized video: {fixed_path} ({file_size:.2f}MB)")
            return fixed_path
        else:
            print(f"[NORMALIZE] Failed to normalize video. stderr: {result.stderr}")
            if os.path.exists(fixed_path):
                os.remove(fixed_path)  # Clean up failed output
            return input_path
    except Exception as e:
        print(f"[NORMALIZE] Error during normalization: {e}")
        return input_path

def download_with_config(attempt_num, config_name, ydl_opts):
    """Download video with specific configuration."""
    print(f"\n===== ATTEMPT {attempt_num} - {config_name} =====")
    
    output_file = os.path.join(OUTPUT_DIR, f"attempt_{attempt_num}.mp4")
    ffprobe_file = os.path.join(OUTPUT_DIR, f"attempt_{attempt_num}_ffprobe.json")
    normalized_file = os.path.join(OUTPUT_DIR, f"attempt_{attempt_num}_normalized.mp4")
    
    # Update output template
    ydl_opts['outtmpl'] = output_file
    
    # Add cookie support if available
    if has_valid_cookies():
        ydl_opts['cookiefile'] = COOKIE_FILE
        print(f"[COOKIE] Using cookie file: {COOKIE_FILE}")
    else:
        print("[COOKIE] No valid cookie file found, proceeding without cookies")
    
    # Add fallback options for better reliability
    ydl_opts.setdefault('retries', 3)
    ydl_opts.setdefault('fragment_retries', 3)
    ydl_opts.setdefault('socket_timeout', 30)
    
    try:
        # Download video
        print(f"[DOWNLOAD] Using config: {config_name}")
        with YoutubeDL(ydl_opts) as ydl:
            print(f"[DOWNLOAD] Downloading: {REEL_URL}")
            info = ydl.extract_info(REEL_URL, download=True)
            print(f"[DOWNLOAD] Success: {info.get('title', 'Unknown')}")
        
        # Check if file exists
        if not os.path.exists(output_file):
            print("[RESULT] Download failed - no output file")
            return "failed"
        
        # Get file size
        file_size = os.path.getsize(output_file) / (1024 * 1024)  # MB
        print(f"[FILE] Size: {file_size:.2f} MB")
        
        # Run ffprobe analysis
        print("[FFPROBE] Analyzing downloaded file...")
        probe_result = run_ffprobe(output_file)
        
        if probe_result:
            # Save ffprobe result
            with open(ffprobe_file, 'w') as f:
                json.dump(probe_result, f, indent=2)
            
            # Analyze result
            analysis = analyze_ffprobe_result(probe_result)
            print(f"[FFPROBE] Analysis: {analysis}")
            
            # Check for video stream
            if analysis["video_streams"] > 0:
                print("[RESULT] SUCCESS - Video stream detected")
                
                # Try normalization
                print("[NORMALIZE] Testing timestamp normalization...")
                normalized_path = normalize_video(output_file)
                
                if normalized_path != output_file:
                    # Analyze normalized file
                    print("[FFPROBE] Analyzing normalized file...")
                    normalized_probe = run_ffprobe(normalized_path)
                    if normalized_probe:
                        normalized_analysis = analyze_ffprobe_result(normalized_probe)
                        print(f"[NORMALIZED] Analysis: {normalized_analysis}")
                        
                        # Clean up normalized file
                        if os.path.exists(normalized_path):
                            os.remove(normalized_path)
                
                return "video+audio"
            else:
                print("[RESULT] AUDIO-ONLY - No video stream detected")
                return "audio-only"
        else:
            print("[RESULT] FFPROBE FAILED")
            return "ffprobe-failed"
            
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        return "exception"

def main():
    print("===== Instagram Video Diagnostic =====")
    print(f"URL: {REEL_URL}")
    print(f"Cookie file: {COOKIE_FILE}")
    print(f"Cookie file exists: {os.path.exists(COOKIE_FILE)}")
    
    if has_valid_cookies():
        print("Valid cookies detected")
    else:
        print("No valid cookies found - downloads may fail")
    
    # Define different configurations to test
    configs = [
        {
            "name": "Default yt-dlp with fallback",
            "opts": {
                'format': 'bv*+ba/best',
                'merge_output_format': 'mp4',
                'prefer_ffmpeg': True,
            }
        },
        {
            "name": "Android User-Agent with fallback",
            "opts": {
                'format': 'bv*+ba/best',
                'merge_output_format': 'mp4',
                'prefer_ffmpeg': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36',
                }
            }
        },
        {
            "name": "Instagram App Headers",
            "opts": {
                'format': 'bv*+ba/best',
                'merge_output_format': 'mp4',
                'prefer_ffmpeg': True,
                'http_headers': {
                    'User-Agent': 'Instagram 271.1.0.21.84 Android',
                    'X-IG-App-ID': '567067343352427',
                }
            }
        },
        {
            "name": "Full Instagram App Headers with Extractor",
            "opts": {
                'format': 'bv*+ba/best',
                'merge_output_format': 'mp4',
                'prefer_ffmpeg': True,
                'http_headers': {
                    'User-Agent': 'Instagram 271.1.0.21.84 Android',
                    'X-IG-App-ID': '567067343352427',
                    'X-Requested-With': 'com.instagram.android',
                },
                'extractor_args': {'instagram': {'api': ['mobile']}}
            }
        }
    ]
    
    results = []
    
    # Run each configuration
    for i, config in enumerate(configs, 1):
        result = download_with_config(i, config["name"], config["opts"])
        results.append({
            "attempt": i,
            "name": config["name"],
            "result": result
        })
    
    # Summary
    print("\n===== SUMMARY =====")
    for res in results:
        print(f"ATTEMPT {res['attempt']} - {res['name']}: {res['result']}")
    
    # Find best working method
    video_results = [r for r in results if r["result"] == "video+audio"]
    if video_results:
        best_method = video_results[0]
        print(f"\nBest working method: ATTEMPT {best_method['attempt']} - {best_method['name']}")
        print("Portal should use: Full Instagram App Headers")
    else:
        print("\nNo method successfully retrieved video stream")
        print("Portal should use: Enhanced retry logic with fallback strategies")
    
    print("\nRecommendation to fix:")
    print("- Implement Instagram App emulation headers")
    print("- Add fallback to different extractors")
    print("- Include timestamp normalization")
    print("- Add comprehensive error handling")

if __name__ == "__main__":
    main()