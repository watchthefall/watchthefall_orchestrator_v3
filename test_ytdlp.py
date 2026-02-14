try:
    from yt_dlp import YoutubeDL
    print("yt-dlp imported successfully")
    print(f"YoutubeDL: {YoutubeDL}")
except ImportError as e:
    print(f"Failed to import yt-dlp: {e}")
except Exception as e:
    print(f"Other error importing yt-dlp: {e}")