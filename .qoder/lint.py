#!/usr/bin/env python3
"""
Qoder Lint Task
Runs pylint on the codebase
"""
import subprocess
import sys
import os

def run_lint():
    """Run pylint on Python files"""
    try:
        # Run pylint on all Python files
        result = subprocess.run([
            'pylint',
            '--disable=C0114,C0115,C0116',  # Disable missing docstring warnings
            '--max-line-length=120',
            '--recursive=y',
            '.'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Linting passed with no errors")
            return True
        else:
            print("❌ Linting found issues:")
            print(result.stdout)
            print(result.stderr)
            return False
    except FileNotFoundError:
        print("⚠️  pylint not found, skipping lint check")
        return True
    except Exception as e:
        print(f"❌ Linting failed with error: {e}")
        return False

if __name__ == "__main__":
    success = run_lint()
    sys.exit(0 if success else 1)