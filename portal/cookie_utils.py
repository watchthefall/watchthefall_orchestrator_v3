"""
Cookie Utilities for Instagram Authentication
"""
import os
import glob

def find_valid_cookie_file():
    """
    Find and validate cookie files in the cookies directory.
    Returns the path to the first valid cookie file found, or None if none found.
    """
    # Define the cookies directory
    cookies_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cookies')
    
    # Check if cookies directory exists
    if not os.path.exists(cookies_dir):
        print("[COOKIES] Cookies directory not found")
        return None
    
    # Find all cookie files matching the pattern
    cookie_files = glob.glob(os.path.join(cookies_dir, 'cookies_*.txt'))
    
    if not cookie_files:
        print("[COOKIES] No cookie files found in cookies directory")
        return None
    
    # Validate each cookie file
    for cookie_file in cookie_files:
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookie_text = f.read()
            
            # Validate cookie format
            if (cookie_text.startswith('# Netscape HTTP Cookie File') and 
                'ig_did' in cookie_text and 
                'csrftoken' in cookie_text and 
                'sessionid' in cookie_text):
                print(f"[COOKIES] Valid cookie file found: {os.path.basename(cookie_file)}")
                return cookie_file
            else:
                print(f"[COOKIES] Invalid cookie format in: {os.path.basename(cookie_file)}")
        except Exception as e:
            print(f"[COOKIES] Error reading cookie file {os.path.basename(cookie_file)}: {str(e)}")
            continue
    
    print("[COOKIES] No valid cookie files found")
    return None

def load_cookie_content(cookie_file_path):
    """
    Load the content of a cookie file.
    Returns the cookie content as a string, or None if failed.
    """
    try:
        with open(cookie_file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"[COOKIES] Error loading cookie content: {str(e)}")
        return None