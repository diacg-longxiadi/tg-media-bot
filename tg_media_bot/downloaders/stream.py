import os
import re
import subprocess

from .common import run_blocking

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
    return await run_blocking(_)

async def dl_m3u8(m3u8_url: str, tmpdir: str) -> str:
    out = os.path.join(tmpdir, "output.mp4")
    def _():
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", m3u8_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", out],
            capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 失敗：{r.stderr.decode()[-300:]}")
        return out
    return await run_blocking(_)

