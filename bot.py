import os
import hashlib
import re
import json
import asyncio
import tempfile
import glob
import subprocess
import shutil
import traceback
import time
import urllib.parse
from pathlib import Path

from telegram import Update, InputMediaPhoto, Document
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
import yt_dlp

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
LOCAL_API_URL = os.environ.get("LOCAL_API_URL", "")
COOKIES_FILE  = os.environ.get("COOKIES_FILE", "/app/cookies.txt")
CACHE_FILE    = os.environ.get("CACHE_FILE", "/app/cache.json")
TORRENT_DIR   = os.environ.get("TORRENT_DIR", "/downloads")

QB_HOST       = os.environ.get("QB_HOST", "http://qbittorrent:8080")
QB_USER       = os.environ.get("QB_USER", "admin")
QB_PASS       = os.environ.get("QB_PASS", "adminadmin")

# ── 快取 ──────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[cache] 寫入失敗：{e}")

_cache: dict = _load_cache()

def cache_get(url: str):
    return _cache.get(url)

def cache_set(url: str, value: dict):
    _cache[url] = value
    _save_cache(_cache)

# ── Deep link URL store ────────────────────────────────────────────────────────

def _url_store() -> dict:
    if "__url_store__" not in _cache:
        _cache["__url_store__"] = {}
    return _cache["__url_store__"]

def store_url(url: str) -> str:
    key = hashlib.md5(url.encode()).hexdigest()[:8].upper()
    _url_store()[key] = url
    _save_cache(_cache)
    return key

def retrieve_url(key: str) -> str | None:
    return _url_store().get(key)

# ── 種子任務佇列 ──────────────────────────────────────────────────────────────

_active_tasks: int = 0
_torrent_queue: asyncio.Queue   # 延遲初始化

# ── URL patterns ──────────────────────────────────────────────────────────────

BILIBILI_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/\S+|"
    r"https?://b23\.tv/\S+",
    re.IGNORECASE,
)
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?youtube\.com/watch\?[^\s]*v=[\w-]+\S*|"
    r"https?://youtu\.be/[\w-]+\S*|"
    r"https?://(?:www\.)?youtube\.com/shorts/[\w-]+\S*",
    re.IGNORECASE,
)
TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+",
    re.IGNORECASE,
)
TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+/status/\d+\S*",
    re.IGNORECASE,
)
XIAOHONGSHU_RE = re.compile(
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-zA-Z0-9]+|"
    r"https?://xhslink\.com/\S+",
    re.IGNORECASE,
)
DOUYIN_RE = re.compile(
    r"https?://(?:www\.)?douyin\.com/video/\d+|"
    r"https?://v\.douyin\.com/\S+",
    re.IGNORECASE,
)
MAGNET_RE = re.compile(
    r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40,}[^\s]*",
    re.IGNORECASE,
)
GENERIC_URL_RE  = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TIKTOK_PHOTO_RE = re.compile(r"tiktok\.com/.*/photo(?:/|\?)", re.IGNORECASE)

def _parse_tweet_id(url: str) -> str | None:
    m = re.search(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)", url)
    return m.group(1) if m else None

def extract_known_urls(text: str):
    results, seen = [], set()
    for pattern, platform in [
        (BILIBILI_RE, "bilibili"),
        (YOUTUBE_RE,  "youtube"),
        (TIKTOK_RE,   "tiktok"),
        (TWITTER_RE,  "twitter"),
        (XIAOHONGSHU_RE, "xiaohongshu"),
        (DOUYIN_RE,   "douyin"),
    ]:
        for m in pattern.finditer(text):
            if m.group() not in seen:
                results.append((m.group(), platform))
                seen.add(m.group())
    return results

def extract_generic_urls(text: str):
    known = {u for u, _ in extract_known_urls(text)}
    results = []
    for m in GENERIC_URL_RE.finditer(text):
        url = m.group().rstrip(".,;)")
        if url not in known and url not in results:
            results.append(url)
    return results

def extract_magnets(text: str) -> list[str]:
    return [m.group() for m in MAGNET_RE.finditer(text)]

# ── helpers ───────────────────────────────────────────────────────────────────

def _escape_md(text: str) -> str:
    """跳脫 Markdown v1 特殊字元。"""
    return text.replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")

def _fmt_speed(bps: int) -> str:
    if bps >= 1024 * 1024:
        return f"{bps/1024/1024:.1f} MB/s"
    if bps >= 1024:
        return f"{bps/1024:.1f} KB/s"
    return f"{bps} B/s"

# ── yt-dlp helpers ────────────────────────────────────────────────────────────

def _base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "socket_timeout": 60, "retries": 5,
             "js_runtimes": {"node": {}, "deno": {}, "bun": {}},
             "remote_components": ["ejs:github"]}
    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts

