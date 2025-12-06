#!/usr/bin/env python3
"""
Test Client for WatchTheFall Orchestrator Portal
"""

import requests
import json
import time
import os
from urllib.parse import urlparse

BASE_URL = "https://watchthefall-portal.onrender.com"

def test_basic_endpoints():
    """Test basic health endpoints"""
    print("=== TESTING BASIC ENDPOINTS ===")
    
    endpoints = [
        ("/", "Root endpoint"),
        ("/api", "API documentation"),
        ("/__debug_alive", "Health check"),
        ("/api/debug/brand-integrity", "Brand integrity check")
    ]
    
    for endpoint, description in endpoints:
        try:
            url = f"{BASE_URL}{endpoint}"
            response = requests.get(url)
            print(f"✓ {description}: {response.status_code}")
            if response.status_code != 200:
                print(f"  Response: {response.text[:100]}...")
        except Exception as e:
            print(f"✗ {description}: {e}")
    
    print()

def fetch_video(url):
    """Fetch a video from URL"""
    print("=== FETCHING VIDEO ===")
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/videos/fetch",
            json={"urls": [url]},
            timeout=60
        )
        
        print(f"Fetch response status: {response.status_code}")
        print(f"Fetch response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                result = data['results'][0]
                if result.get('success'):
                    return result['filename']
                else:
                    print(f"Fetch failed: {result.get('error')}")
                    return None
            else:
                print(f"Fetch failed: {data.get('error')}")
                return None
        else:
            print(f"HTTP Error: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Exception during fetch: {e}")
        return None

def process_brands(source_filename, brands):
    """Process video with selected brands"""
    print("=== PROCESSING BRANDS ===")
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/videos/process_brands",
            json={
                "source_path": source_filename,  # Use source_path instead of url
                "brands": brands
            },
            timeout=300  # 5 minutes for processing
        )
        
        print(f"Process response status: {response.status_code}")
        print(f"Process response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                return data.get('outputs', [])  # Changed from 'processed_videos' to 'outputs'
            else:
                print(f"Processing failed: {data.get('error')}")
                return []
        else:
            print(f"HTTP Error: {response.status_code}")
            return []
            
    except Exception as e:
        print(f"Exception during processing: {e}")
        return []

def download_video(filename, output_path):
    """Download processed video"""
    print("=== DOWNLOADING VIDEO ===")
    
    try:
        response = requests.get(
            f"{BASE_URL}/api/videos/download/{filename}",
            timeout=60
        )
        
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded {filename} to {output_path}")
            return True
        else:
            print(f"Download failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"Exception during download: {e}")
        return False

def main():
    """Main test function"""
    print("WatchTheFall Orchestrator Portal Test Client")
    print("=" * 50)
    
    # Test basic endpoints
    test_basic_endpoints()
    
    # Test video processing workflow
    test_url = "https://www.instagram.com/reel/DR5OXIYjAYi/?utm_source=ig_web_copy_link"
    brands = ["ScotlandWTF"]
    
    print("Starting video processing workflow...")
    print(f"URL: {test_url}")
    print(f"Brands: {brands}")
    print()
    
    # Step 1: Fetch video
    source_filename = fetch_video(test_url)
    if not source_filename:
        print("Failed to fetch video. Exiting.")
        return
    
    print(f"Successfully fetched video: {source_filename}")
    print()
    
    # Step 2: Process with brands
    # Send the filename (not the full URL) to process the local file
    processed_videos = process_brands(source_filename, brands)
    if not processed_videos:
        print("Failed to process video. Exiting.")
        return
    
    print(f"Successfully processed {len(processed_videos)} videos:")
    for video in processed_videos:
        print(f"  - {video.get('filename')} -> {video.get('download_url')}")
    
    # Step 3: Download processed videos
    for video in processed_videos:
        filename = video.get('filename')
        if filename:
            output_path = f"./{filename}"
            if download_video(filename, output_path):
                print(f"Successfully downloaded {filename}")
            else:
                print(f"Failed to download {filename}")

if __name__ == "__main__":
    main()