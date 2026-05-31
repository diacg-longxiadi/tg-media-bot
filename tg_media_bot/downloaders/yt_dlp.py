import glob
import os

import yt_dlp

from ..config import COOKIES_FILE
from .common import run_blocking

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

    return await run_blocking(_)


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

    return await run_blocking(_)