def _video_opts(out_tmpl: str) -> dict:
    return {
        **_base_opts(), "outtmpl": out_tmpl,
        "format": "bestvideo+bestaudio/best",
        "format_sort": ["res:1080", "ext:mp4:m4a", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "check_formats": False,
    }

def _video_opts_fallback(out_tmpl: str) -> dict:
    return {
        **_base_opts(), "outtmpl": out_tmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "check_formats": False,
    }

def _any_opts(out_tmpl: str) -> dict:
    return {**_base_opts(), "outtmpl": out_tmpl, "format": "best"}


async def _run(fn):
    return await asyncio.get_event_loop().run_in_executor(None, fn)


async def dl_video(url: str, tmpdir: str):
    out = os.path.join(tmpdir, "%(title).120s.%(ext)s")

    def _():
        try:
            with yt_dlp.YoutubeDL(_video_opts(out)) as ydl:
                info = ydl.extract_info(url, download=True) or {}
                title = info.get("title", "")
        except yt_dlp.utils.DownloadError as e:
            if "Requested format is not available" not in str(e):
                raise
            print(f"[dl] format_sort failed, retrying with best: {e}")
            with yt_dlp.YoutubeDL(_video_opts_fallback(out)) as ydl:
                info = ydl.extract_info(url, download=True) or {}
                title = info.get("title", "")

        files = (glob.glob(os.path.join(tmpdir, "*.mp4")) +
                 glob.glob(os.path.join(tmpdir, "*.mkv")) +
                 glob.glob(os.path.join(tmpdir, "*.webm")))
        if not files:
            raise FileNotFoundError("yt-dlp 未產生影片檔案")
        return title, files[0]

    return await _run(_)


async def dl_any(url: str, tmpdir: str):
    out = os.path.join(tmpdir, "%(autonumber)s.%(ext)s")

    def _():
        with yt_dlp.YoutubeDL(_any_opts(out)) as ydl:
            info = ydl.extract_info(url, download=True) or {}
            title = info.get("title", "")
        imgs = sorted(
            glob.glob(os.path.join(tmpdir, "*.jpg")) +
            glob.glob(os.path.join(tmpdir, "*.jpeg")) +
            glob.glob(os.path.join(tmpdir, "*.png")) +
            glob.glob(os.path.join(tmpdir, "*.webp"))
        )
        vids = (glob.glob(os.path.join(tmpdir, "*.mp4")) +
                glob.glob(os.path.join(tmpdir, "*.mkv")) +
                glob.glob(os.path.join(tmpdir, "*.webm")))
        if imgs:
            return title, "photos", imgs
        if vids:
            return title, "video", vids[0]
        raise FileNotFoundError("yt-dlp 未產生任何媒體")

    return await _run(_)


async def dl_gallery(url: str, tmpdir: str):
    def _():
        cmd = ["gallery-dl", "-d", tmpdir]
        if os.path.isfile(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"gallery-dl 失敗：{result.stderr[-500:]}")
        all_files = []
        for root, _, files in os.walk(tmpdir):
            for f in files:
                all_files.append(os.path.join(root, f))
        imgs = sorted([f for f in all_files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))])
        vids = [f for f in all_files if f.lower().endswith(('.mp4', '.mkv', '.webm'))]
        title = url.split('/')[-1] if '/' in url else "TikTok"
        if imgs:
            return title, "photos", imgs
        if vids:
            return title, "video", vids[0]
        raise FileNotFoundError("gallery-dl 未產生任何媒體")
    return await _run(_)


