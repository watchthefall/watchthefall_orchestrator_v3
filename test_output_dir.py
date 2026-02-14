import os

# Test OUTPUT_DIR access
from portal.config import OUTPUT_DIR

print(f"OUTPUT_DIR: {OUTPUT_DIR}")
print(f"OUTPUT_DIR exists: {os.path.exists(OUTPUT_DIR)}")
print(f"OUTPUT_DIR is writable: {os.access(OUTPUT_DIR, os.W_OK)}")

# Try to create a test file
try:
    test_file = os.path.join(OUTPUT_DIR, 'test.txt')
    with open(test_file, 'w') as f:
        f.write('test')
    print(f"Successfully wrote to {test_file}")
    os.remove(test_file)
    print(f"Successfully removed {test_file}")
except Exception as e:
    print(f"Error with OUTPUT_DIR: {e}")