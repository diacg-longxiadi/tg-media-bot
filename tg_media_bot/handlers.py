import asyncio
import hashlib
import os
import re
import tempfile
import traceback

from telegram import Update
from telegram.ext import ContextTypes

from . import state
from .cache import cache_get, retrieve_url
from .config import TORRENT_DIR
from .downloaders.bilibili import dl_bilibili
from .downloaders.douyin import dl_douyin_media
from .downloaders.generic import dl_generic
from .downloaders.tiktok import dl_tiktok
from .downloaders.twitter import dl_twitter
from .downloaders.youtube import dl_youtube
from .downloaders.xiaohongshu import dl_xiaohongshu
from .patterns import (
    BILIBILI_RE,
    DOUYIN_RE,
    TIKTOK_PHOTO_RE,
    TIKTOK_RE,
    TWITTER_RE,
    XIAOHONGSHU_RE,
    YOUTUBE_RE,
    extract_generic_urls,
    extract_known_urls,
    extract_magnets,
)
from .telegram_sender import send_cached, send_photos, send_video
from .torrent import dl_torrent, enqueue_torrent


def _platform_for_url(url: str) -> str:
    for pattern, platform in [
        (YOUTUBE_RE, "youtube"),
        (BILIBILI_RE, "bilibili"),
        (TIKTOK_RE, "tiktok"),
        (TWITTER_RE, "twitter"),
        (XIAOHONGSHU_RE, "xiaohongshu"),
        (DOUYIN_RE, "douyin"),
    ]:
        if pattern.search(url):
            return platform
    return "generic"


async def _download_by_platform(platform: str, url: str, tmpdir: str):
    if platform == "youtube":
        return await dl_youtube(url, tmpdir)
    if platform == "bilibili":
        return await dl_bilibili(url, tmpdir)
    if platform == "tiktok":
        return await dl_tiktok(url, tmpdir, photo_only=bool(TIKTOK_PHOTO_RE.search(url)))
    if platform == "twitter":
        return await dl_twitter(url, tmpdir)
    if platform == "xiaohongshu":
        return await dl_xiaohongshu(url, tmpdir)
    if platform == "douyin":
        return await dl_douyin_media(url, tmpdir)
    return await dl_generic(url, tmpdir)


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    status_text: str = "下載中...",
    video_only: bool = False,
):
    msg = update.message
    status = None
    state.active_tasks += 1

    try:
        cached = cache_get(url)
        if cached:
            status = await msg.reply_text("從快取傳送...", quote=True)
            if await send_cached(update, cached):
                await status.delete()
                return
            await status.edit_text("快取暫時無法傳送，請再送一次連結")
            return

        platform = _platform_for_url(url)
        status = await msg.reply_text(status_text, quote=True)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                title, kind, result = await _download_by_platform(platform, url, tmpdir)
                caption = f"🎬 {title}" if title else None
                if kind == "video":
                    await send_video(update, context, url, result, caption)
                else:
                    await send_photos(update, context, url, result, caption)
            await status.delete()
        except Exception as e:
            err_text = str(e)
            if video_only:
                lower = err_text.lower()
                if "requested format is not available" in lower:
                    await status.edit_text("找不到可用格式，可能是地區限制、年齡限制或需要登入")
                    return
                if "private video" in lower:
                    await status.edit_text("私人影片，無法下載")
                    return
                if "members-only" in lower:
                    await status.edit_text("會員限定影片，無法下載")
                    return
            await status.edit_text(f"下載失敗：{e}")

    except Exception as e:
        print(f"[handle_url] unexpected error: {traceback.format_exc()}")
        error_msg = f"處理失敗：{e}" if str(e) else "處理失敗"
        try:
            if status:
                await status.edit_text(error_msg)
            else:
                await msg.reply_text(error_msg, quote=True)
        except Exception:
            pass
    finally:
        state.active_tasks = max(0, state.active_tasks - 1)


