from .yt_dlp import dl_video


async def dl_bilibili(url: str, tmpdir: str):
    title, path = await dl_video(url, tmpdir)
    return title, "video", path
