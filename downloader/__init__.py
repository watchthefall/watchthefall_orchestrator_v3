"""
WTF Downloader Package

Main entry point for the WTF Downloader application.
"""

import os
import asyncio
from flask import Flask, jsonify, request
from .platform_detector import detect_platform
from .batch_downloader import download_batch, download_single_video

def create_downloader_app():
    """Create and configure the WTF Downloader Flask app."""
    app = Flask(__name__)
    
    # Ensure storage directories exist
    os.makedirs("./storage/raw/", exist_ok=True)
    os.makedirs("./storage/processed/", exist_ok=True)
    
    @app.route('/')
    def home():
        """Home endpoint."""
        return jsonify({
            "app": "WTF Downloader",
            "version": "1.0.0",
            "description": "Simple video downloader for TikTok, Instagram, Twitter, and YouTube"
        })
    
    @app.route('/detect-platform', methods=['POST'])
    def detect_platform_endpoint():
        """Detect the platform from a URL."""
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        platform = detect_platform(url)
        return jsonify({"url": url, "platform": platform})
    
    @app.route('/download', methods=['POST'])
    def download_video():
        """Download a single video."""
        data = request.get_json()
        url = data.get('url')
        
        if not url:
            return jsonify({"error": "URL is required"}), 400
        
        # Run the async download function
        result = asyncio.run(download_single_video(url))
        return jsonify(result)
    
    @app.route('/download/batch', methods=['POST'])
    def download_batch_videos():
        """Download multiple videos."""
        data = request.get_json()
        urls = data.get('urls', [])
        
        if not urls:
            return jsonify({"error": "At least one URL is required"}), 400
        
        # Run the async batch download function
        results = asyncio.run(download_batch(urls))
        return jsonify({"downloads": results})
    
    return app