import os

# Test cookie file access
cookie_file = './portal/data/cookies.txt'

print(f"Current working directory: {os.getcwd()}")
print(f"Cookie file path: {cookie_file}")
print(f"Cookie file exists: {os.path.exists(cookie_file)}")
print(f"Cookie file is file: {os.path.isfile(cookie_file)}")

if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            print(f"Cookie file content length: {len(content)}")
            print(f"First 100 chars: {content[:100]}")
    except Exception as e:
        print(f"Error reading cookie file: {e}")
else:
    print("Cookie file not found or not readable")