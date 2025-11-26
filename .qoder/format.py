#!/usr/bin/env python3
"""
Qoder Format Task
Runs black formatter on the codebase
"""
import subprocess
import sys

def run_format_check():
    """Run black formatter in check mode"""
    try:
        # Run black in check mode (doesn't modify files)
        result = subprocess.run([
            'black',
            '--check',
            '--line-length=88',
            '.'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Code formatting is correct")
            return True
        else:
            print("❌ Code formatting issues found:")
            print(result.stdout)
            print(result.stderr)
            return False
    except FileNotFoundError:
        print("⚠️  black formatter not found, skipping format check")
        return True
    except Exception as e:
        print(f"❌ Formatting check failed with error: {e}")
        return False

def run_format_fix():
    """Run black formatter to fix formatting issues"""
    try:
        # Run black to fix formatting
        result = subprocess.run([
            'black',
            '--line-length=88',
            '.'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Code formatting fixed")
            return True
        else:
            print("❌ Failed to fix code formatting:")
            print(result.stdout)
            print(result.stderr)
            return False
    except FileNotFoundError:
        print("⚠️  black formatter not found, skipping format fix")
        return True
    except Exception as e:
        print(f"❌ Formatting fix failed with error: {e}")
        return False

if __name__ == "__main__":
    # Check if we should fix formatting or just check
    if len(sys.argv) > 1 and sys.argv[1] == '--fix':
        success = run_format_fix()
    else:
        success = run_format_check()
    
    sys.exit(0 if success else 1)