async def dl_twitter_photos(url: str, tmpdir: str):
    """從推文中下載圖片（透過 vxtwitter API）。"""
    tweet_id = _parse_tweet_id(url)
    if not tweet_id:
        raise ValueError("無法解析推文 ID")
    import requests as _req

    def _():
        api_url = f"https://api.vxtwitter.com/twitter/status/{tweet_id}"
        r = _req.get(api_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        r.raise_for_status()
        data = r.json()

        if not data.get("media_extended"):
            if not data.get("mediaURLs"):
                raise FileNotFoundError("推文中沒有圖片")
            urls = data["mediaURLs"]
        else:
            urls = [m["url"] for m in data["media_extended"]
                    if m.get("type") in ("image", "photo")]

        if not urls:
            raise FileNotFoundError("推文中沒有圖片")

        paths = []
        for i, img_url in enumerate(urls):
            resp = _req.get(img_url, timeout=30)
            ext = img_url.rsplit(".", 1)[-1] if "." in img_url else "jpg"
            fname = os.path.join(tmpdir, f"twitter_img_{i:03d}.{ext}")
            with open(fname, "wb") as f:
                f.write(resp.content)
            paths.append(fname)

        name = data.get("user_screen_name", "?")
        tweet_text = data.get("text", "").strip()
        title = f"@{name}"
        if tweet_text:
            if len(tweet_text) > 200:
                tweet_text = tweet_text[:200] + "…"
            title = f"@{name}\n\n{tweet_text}"
        return title, "photos", paths

    return await _run(_)


async def dl_douyin(url: str, tmpdir: str):
    """直接爬抖音頁面提取影片網址（免 cookies、免 API）。"""
    import requests as _req
    import base64

    def _():
        # 先解析短網址（v.douyin.com → www.douyin.com/video/ID）
        final_url = url
        if "v.douyin.com" in url:
            try:
                resp = _req.get(url, timeout=10, allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"})
                final_url = resp.url
                print(f"[douyin] 短網址解析：{url} → {final_url}")
            except Exception as e:
                print(f"[douyin] 短網址解析失敗：{e}")

        # 從 URL 提取影片 ID
        m = re.search(r"video/(\d+)", final_url)
        video_id = m.group(1) if m else ""
        if not video_id:
            raise ValueError(f"無法從網址提取抖音影片 ID：{final_url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        }

        # 抓頁面 HTML
        resp = _req.get(final_url, timeout=15, headers=headers)
        html = resp.text

        # 方法 1: 找 RENDER_DATA（base64 編碼的 JSON）
        video_url = None
        title = "抖音"

        render_match = re.search(r'id="RENDER_DATA"[^>]*>\s*([^<]+)', html)
        if render_match:
            try:
                encoded = render_match.group(1)
                # 補 padding
                pad = 4 - len(encoded) % 4
                if pad != 4:
                    encoded += "=" * pad
                decoded = base64.b64decode(encoded).decode("utf-8")
                data = json.loads(decoded)

                # 遍歷 JSON 找 video url
                def find_video(obj, depth=0):
                    if depth > 20:
                        return None
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("video", "video_url", "play_url", "play_addr", "main_url"):
                                val = v
                                if isinstance(val, dict):
                                    val = val.get("url_list", val.get("url", ""))
                                if isinstance(val, list):
                                    val = val[0] if val else None
                                if val and isinstance(val, str) and val.startswith("http"):
                                    return val
                            if isinstance(v, (dict, list)):
                                result = find_video(v, depth + 1)
                                if result:
                                    return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_video(item, depth + 1)
                            if result:
                                return result
                    return None

                video_url = find_video(data)
                if not video_url:
                    print("[douyin] RENDER_DATA 中未找到影片網址")

                # 找 title
                def find_title(obj, depth=0):
                    if depth > 15:
                        return None
                    if isinstance(obj, dict):
                        if obj.get("desc"):
                            return obj["desc"]
                        for v in obj.values():
                            result = find_title(v, depth + 1)
                            if result:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_title(item, depth + 1)
                            if result:
                                return result
                    return None

                t = find_title(data)
                if t:
                    title = t

            except Exception as e:
                print(f"[douyin] RENDER_DATA 解析失敗：{e}")

        # 方法 2: RENDER_DATA 失敗，直接用 douyin 的 aweme API
        if not video_url:
            print("[douyin] RENDER_DATA 失敗，嘗試 aweme API…")
            try:
                api_headers = {**headers, "Accept": "application/json"}
                api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={video_id}"
                api_resp = _req.get(api_url, timeout=15, headers=api_headers)
                if api_resp.status_code == 200:
                    api_data = api_resp.json()
                    aweme = api_data.get("aweme_detail", {})
                    video = aweme.get("video", {})
                    play_addr = video.get("play_addr", {})
                    url_list = play_addr.get("url_list", [])
                    if url_list:
                        video_url = url_list[0]
                    title = aweme.get("desc") or title
            except Exception as e:
                print(f"[douyin] aweme API 也失敗：{e}")

        if not video_url:
            raise FileNotFoundError("抖音影片網址提取失敗")

        # 下載影片
        print(f"[douyin] 下載影片：{video_url[:80]}…")
        out = os.path.join(tmpdir, "douyin.mp4")
        resp = _req.get(video_url.replace("https://", "http://"), timeout=120,
                        headers=headers, stream=True)
        if resp.status_code >= 400:
            # 用原 URL 重試
            resp = _req.get(video_url, timeout=120, headers=headers, stream=True)
        with open(out, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return title, "video", out

    return await _run(_)


async def fetch_m3u8(url: str):
    def _():
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", errors="ignore")
            m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
            return m.group() if m else None
        except Exception:
            return None
    return await _run(_)

async def dl_m3u8(m3u8_url: str, tmpdir: str) -> str:
    out = os.path.join(tmpdir, "output.mp4")
    def _():
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", m3u8_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", out],
            capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 失敗：{r.stderr.decode()[-300:]}")
        return out
    return await _run(_)

# ── qBittorrent Web API 客戶端 ────────────────────────────────────────────────

TORRENT_SLOW_THRESHOLD = 50 * 1024   # 50 KB/s
TORRENT_SLOW_WINDOW    = 60          # 秒


# 公網 tracker 列表（無入站 IP 時用）
_PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.nitrix.me:443/announce",
    "http://tracker.bt4g.com:2095/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "https://opentracker.i2p.rocks:443/announce",
    "http://tracker.renfei.net:8080/announce",
]

class QBittorrentClient:
    """非同步 qBittorrent Web API 封裝。所有 HTTP 在 executor 裡執行，不阻塞 event loop。"""

    def __init__(self, host: str, username: str, password: str):
        self.host     = host.rstrip("/")
        self.username = username
        self.password = password
        self._cookie  = ""

    def _req(self, method: str, path: str, **kwargs):
        import urllib.request
        import urllib.parse
        url = f"{self.host}/api/v2{path}"
        print(f"[qb] {method} {path}  (cookie={bool(self._cookie)}, has_data={'data' in kwargs})")
        headers = {"Cookie": self._cookie, "Referer": self.host}

        if method == "POST":
            data = kwargs.get("data")
            if isinstance(data, dict):
                if kwargs.get("multipart"):
                    boundary = "----FormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
                    body_parts = []
                    for k, v in data.items():
                        if isinstance(v, bytes):
                            body_parts.append(
                                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="file.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()
                                + v + b"\r\n"
                            )
                        else:
                            body_parts.append(
                                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                            )
                    body_parts.append(f"--{boundary}--\r\n".encode())
                    body = b"".join(body_parts)
                    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
                else:
                    body = urllib.parse.urlencode(data).encode()
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = data or b""
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        else:
            req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[qb] → {r.status} {path}")
            return r.status, r.read().decode("utf-8", errors="ignore"), dict(r.headers)

    async def _call(self, method, path, **kwargs):
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._req(method, path, **kwargs)
        )

    async def login(self):
        status, body, headers = await self._call("POST", "/auth/login",
            data={"username": self.username, "password": self.password})
        if status == 204:
            pass  # 新版 qBittorrent 5.2+ 成功回 204 No Content
        elif body.strip() != "Ok.":
            raise RuntimeError(f"qBittorrent 登入失敗：{body}")
        # set-cookie 可能為大小寫，轉小寫查詢
        raw_headers = {k.lower(): v for k, v in headers.items()}
        cookie_hdr = raw_headers.get("set-cookie", "")
        self._cookie = cookie_hdr.split(";")[0] if cookie_hdr else ""
        print(f"[qb] 登入成功 (status={status}), cookie={self._cookie[:30]}...")

    async def wait_ready(self, retries: int = 30):
        for i in range(retries):
            try:
                await self.login()
                return
            except Exception as e:
                if i == retries - 1:
                    raise RuntimeError(f"qBittorrent 連線失敗：{e}")
                await asyncio.sleep(2)

    async def add_magnet(self, magnet: str, save_path: str) -> str:
        m = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
        info_hash = m.group(1).lower() if m else ""
        # 補 tracker（尚未包含的才加）
        for tr in _PUBLIC_TRACKERS:
            if tr not in magnet:
                magnet += f"&tr={urllib.parse.quote(tr, safe='')}"
        print(f"[qb] add_magnet: {len(_PUBLIC_TRACKERS)} trackers appended, hash={info_hash[:12]}...")
        await self._call("POST", "/torrents/add", data={
            "urls":               magnet,
            "savepath":           save_path,
            "upload_limit":       1024,
            "ratio_limit":        "0",
            "seeding_time_limit": 0,
        })
        return info_hash

    async def add_torrent_file(self, torrent_bytes: bytes, save_path: str) -> str:
        await self._call("POST", "/torrents/add", multipart=True, data={
            "torrents":           torrent_bytes,
            "savepath":           save_path,
            "upload_limit":       1024,
            "ratio_limit":        "0",
            "seeding_time_limit": 0,
        })
        await asyncio.sleep(2)
        _, body, _ = await self._call("GET", "/torrents/info?sort=added_on&reverse=true&limit=1")
        torrents = json.loads(body)
        return torrents[0]["hash"] if torrents else ""

    async def get_torrent(self, info_hash: str) -> dict | None:
        _, body, _ = await self._call("GET", f"/torrents/info?hashes={info_hash}")
        items = json.loads(body)
        return items[0] if items else None

    async def delete_torrent(self, info_hash: str, delete_files: bool = False):
        await self._call("POST", "/torrents/delete", data={
            "hashes":      info_hash,
            "deleteFiles": "true" if delete_files else "false",
        })


async def dl_torrent(
    source: str,
    out_dir: str,
    status_msg,
) -> list[str]:
    """
    透過 qBittorrent Web API 下載種子。
    上傳限速 1 KB/s、ratio 0、做種時間 0，下載完立即移除任務保留檔案。
    全程 await asyncio.sleep 輪詢，不阻塞 event loop。
    """
    os.makedirs(out_dir, exist_ok=True)

    qb = QBittorrentClient(QB_HOST, QB_USER, QB_PASS)
    await qb.wait_ready()

    if source.startswith("magnet:"):
        info_hash = await qb.add_magnet(source, out_dir)
    else:
        with open(source, "rb") as f:
            torrent_bytes = f.read()
        info_hash = await qb.add_torrent_file(torrent_bytes, out_dir)

    if not info_hash:
        raise RuntimeError("無法取得種子 hash，請確認 qBittorrent 已正常運行")

    print(f"[torrent] hash={info_hash} out={out_dir}")

    slow_since:  float | None = None
    warned_slow: bool         = False
    last_edit:   float        = 0.0
    dot_count:   int          = 0
    META_STATES = {"metaDL", "checkingResumeData", "allocating"}

    while True:
        await asyncio.sleep(4)

        t = await qb.get_torrent(info_hash)
        if t is None:
            raise RuntimeError("找不到種子任務，可能已被刪除")

        state      = t.get("state", "")
        dlspeed    = t.get("dlspeed", 0)
        progress   = t.get("progress", 0.0)
        total      = t.get("size", 0)
        downloaded = int(total * progress)
        peers      = t.get("num_seeds", 0) + t.get("num_leechs", 0)
        eta        = t.get("eta", -1)

        if state in ("error", "missingFiles"):
            raise RuntimeError(f"qBittorrent 回報錯誤狀態：{state}")

        # 完成（進入做種或暫停狀態）→ 刪任務保留檔案
        if state in ("uploading", "stalledUP", "forcedUP", "pausedUP", "queuedUP", "checkingUP"):
            await qb.delete_torrent(info_hash, delete_files=False)
            break

        # Metadata 抓取中
        if state in META_STATES or total == 0:
            dot_count = (dot_count + 1) % 4
            now = time.time()
            if now - last_edit >= 4:
                try:
                    await status_msg.edit_text(
                        f"⏳ 正在獲取種子資訊{'.' * (dot_count + 1)}\n"
                        f"🔗 {peers} 個節點"
                    )
                except Exception:
                    pass
                last_edit = now
            continue

        # 實際下載中
        done_mb  = downloaded / 1024 / 1024
        total_mb = total      / 1024 / 1024
        pct      = progress * 100
        size_str = (f"{done_mb/1024:.2f}/{total_mb/1024:.2f} GB"
                    if total_mb >= 1024 else f"{done_mb:.1f}/{total_mb:.1f} MB")
        progress_str = f"{pct:.1f}% ({size_str})"
        speed_str    = _fmt_speed(dlspeed)
        eta_str      = f"{eta//60}m{eta%60:02d}s" if 0 < eta < 86400 else "–"

        # 死種偵測
        if dlspeed < TORRENT_SLOW_THRESHOLD:
            if slow_since is None:
                slow_since = time.time()
            elif not warned_slow and (time.time() - slow_since) > TORRENT_SLOW_WINDOW:
                warned_slow = True
                try:
                    await status_msg.edit_text(
                        f"⚠️ 速度超過 {TORRENT_SLOW_WINDOW}s 低於 {_fmt_speed(TORRENT_SLOW_THRESHOLD)}\n"
                        f"可能為死種，已降至最低優先繼續等待…\n\n"
                        f"📥 {progress_str}　🐢 {speed_str}　🔗 {peers} 節點"
                    )
                except Exception:
                    pass
                last_edit = time.time()
                continue
        else:
            slow_since  = None
            warned_slow = False

        now = time.time()
        if now - last_edit >= 5:
            icon = "🐢" if warned_slow else "⚡"
            try:
                await status_msg.edit_text(
                    f"📥 種子下載中…\n"
                    f"進度：{progress_str}\n"
                    f"速度：{icon} {speed_str}　ETA：{eta_str}\n"
                    f"🔗 {peers} 節點"
                )
            except Exception:
                pass
            last_edit = now

    files = []
    for root, _, fnames in os.walk(out_dir):
        for fn in fnames:
            if not fn.endswith(".!qB"):
                files.append(os.path.join(root, fn))
    return sorted(files)


# ── 種子佇列 worker ───────────────────────────────────────────────────────────

async def _torrent_worker():
    """低優先種子任務 worker：只在沒有其他下載任務時執行。"""
    global _active_tasks
    while True:
        item = await _torrent_queue.get()
        while _active_tasks > 0:
            await asyncio.sleep(2)
        try:
            await item()
        except Exception as e:
            print(f"[torrent_worker] 任務失敗：{e}")
        finally:
            _torrent_queue.task_done()


async def enqueue_torrent(coro_fn):
    await _torrent_queue.put(coro_fn)


# ── Telegram send + cache ─────────────────────────────────────────────────────

_VT = 600
_IT = 120


async def send_cached(update: Update, entry: dict) -> bool:
    msg     = update.message
    caption = entry.get("caption") or ""
    if len(caption) > 1024:
        caption = caption[:1021] + "…"

    async def _send(parse_mode: str | None):
        if entry["type"] == "video":
            await msg.reply_video(
                video=entry["file_id"],
                caption=caption or None,
                parse_mode=parse_mode,
                quote=True,
                read_timeout=60, write_timeout=60, connect_timeout=30,
            )
        else:
            fids = entry["file_ids"]
            for i_chunk, chunk in enumerate([fids[i:i+10] for i in range(0, len(fids), 10)]):
                media = [InputMediaPhoto(
                    media=fid,
                    caption=(caption if i_chunk == 0 and i == 0 and caption else None),
                    parse_mode=(parse_mode if i_chunk == 0 and i == 0 and caption else None),
                ) for i, fid in enumerate(chunk)]
                await msg.reply_media_group(media=media, quote=True,
                    read_timeout=_IT, write_timeout=_IT)

    # 先嘗試 Markdown，失敗改純文字（保留 caption）
    try:
        await _send("Markdown")
        return True
    except Exception as e:
        print(f"[cache] Markdown 失敗，改純文字重試：{e}")
    try:
        await _send(None)
        return True
    except Exception as e2:
        print(f"[cache] 最終轉發失敗：{e2}")
        return False


async def send_video(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     url: str, filepath: str, caption):
    msg = update.message
    key = store_url(url)
    deep_link    = f"https://t.me/{context.bot.username}?start=v{key}"
    full_caption = f"{_escape_md(caption)}\n{deep_link}" if caption else deep_link
    print(f"[video] {os.path.getsize(filepath)/1024/1024:.1f} MB")
    with open(filepath, "rb") as f:
        sent = await msg.reply_video(
            video=f, caption=full_caption, quote=True,
            supports_streaming=True,
            parse_mode="Markdown",
            read_timeout=_VT, write_timeout=_VT, connect_timeout=30,
        )
    fid = sent.video.file_id if sent and sent.video else None
    if fid:
        cache_set(url, {"type": "video", "file_id": fid, "caption": full_caption})


async def send_photos(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      url: str, paths: list, caption):
    msg = update.message
    key = store_url(url)
    deep_link = f"https://t.me/{context.bot.username}?start=v{key}"
    caption   = f"{_escape_md(caption)}\n{deep_link}" if caption else deep_link
    all_fids  = []
    for idx, chunk in enumerate([paths[i:i+10] for i in range(0, len(paths), 10)]):
        media = []
        for i, p in enumerate(chunk):
            with open(p, "rb") as f:
                data = f.read()
            cap = _escape_md(caption) if (idx == 0 and i == 0) else None
            media.append(InputMediaPhoto(media=data, caption=cap, parse_mode="Markdown" if cap else None))
        try:
            sent_msgs = await msg.reply_media_group(media=media, quote=True,
                read_timeout=_IT, write_timeout=_IT)
        except Exception as e:
            print(f"[send_photos] Markdown 失敗，改純文字：{e}")
            # 重試不帶 parse_mode
            media2 = []
            for j, p2 in enumerate(chunk):
                with open(p2, "rb") as f2:
                    data2 = f2.read()
                cap2 = caption if (idx == 0 and j == 0) else None
                media2.append(InputMediaPhoto(media=data2, caption=cap2))
            sent_msgs = await msg.reply_media_group(media=media2, quote=True,
                read_timeout=_IT, write_timeout=_IT)
        for m in sent_msgs:
            if m.photo:
                all_fids.append(m.photo[-1].file_id)
    if all_fids:
        cache_set(url, {"type": "photos", "file_ids": all_fids, "caption": caption})

# ── 統一下載入口（一般 URL）──────────────────────────────────────────────────

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_text: str = "⏳ 下載中…",
    video_only: bool = False,
):
    global _active_tasks
    msg    = update.message
    status = None
    _active_tasks += 1

    try:
        # 1. 快取
        cached = cache_get(url)
        if cached:
            status = await msg.reply_text("⏳ 從快取傳送…", quote=True)
            if await send_cached(update, cached):
                await status.delete()
                return
            else:
                await status.edit_text("⚠️ 快取暫時無法發送，請再按一次連結")
                return

        # 2. TikTok 圖集直接走 gallery-dl
        if TIKTOK_PHOTO_RE.search(url):
            status = await msg.reply_text("⏳ 下載 TikTok 圖集…", quote=True)
            try:
                with tempfile.TemporaryDirectory() as d:
                    title, kind, result = await dl_gallery(url, d)
                    if kind == "video":
                        await send_video(update, context, url, result, f"🎬 {title}" if title else None)
                    else:
                        await send_photos(update, context, url, result, f"🖼️ {title}" if title else None)
                await status.delete()
            except Exception as ge:
                print(f"[dl] gallery-dl failed for photo URL: {ge}")
                await status.edit_text("❌ TikTok 圖集下載失敗")
            return

        status = await msg.reply_text(status_text, quote=True)

        # 3. 嘗試影片
        video_err = None
        try:
            with tempfile.TemporaryDirectory() as d:
                title, fp = await dl_video(url, d)
                await send_video(update, context, url, fp, f"🎬 {title}" if title else None)
            await status.delete()
            return
        except Exception as e:
            video_err = e
            print(f"[dl] video failed: {e}")
            err_str = str(e)
            if video_only:
                if "Requested format is not available" in err_str:
                    await status.edit_text("❌ 無可用格式（可能是地區限制、年齡限制或需要登入）")
                    return
                if "Private video" in err_str:
                    await status.edit_text("❌ 私人影片，無法下載")
                    return
                if "members-only" in err_str.lower():
                    await status.edit_text("❌ 會員限定影片，無法下載")
                    return
            await status.edit_text("⏳ 嘗試下載圖片/媒體…")

        # 4. 任意媒體
        any_err = None
        try:
            with tempfile.TemporaryDirectory() as d:
                title, kind, result = await dl_any(url, d)
                if kind == "video":
                    await send_video(update, context, url, result, f"🎬 {title}" if title else None)
                else:
                    await send_photos(update, context, url, result, f"🖼️ {title}" if title else None)
            await status.delete()
            return
        except Exception as e:
            any_err = e
            print(f"[dl] any-media failed: {e}")

        # 4.5 Twitter 圖片 fallback
        is_twitter = bool(TWITTER_RE.search(url))
        if is_twitter and "No video could be found" in str(video_err):
            try:
                await status.edit_text("⏳ 推文無影片，嘗試下載圖片…")
                with tempfile.TemporaryDirectory() as d:
                    title, kind, result = await dl_twitter_photos(url, d)
                    await send_photos(update, context, url, result, f"🖼️ {title}")
                await status.delete()
                return
            except Exception as te:
                print(f"[dl] twitter photos also failed: {te}")

        # 4.6 抖音 fallback（cookies 不足時走公開 API）
        is_douyin = bool(DOUYIN_RE.search(url))
        if is_douyin and ("cookies" in str(video_err).lower() or "cookies" in str(any_err).lower()):
            try:
                await status.edit_text("⏳ 抖音需要 cookies，嘗試替代 API…")
                with tempfile.TemporaryDirectory() as d:
                    title, kind, result = await dl_douyin(url, d)
                    await send_video(update, context, url, result, f"🎬 {title}" if title else None)
                await status.delete()
                return
            except Exception as de:
                print(f"[dl] douyin API also failed: {de}")

        # 5. gallery-dl fallback（TikTok 短連結）
        is_tiktok     = bool(TIKTOK_RE.search(url))
        err_has_photo = "photo" in str(any_err).lower() or "photo" in str(video_err).lower()
        if is_tiktok and (err_has_photo or "Unsupported URL" in str(any_err)):
            try:
                await status.edit_text("⏳ 嘗試用 gallery-dl 下載…")
                with tempfile.TemporaryDirectory() as d:
                    title, kind, result = await dl_gallery(url, d)
                    if kind == "video":
                        await send_video(update, context, url, result, f"🎬 {title}" if title else None)
                    else:
                        await send_photos(update, context, url, result, f"🖼️ {title}" if title else None)
                await status.delete()
                return
            except Exception as ge:
                print(f"[dl] gallery-dl also failed: {ge}")

        # 6. m3u8 fallback
        if not is_tiktok:
            await status.edit_text("⏳ 嘗試提取 m3u8…")
            try:
                m3u8_url = await fetch_m3u8(url)
                if not m3u8_url:
                    hint = f"\n`{video_err}`" if video_err else ""
                    await status.edit_text(f"❌ 找不到可下載的媒體{hint}")
                    return
                await status.edit_text("⏳ 找到 m3u8，下載中…")
                with tempfile.TemporaryDirectory() as d:
                    fp = await dl_m3u8(m3u8_url, d)
                    await send_video(update, context, url, fp, f"🎬 {url}")
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ 下載失敗：{e}")
        else:
            await status.edit_text("❌ 找不到可下載的媒體")

    except Exception as e:
        print(f"[handle_url] 未預期錯誤：{traceback.format_exc()}")
        error_msg = f"❌ 處理失敗：{e}" if str(e) else "❌ 處理失敗"
        try:
            if status:
                await status.edit_text(error_msg)
            else:
                await msg.reply_text(error_msg, quote=True)
        except Exception:
            pass
    finally:
        _active_tasks = max(0, _active_tasks - 1)


