"""
Instagram Downloader Module

Handles downloading videos from Instagram.
"""

import os
from typing import Optional, Tuple
from yt_dlp import YoutubeDL

def download_instagram_video(url: str, output_dir: str = "./storage/raw/") -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Download an Instagram video.
    
    Args:
        url (str): The Instagram video URL
        output_dir (str): Directory to save the downloaded video
        
    Returns:
        Tuple[bool, Optional[str], Optional[str]]: (success, file_path, error_message)
    """
    try:
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Configure yt-dlp for Instagram with app emulation
        ydl_opts = {
            'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
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
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            if os.path.exists(file_path):
                return True, file_path, None
            else:
                return False, None, "Download completed but file not found"
                
    except Exception as e:
        return False, None, str(e)