import os

from telegram import InputMediaPhoto
from telegram.ext import ContextTypes
from telegram import Update

from .cache import cache_set, store_url
from .utils import _escape_md

_VT = 600
_IT = 120


async def send_cached(update: Update, entry: dict) -> bool:
    msg     = update.message
    caption = entry.get("caption") or ""
    if len(caption) > 1024:
        caption = caption[:1021] + "…"

    async def _send(parse_mode: str | None):
        if entry["type"] == "video":
            await msg.reply_video(
                video=entry["file_id"],
                caption=caption or None,
                parse_mode=parse_mode,
                quote=True,
                read_timeout=60, write_timeout=60, connect_timeout=30,
            )
        else:
            fids = entry["file_ids"]
            for i_chunk, chunk in enumerate([fids[i:i+10] for i in range(0, len(fids), 10)]):
                media = [InputMediaPhoto(
                    media=fid,
                    caption=(caption if i_chunk == 0 and i == 0 and caption else None),
                    parse_mode=(parse_mode if i_chunk == 0 and i == 0 and caption else None),
                ) for i, fid in enumerate(chunk)]
                await msg.reply_media_group(media=media, quote=True,
                    read_timeout=_IT, write_timeout=_IT)

    # 先嘗試 Markdown，失敗改純文字（保留 caption）
    try:
        await _send("Markdown")
        return True
    except Exception as e:
        print(f"[cache] Markdown 失敗，改純文字重試：{e}")
    try:
        await _send(None)
        return True
    except Exception as e2:
        print(f"[cache] 最終轉發失敗：{e2}")
        return False


async def send_video(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     url: str, filepath: str, caption):
    msg = update.message
    key = store_url(url)
    deep_link    = f"https://t.me/{context.bot.username}?start=v{key}"
    full_caption = f"{_escape_md(caption)}\n{_escape_md(deep_link)}" if caption else _escape_md(deep_link)
    print(f"[video] {os.path.getsize(filepath)/1024/1024:.1f} MB")
    with open(filepath, "rb") as f:
        sent = await msg.reply_video(
            video=f, caption=full_caption, quote=True,
            supports_streaming=True,
            parse_mode="Markdown",
            read_timeout=_VT, write_timeout=_VT, connect_timeout=30,
        )
    fid = sent.video.file_id if sent and sent.video else None
    if fid:
        cache_set(url, {"type": "video", "file_id": fid, "caption": full_caption})


async def send_photos(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      url: str, paths: list, caption):
    msg = update.message
    key = store_url(url)
    deep_link = f"https://t.me/{context.bot.username}?start=v{key}"
    caption   = f"{_escape_md(caption)}\n{_escape_md(deep_link)}" if caption else _escape_md(deep_link)
    all_fids  = []
    for idx, chunk in enumerate([paths[i:i+10] for i in range(0, len(paths), 10)]):
        media = []
        for i, p in enumerate(chunk):
            with open(p, "rb") as f:
                data = f.read()
            cap = _escape_md(caption) if (idx == 0 and i == 0) else None
            media.append(InputMediaPhoto(media=data, caption=cap, parse_mode="Markdown" if cap else None))
        try:
            sent_msgs = await msg.reply_media_group(media=media, quote=True,
                read_timeout=_IT, write_timeout=_IT)
        except Exception as e:
            print(f"[send_photos] Markdown 失敗，改純文字：{e}")
            # 重試不帶 parse_mode
            media2 = []
            for j, p2 in enumerate(chunk):
                with open(p2, "rb") as f2:
                    data2 = f2.read()
                cap2 = caption if (idx == 0 and j == 0) else None
                media2.append(InputMediaPhoto(media=data2, caption=cap2))
            sent_msgs = await msg.reply_media_group(media=media2, quote=True,
                read_timeout=_IT, write_timeout=_IT)
        for m in sent_msgs:
            if m.photo:
                all_fids.append(m.photo[-1].file_id)
    if all_fids:
        cache_set(url, {"type": "photos", "file_ids": all_fids, "caption": caption})

# ── 統一下載入口（一般 URL）──────────────────────────────────────────────────
