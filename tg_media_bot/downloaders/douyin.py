import json
import os
import re

from .common import run_blocking
from .yt_dlp import dl_any, dl_video

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

    return await run_blocking(_)


async def dl_douyin_media(url: str, tmpdir: str):
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
    if "cookies" in err_text:
        return await dl_douyin(url, tmpdir)

    raise any_err or video_err

