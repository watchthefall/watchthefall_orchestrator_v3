import os
from portal.config import OUTPUT_DIR

# Test fetch function logic
print(f"OUTPUT_DIR: {OUTPUT_DIR}")

# Test yt-dlp import
try:
    from yt_dlp import YoutubeDL
    print("yt-dlp imported successfully")
except ImportError as e:
    print(f"Failed to import yt-dlp: {e}")

# Test cookie file
cookie_file = './portal/data/cookies.txt'
print(f"Cookie file: {cookie_file}")
print(f"Cookie file exists: {os.path.exists(cookie_file)}")

if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            has_cookie_data = False
            if content:
                lines = content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '\t' in line:
                            has_cookie_data = True
                            break
            
            if has_cookie_data:
                print("Cookie file has valid data")
            else:
                print("Cookie file appears to be empty or only contains header")
    except Exception as e:
        print(f"Error reading cookie file: {e}")

# Test yt-dlp options
ydl_opts = {
    'outtmpl': os.path.join(OUTPUT_DIR, '%(id)s.%(ext)s'),
    'merge_output_format': 'mp4',
    'format': 'mp4',
    'retries': 5,
    'fragment_retries': 5,
    'socket_timeout': 300,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
}

if has_cookie_data:
    ydl_opts['cookiefile'] = cookie_file
    print(f"Added cookiefile to ydl_opts: {cookie_file}")

print("Test completed successfully")