async def handle_torrent_source(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source: str,
    display_name: str = "",
):
    msg = update.message
    label = display_name or (source[:60] + "..." if len(source) > 60 else source)
    queue_size = state.get_torrent_queue().qsize()
    wait_note = f"（前方還有 {queue_size} 個任務等待）" if queue_size > 0 or state.active_tasks > 0 else ""
    status = await msg.reply_text(
        f"已排入種子下載佇列{wait_note}\n`{label}`",
        quote=True,
        parse_mode="Markdown",
    )

    async def _task():
        try:
            await status.edit_text(f"開始下載種子：\n`{label}`", parse_mode="Markdown")
            out_dir = os.path.join(TORRENT_DIR, hashlib.md5(source.encode()).hexdigest()[:8])
            files = await dl_torrent(source, out_dir, status)

            if not files:
                await status.edit_text("種子下載完成，但找不到檔案")
                return

            total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
            size_str = (
                f"{total_size/1024**3:.2f} GB"
                if total_size >= 1024**3
                else f"{total_size/1024/1024:.1f} MB"
            )
            file_list = "\n".join(f"- {os.path.basename(f)}" for f in files[:10])
            if len(files) > 10:
                file_list += f"\n...以及 {len(files)-10} 個更多檔案"

            await status.edit_text(
                f"種子下載完成\n"
                f"{len(files)} 個檔案，共 {size_str}\n{file_list}"
            )
        except Exception as e:
            print(f"[torrent] download failed: {traceback.format_exc()}")
            try:
                await status.edit_text(f"種子下載失敗：{e}")
            except Exception:
                pass

    await enqueue_torrent(_task)


async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".torrent"):
        return
    status = await update.message.reply_text("正在接收種子檔案...", quote=True)
    try:
        tfile = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tmp:
            await tfile.download_to_drive(tmp.name)
            torrent_path = tmp.name
        await status.delete()
        await handle_torrent_source(update, context, torrent_path, doc.file_name)
    except Exception as e:
        await status.edit_text(f"種子檔案接收失敗：{e}")


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    args = context.args
    if not args or not args[0].startswith("v"):
        await update.message.reply_text("傳我影片連結、媒體連結、磁力連結或 .torrent 檔，我幫你下載。")
        return
    key = args[0][1:].upper()
    url = retrieve_url(key)
    if not url:
        await update.message.reply_text("連結已過期或無效。")
        return
    platform = _platform_for_url(url)
    await handle_url(
        update,
        context,
        url,
        status_text=f"下載 {platform} 內容...",
        video_only=(platform in ("bilibili", "youtube")),
    )


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

    text = update.message.text
    bot_username = context.bot.username
    is_private = update.effective_chat.type == "private"
    is_mentioned = (
        update.message.entities
        and any(e.type == "mention" for e in update.message.entities)
        and f"@{bot_username}" in text
    )

    async def safe_handle_url(url, platform=None, status_text=None):
        try:
            await handle_url(
                update=update,
                context=context,
                url=url,
                status_text=status_text or "下載中...",
                video_only=(platform in ("bilibili", "youtube")),
            )
        except Exception:
            print(f"[message_handler] url task failed {url}: {traceback.format_exc()}")

    async def safe_handle_magnet(magnet):
        try:
            await handle_torrent_source(update, context, magnet)
        except Exception:
            print(f"[message_handler] magnet task failed: {traceback.format_exc()}")

    tasks = []

    if is_private:
        source_text = text
        include_generic = True
    elif is_mentioned:
        source_text = re.sub(rf"@{re.escape(bot_username)}", "", text).strip()
        include_generic = True
    else:
        source_text = text
        include_generic = False

    if is_private or is_mentioned:
        for magnet in extract_magnets(source_text):
            tasks.append(safe_handle_magnet(magnet))

    for url, platform in extract_known_urls(source_text):
        tasks.append(safe_handle_url(url, platform, f"下載 {platform} 內容..."))

    if include_generic:
        for url in extract_generic_urls(source_text):
            tasks.append(safe_handle_url(url))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
