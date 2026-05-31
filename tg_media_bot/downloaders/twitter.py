import os

from ..patterns import _parse_tweet_id
from .common import run_blocking
from .yt_dlp import dl_any, dl_video

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

    return await run_blocking(_)


async def dl_twitter(url: str, tmpdir: str):
    video_err = None
    try:
        title, path = await dl_video(url, tmpdir)
        return title, "video", path
    except Exception as e:
        video_err = e

    try:
        return await dl_any(url, tmpdir)
    except Exception:
        pass

    if "no video could be found" in str(video_err).lower():
        return await dl_twitter_photos(url, tmpdir)

    raise video_err

