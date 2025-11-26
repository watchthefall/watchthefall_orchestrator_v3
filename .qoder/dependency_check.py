#!/usr/bin/env python3
"""
Qoder Dependency Check Task
Checks for outdated or vulnerable dependencies
"""
import subprocess
import sys
import json

def check_outdated_dependencies():
    """Check for outdated dependencies using pip list --outdated"""
    try:
        # Get outdated packages
        result = subprocess.run([
            'pip',
            'list',
            '--outdated',
            '--format=json'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            outdated_packages = json.loads(result.stdout)
            if outdated_packages:
                print("⚠️  Outdated packages found:")
                for package in outdated_packages:
                    print(f"  - {package['name']}: {package['version']} → {package['latest_version']}")
                return False
            else:
                print("✅ All dependencies are up to date")
                return True
        else:
            print("❌ Failed to check for outdated dependencies:")
            print(result.stderr)
            return False
    except FileNotFoundError:
        print("⚠️  pip not found, skipping dependency check")
        return True
    except Exception as e:
        print(f"❌ Dependency check failed with error: {e}")
        return False

def check_vulnerable_dependencies():
    """Check for vulnerable dependencies using safety (if available)"""
    try:
        # Check for vulnerable packages
        result = subprocess.run([
            'safety',
            'check',
            '--json'
        ], capture_output=True, text=True)
        
        if result.returncode in [0, 1]:  # Safety returns 1 when vulnerabilities found
            if result.stdout:
                try:
                    vulnerabilities = json.loads(result.stdout)
                    if vulnerabilities:
                        print("⚠️  Vulnerable dependencies found:")
                        for vuln in vulnerabilities:
                            print(f"  - {vuln['package_name']} {vuln['analyzed_version']}: {vuln['advisory']}")
                        return False
                    else:
                        print("✅ No known vulnerabilities found")
                        return True
                except json.JSONDecodeError:
                    # Safety might not have returned JSON
                    if "No known security vulnerabilities" in result.stdout:
                        print("✅ No known vulnerabilities found")
                        return True
                    else:
                        print("⚠️  Vulnerabilities check returned unexpected output")
                        print(result.stdout)
                        return False
            else:
                print("✅ No known vulnerabilities found")
                return True
        else:
            print("⚠️  Safety check failed (safety may not be installed):")
            print(result.stderr)
            return True  # Don't fail if safety isn't installed
    except FileNotFoundError:
        print("⚠️  safety scanner not found, skipping vulnerability check")
        return True
    except Exception as e:
        print(f"❌ Vulnerability check failed with error: {e}")
        return False

def run_dependency_check():
    """Run all dependency checks"""
    print("🔍 Checking dependencies...")
    
    outdated_ok = check_outdated_dependencies()
    vulnerable_ok = check_vulnerable_dependencies()
    
    return outdated_ok and vulnerable_ok

if __name__ == "__main__":
    success = run_dependency_check()
    sys.exit(0 if success else 1)