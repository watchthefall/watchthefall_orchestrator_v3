"""
Portal Runner - Start the WatchTheFall Portal
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from portal.app import app

if __name__ == '__main__':
    print("=" * 60)
    print("WatchTheFall Portal Starting...")
    print("=" * 60)
    print(f"\n  Portal URL: http://localhost:5000/portal/")
    print(f"  Test URL:   http://localhost:5000/portal/test")
    print(f"\n  API Endpoints:")
    print(f"   POST /api/videos/upload")
    print(f"   POST /api/videos/process")
    print(f"   GET  /api/videos/status/<job_id>")
    print(f"   GET  /api/templates")
    print(f"   GET  /api/system/logs")
    print(f"   GET  /api/system/queue")
    print(f"\n  Database: portal/db/portal.db")
    print(f"  Uploads:   portal/uploads/")
    print(f"  Outputs:   portal/outputs/")
    print("\n" + "=" * 60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)
