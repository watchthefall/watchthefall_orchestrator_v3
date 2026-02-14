# Test the process_brands logic
import json

# Test case 1: Only url provided
data1 = {
    'url': 'https://example.com/video.mp4',
    'brands': ['ScotlandWTF']
}

url1 = data1.get('url')
selected_brands1 = data1.get('brands', [])

# NEW: accept source_path for local downloaded files
source_path1 = data1.get("source_path")
if source_path1 and not url1:
    url1 = source_path1

print("Test case 1:")
print(f"  url: {url1}")
print(f"  source_path: {source_path1}")
print(f"  selected_brands: {selected_brands1}")
print(f"  Validation: {'PASS' if url1 and selected_brands1 else 'FAIL'}")
print()

# Test case 2: Only source_path provided
data2 = {
    'source_path': 'DR5OXIYjAYi.mp4',
    'brands': ['ScotlandWTF']
}

url2 = data2.get('url')
selected_brands2 = data2.get('brands', [])

# NEW: accept source_path for local downloaded files
source_path2 = data2.get("source_path")
if source_path2 and not url2:
    url2 = source_path2

print("Test case 2:")
print(f"  url: {url2}")
print(f"  source_path: {source_path2}")
print(f"  selected_brands: {selected_brands2}")
print(f"  Validation: {'PASS' if url2 and selected_brands2 else 'FAIL'}")
print()

# Test case 3: Neither provided
data3 = {
    'brands': ['ScotlandWTF']
}

url3 = data3.get('url')
selected_brands3 = data3.get('brands', [])

# NEW: accept source_path for local downloaded files
source_path3 = data3.get("source_path")
if source_path3 and not url3:
    url3 = source_path3

print("Test case 3:")
print(f"  url: {url3}")
print(f"  source_path: {source_path3}")
print(f"  selected_brands: {selected_brands3}")
print(f"  Validation: {'PASS' if url3 and selected_brands3 else 'FAIL'}")
print(f"  Error message: {'URL or source_path is required' if not url3 else 'None'}")