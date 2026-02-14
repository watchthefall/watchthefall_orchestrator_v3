"""
Main entry point for the WTF Downloader application.
"""

from downloader import create_downloader_app

app = create_downloader_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)