"""
Platform Detector Module

Detects the platform from a given URL.
"""

import re
from typing import Literal, Union

PlatformType = Literal["tiktok", "instagram", "twitter", "youtube", "unknown"]

def detect_platform(url: str) -> PlatformType:
    """
    Detect the platform from a given URL.
    
    Args:
        url (str): The URL to analyze
        
    Returns:
        PlatformType: The detected platform or "unknown" if not recognized
    """
    url = url.lower().strip()
    
    # TikTok patterns
    if re.search(r'(?:https?://)?(?:www\.)?tiktok\.com/', url):
        return "tiktok"
    
    # Instagram patterns
    if re.search(r'(?:https?://)?(?:www\.)?instagram\.com/', url):
        return "instagram"
    
    # Twitter/X patterns
    if re.search(r'(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/', url):
        return "twitter"
    
    # YouTube patterns
    if re.search(r'(?:https?://)?(?:www\.)?youtube\.com/', url) or \
       re.search(r'(?:https?://)?(?:www\.)?youtu\.be/', url):
        return "youtube"
    
    return "unknown"