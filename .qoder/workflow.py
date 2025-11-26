#!/usr/bin/env python3
"""
Qoder Workflow Task
Runs all maintenance tasks and commits if they pass
"""
import subprocess
import sys
import datetime
import os

def run_task(script_name, task_name):
    """Run a maintenance task script"""
    try:
        print(f"🔧 Running {task_name}...")
        result = subprocess.run([
            'python', 
            os.path.join('.qoder', script_name)
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ {task_name} passed")
            return True
        else:
            print(f"❌ {task_name} failed:")
            print(result.stdout)
            print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ {task_name} failed with error: {e}")
        return False

def commit_changes():
    """Commit maintenance changes with dated message"""
    try:
        # Check if there are any changes to commit
        result = subprocess.run([
            'git', 'status', '--porcelain'
        ], capture_output=True, text=True)
        
        if not result.stdout.strip():
            print("ℹ️  No changes to commit")
            return True
        
        # Add all changes
        subprocess.run(['git', 'add', '.'], check=True)
        
        # Create commit with dated message
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        commit_message = f"Maintenance: {date_str}"
        
        result = subprocess.run([
            'git', 'commit', '-m', commit_message
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✅ Changes committed: {commit_message}")
            return True
        else:
            print("❌ Failed to commit changes:")
            print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ Commit failed with error: {e}")
        return False

def run_daily_workflow():
    """Run daily maintenance workflow"""
    print("🚀 Running Daily Maintenance Workflow")
    print("=" * 40)
    
    # Run all daily tasks
    lint_ok = run_task('lint.py', 'Lint Check')
    format_ok = run_task('format.py', 'Format Check')
    security_ok = run_task('security_scan.py', 'Security Scan')
    
    # If all tasks pass, commit changes
    if lint_ok and format_ok and security_ok:
        print("\n✅ All daily tasks passed!")
        commit_result = commit_changes()
        return commit_result
    else:
        print("\n❌ Some daily tasks failed, skipping commit")
        return False

def run_weekly_workflow():
    """Run weekly maintenance workflow"""
    print("🚀 Running Weekly Maintenance Workflow")
    print("=" * 40)
    
    # Run all tasks including dependency check
    lint_ok = run_task('lint.py', 'Lint Check')
    format_ok = run_task('format.py', 'Format Check')
    security_ok = run_task('security_scan.py', 'Security Scan')
    dependency_ok = run_task('dependency_check.py', 'Dependency Check')
    
    # If all tasks pass, commit changes
    if lint_ok and format_ok and security_ok and dependency_ok:
        print("\n✅ All weekly tasks passed!")
        commit_result = commit_changes()
        return commit_result
    else:
        print("\n❌ Some weekly tasks failed, skipping commit")
        return False

if __name__ == "__main__":
    # Check if we're running daily or weekly workflow
    if len(sys.argv) > 1 and sys.argv[1] == '--weekly':
        success = run_weekly_workflow()
    else:
        success = run_daily_workflow()
    
    sys.exit(0 if success else 1)