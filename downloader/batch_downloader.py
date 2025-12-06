"""
Batch Downloader Module

Handles downloading multiple videos concurrently.
"""

import os
import asyncio
from typing import List, Tuple, Dict, Any
from .platform_detector import detect_platform
from .tiktok_downloader import download_tiktok_video
from .insta_downloader import download_instagram_video
from .twitter_downloader import download_twitter_video
from .youtube_downloader import download_youtube_video

async def download_single_video(url: str, output_dir: str = "./storage/raw/") -> Dict[str, Any]:
    """
    Download a single video based on its platform.
    
    Args:
        url (str): The video URL
        output_dir (str): Directory to save the downloaded video
        
    Returns:
        Dict[str, Any]: Download result with status and file info
    """
    platform = detect_platform(url)
    
    if platform == "tiktok":
        success, file_path, error = download_tiktok_video(url, output_dir)
    elif platform == "instagram":
        success, file_path, error = download_instagram_video(url, output_dir)
    elif platform == "twitter":
        success, file_path, error = download_twitter_video(url, output_dir)
    elif platform == "youtube":
        success, file_path, error = download_youtube_video(url, output_dir)
    else:
        return {
            "url": url,
            "success": False,
            "error": "Unsupported platform",
            "platform": platform
        }
    
    return {
        "url": url,
        "success": success,
        "file_path": file_path,
        "error": error,
        "platform": platform
    }

async def download_batch(urls: List[str], output_dir: str = "./storage/raw/") -> List[Dict[str, Any]]:
    """
    Download multiple videos concurrently.
    
    Args:
        urls (List[str]): List of video URLs
        output_dir (str): Directory to save the downloaded videos
        
    Returns:
        List[Dict[str, Any]]: List of download results
    """
    # Create tasks for concurrent downloads
    tasks = [download_single_video(url, output_dir) for url in urls]
    
    # Execute all downloads concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results, handling any exceptions
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            processed_results.append({
                "url": urls[i],
                "success": False,
                "error": str(result),
                "platform": "unknown"
            })
        else:
            processed_results.append(result)
    
    return processed_results