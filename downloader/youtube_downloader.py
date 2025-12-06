"""
YouTube Downloader Module

Handles downloading videos from YouTube.
"""

import os
from typing import Optional, Tuple
from yt_dlp import YoutubeDL

def download_youtube_video(url: str, output_dir: str = "./storage/raw/") -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Download a YouTube video.
    
    Args:
        url (str): The YouTube video URL
        output_dir (str): Directory to save the downloaded video
        
    Returns:
        Tuple[bool, Optional[str], Optional[str]]: (success, file_path, error_message)
    """
    try:
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Configure yt-dlp for YouTube
        ydl_opts = {
            'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
            'format': 'mp4',
            'merge_output_format': 'mp4',
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 30,
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