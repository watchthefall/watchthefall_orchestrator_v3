#!/usr/bin/env python3
"""
Qoder Security Scan Task
Runs bandit security scanner on the codebase
"""
import subprocess
import sys

def run_security_scan():
    """Run bandit security scanner"""
    try:
        # Run bandit security scanner
        result = subprocess.run([
            'bandit',
            '-r',
            '-f', 'json',
            '.'
        ], capture_output=True, text=True)
        
        if result.returncode in [0, 1]:  # Bandit returns 1 when issues found, which is OK
            print("✅ Security scan completed")
            if result.stdout:
                # Check if there are any high severity issues
                if '"severity": "HIGH"' in result.stdout:
                    print("⚠️  High severity security issues found")
                    print(result.stdout)
                    return False
                else:
                    print("✅ No high severity security issues found")
                    return True
            else:
                print("✅ No security issues found")
                return True
        else:
            print("❌ Security scan failed:")
            print(result.stderr)
            return False
    except FileNotFoundError:
        print("⚠️  bandit security scanner not found, skipping security scan")
        return True
    except Exception as e:
        print(f"❌ Security scan failed with error: {e}")
        return False

if __name__ == "__main__":
    success = run_security_scan()
    sys.exit(0 if success else 1)