# ── 種子下載入口 ──────────────────────────────────────────────────────────────

async def handle_torrent_source(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source: str,
    display_name: str = "",
):
    msg        = update.message
    label      = display_name or (source[:60] + "…" if len(source) > 60 else source)
    queue_size = _torrent_queue.qsize()
    wait_note  = f"（前方還有 {queue_size} 個任務等待）" if queue_size > 0 or _active_tasks > 0 else ""
    status = await msg.reply_text(
        f"🧲 已排入種子下載佇列{wait_note}\n`{label}`",
        quote=True, parse_mode="Markdown",
    )

    async def _task():
        try:
            await status.edit_text(f"📥 開始下載種子：\n`{label}`", parse_mode="Markdown")
            out_dir = os.path.join(TORRENT_DIR, hashlib.md5(source.encode()).hexdigest()[:8])
            files   = await dl_torrent(source, out_dir, status)

            if not files:
                await status.edit_text("❌ 種子下載完成但找不到檔案")
                return

            total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
            size_str   = (f"{total_size/1024**3:.2f} GB"
                          if total_size >= 1024**3 else f"{total_size/1024/1024:.1f} MB")
            file_list  = "\n".join(f"• {os.path.basename(f)}" for f in files[:10])
            if len(files) > 10:
                file_list += f"\n…及 {len(files)-10} 個更多檔案"

            await status.edit_text(
                f"✅ 種子下載完成！\n"
                f"📦 {len(files)} 個檔案，共 {size_str}\n{file_list}"
            )
        except Exception as e:
            print(f"[torrent] 下載失敗：{traceback.format_exc()}")
            try:
                await status.edit_text(f"❌ 種子下載失敗：{e}")
            except Exception:
                pass

    await enqueue_torrent(_task)


