import os
import subprocess

from ..config import COOKIES_FILE
from .common import run_blocking

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
    return await run_blocking(_)

