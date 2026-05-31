from .gallery import dl_gallery
from .yt_dlp import dl_any, dl_video


async def dl_tiktok(url: str, tmpdir: str, photo_only: bool = False):
    if photo_only:
        return await dl_gallery(url, tmpdir)

    video_err = None
    try:
        title, path = await dl_video(url, tmpdir)
        return title, "video", path
    except Exception as e:
        video_err = e

    any_err = None
    try:
        return await dl_any(url, tmpdir)
    except Exception as e:
        any_err = e

    err_text = f"{video_err} {any_err}".lower()
    if "photo" in err_text or "unsupported url" in err_text:
        return await dl_gallery(url, tmpdir)

    raise any_err or video_err