async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".torrent"):
        return
    status = await update.message.reply_text("⏳ 正在接收種子檔案…", quote=True)
    try:
        tfile = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
            await tfile.download_to_drive(tmp.name)
            torrent_path = tmp.name
        await status.delete()
        await handle_torrent_source(update, context, torrent_path, doc.file_name)
    except Exception as e:
        await status.edit_text(f"❌ 種子檔案接收失敗：{e}")


# ── /start handler ────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    args = context.args
    if not args or not args[0].startswith("v"):
        await update.message.reply_text("👋 傳我影片連結或磁力連結，我幫你下載！")
        return
    key = args[0][1:].upper()
    url = retrieve_url(key)
    if not url:
        await update.message.reply_text("❌ 連結已過期或無效。")
        return
    platform = "unknown"
    for pattern, p in [
        (BILIBILI_RE, "bilibili"), (YOUTUBE_RE, "youtube"),
        (TIKTOK_RE, "tiktok"),    (TWITTER_RE, "twitter"),
        (XIAOHONGSHU_RE, "小紅書"), (DOUYIN_RE, "抖音"),
    ]:
        if pattern.search(url):
            platform = p
            break
    await handle_url(update, context, url,
        status_text=f"⏳ 下載 {platform} 內容…",
        video_only=(platform in ("bilibili", "youtube")))

