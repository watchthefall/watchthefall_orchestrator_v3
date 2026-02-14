import os

# Test cookie validation logic
cookie_file = './portal/data/cookies.txt'

if os.path.exists(cookie_file) and os.path.isfile(cookie_file):
    print("Cookie file exists")
    # Test if file is readable and has valid content
    with open(cookie_file, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        print(f"Content length: {len(content)}")
        print(f"Content preview: {content[:100]}")
        # Check if file has actual cookie data (not just comments)
        # File is valid if it has content and either:
        # 1. Doesn't start with the header (unlikely but possible), OR
        # 2. Has more than one line (indicating actual cookie data beyond header)
        # Additional check: look for actual cookie data patterns
        has_cookie_data = False
        if content:
            lines = content.split('\n')
            print(f"Number of lines: {len(lines)}")
            # Check if we have more than just header lines
            # Look for lines that contain actual cookie data (domain, flag, path, etc.)
            for i, line in enumerate(lines):
                line = line.strip()
                print(f"Line {i}: '{line}'")
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    # Check if this looks like a cookie line (has tab-separated values)
                    if '\t' in line:
                        has_cookie_data = True
                        print(f"Found cookie data on line {i}")
                        break
        
        if has_cookie_data:
            print("Cookie file has valid data")
        else:
            print("Cookie file appears to be empty or only contains header")
else:
    print("Cookie file not found or not readable")