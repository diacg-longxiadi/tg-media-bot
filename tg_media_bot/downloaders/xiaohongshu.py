from .yt_dlp import dl_any, dl_video


async def dl_xiaohongshu(url: str, tmpdir: str):
    try:
        title, path = await dl_video(url, tmpdir)
        return title, "video", path
    except Exception:
        return await dl_any(url, tmpdir)