# ── Main message handler ──────────────────────────────────────────────────────

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if update.message.document:
        doc = update.message.document
        if doc.file_name and doc.file_name.lower().endswith(".torrent"):
            await handle_torrent_file(update, context)
        return

    if not update.message.text:
        return

    text         = update.message.text
    bot_username = context.bot.username
    is_private   = (update.effective_chat.type == "private")
    is_mentioned = (
        update.message.entities and
        any(e.type == "mention" for e in update.message.entities) and
        f"@{bot_username}" in text
    )

    async def safe_handle_url(url, platform=None, status_text=None):
        try:
            kwargs = {"update": update, "context": context, "url": url}
            if status_text:
                kwargs["status_text"] = status_text
            if platform in ("bilibili", "youtube"):
                kwargs["video_only"] = True
            await handle_url(**kwargs)
        except Exception:
            print(f"[message_handler] 任務失敗 {url}：{traceback.format_exc()}")

    async def safe_handle_magnet(magnet):
        try:
            await handle_torrent_source(update, context, magnet)
        except Exception:
            print(f"[message_handler] 磁力失敗：{traceback.format_exc()}")

    tasks = []

    if is_private:
        for magnet in extract_magnets(text):
            tasks.append(safe_handle_magnet(magnet))
        for url, platform in extract_known_urls(text):
            tasks.append(safe_handle_url(url, platform, f"⏳ 下載 {platform} 內容…"))
        for url in extract_generic_urls(text):
            tasks.append(safe_handle_url(url))
    else:
        if is_mentioned:
            clean = re.sub(rf"@{re.escape(bot_username)}", "", text).strip()
            for magnet in extract_magnets(clean):
                tasks.append(safe_handle_magnet(magnet))
            for url, platform in extract_known_urls(clean):
                tasks.append(safe_handle_url(url, platform, f"⏳ 下載 {platform} 內容…"))
            for url in extract_generic_urls(clean):
                tasks.append(safe_handle_url(url))
        else:
            for url, platform in extract_known_urls(text):
                tasks.append(safe_handle_url(url, platform, f"⏳ 下載 {platform} 內容…"))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global _torrent_queue

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN 環境變數未設置！")

    print(f"🍪 Cookie：{'已載入 ' + COOKIES_FILE if os.path.isfile(COOKIES_FILE) else '未找到 ' + COOKIES_FILE}")
    print(f"💾 快取：{CACHE_FILE}（{len(_cache)} 筆）")
    print(f"📂 種子下載目錄：{TORRENT_DIR}")
    print(f"🌊 qBittorrent：{QB_HOST}（user={QB_USER}）")

    if shutil.which("gallery-dl"):
        print("🖼️  gallery-dl：已安裝")
    else:
        print("⚠️  gallery-dl：未安裝（TikTok 圖集將無法下載）")

    os.makedirs(TORRENT_DIR, exist_ok=True)

    builder = ApplicationBuilder().token(BOT_TOKEN)
    if LOCAL_API_URL:
        print(f"🔗 本地 API：{LOCAL_API_URL}")
        builder = (builder
            .local_mode(True)
            .base_url(f"{LOCAL_API_URL}/bot")
            .base_file_url(f"{LOCAL_API_URL}/file/bot"))
    else:
        print("⚠️  LOCAL_API_URL 未設置，使用官方 API（上限 50 MB）")

    app = builder.build()

    _torrent_queue = asyncio.Queue()

    async def _post_init(application):
        asyncio.create_task(_torrent_worker())

    app.post_init = _post_init
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, message_handler))

    print("🤖 Bot 已啟動，監聽中…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
