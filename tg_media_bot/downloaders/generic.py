from .stream import dl_m3u8, fetch_m3u8
from .yt_dlp import dl_any, dl_video


async def dl_generic(url: str, tmpdir: str):
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

    m3u8_url = await fetch_m3u8(url)
    if not m3u8_url:
        raise video_err
    path = await dl_m3u8(m3u8_url, tmpdir)
    return url, "video", path
