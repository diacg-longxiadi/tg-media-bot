import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
LOCAL_API_URL = os.environ.get("LOCAL_API_URL", "")
COOKIES_FILE = os.environ.get("COOKIES_FILE", "/app/cookies.txt")
CACHE_FILE = os.environ.get("CACHE_FILE", "/app/cache.json")
TORRENT_DIR = os.environ.get("TORRENT_DIR", "/downloads")

QB_HOST = os.environ.get("QB_HOST", "http://qbittorrent:8080")
QB_USER = os.environ.get("QB_USER", "admin")
QB_PASS = os.environ.get("QB_PASS", "adminadmin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
