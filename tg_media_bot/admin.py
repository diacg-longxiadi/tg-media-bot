import asyncio
import json
import os
import urllib.parse
import urllib.request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from . import state
from .config import ADMIN_PASSWORD, QB_HOST, QB_USER, QB_PASS

_authed_users: set[int] = set()
_torrent_owner: dict[str, int] = {}
_failed_attempts: dict[int, int] = {}  # user_id -> count


async def _login_qb():
    data = urllib.parse.urlencode({"username": QB_USER, "password": QB_PASS}).encode()
    req = urllib.request.Request(f"{QB_HOST}/api/v2/auth/login", data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        return r.headers.get("Set-Cookie", "").split(";")[0]


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _authed_users:
        context.user_data["pending_cmd"] = "list_handler"
        context.user_data["pending_args"] = []
        await update.message.reply_text("🔒 請輸入管理密碼：")
        return
    lines = ["📋 所有進行中任務："]
    idx = 0

    # 1) 下載中任務（從 state）
    for t in state.get_all_tasks():
        idx += 1
        if t["type"] == "download":
            lines.append(f"{idx}. ⬇️ [下載] {t['name']}")
        elif t["type"] == "torrent":
            lines.append(f"{idx}. 🌱 [種子] {t['name']}")
        else:
            lines.append(f"{idx}. ❓ {t['name']}")

    # 2) qBittorrent 種子（未在 state 中的）
    try:
        cookie = await _login_qb()
        req = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/info",
                                     headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as r:
            qb_torrents = json.loads(r.read())
        for t in qb_torrents:
            idx += 1
            name = t.get("name", "?")[:50]
            state_s = t.get("state", "?")
            pct = t.get("progress", 0) * 100
            dl = t.get("dlspeed", 0) // 1024
            lines.append(f"{idx}. 🌱 [{state_s}] {pct:.0f}%  {name}")
            lines.append(f"    ↓{dl}KB/s")
    except Exception as e:
        lines.append(f"  (qBittorrent 連線失敗：{e})")

    if idx == 0:
        lines.append("  目前沒有進行中的任務")

    msg = "\n".join(lines)
    if len(msg) <= 4000:
        await update.message.reply_text(msg)
    else:
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await update.message.reply_text(chunk)


async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _authed_users:
        context.user_data["pending_cmd"] = "stop_handler"
        context.user_data["pending_args"] = context.args or []
        await update.message.reply_text("🔒 請輸入管理密碼：")
        return
    if not context.args:
        await update.message.reply_text("用法：/stop <任務編號>")
        return

    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("請輸入數字編號")
        return

    # 建立編號→任務的映射
    all_downloads = state.get_all_tasks()
    tasks = {}
    idx = 0
    for t in all_downloads:
        idx += 1
        tasks[idx] = ("download", t)

    cookie = ""
    qb_list = []
    try:
        cookie = await _login_qb()
        req = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/info",
                                     headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as r:
            qb_list = json.loads(r.read())
        for t in qb_list:
            idx += 1
            tasks[idx] = ("torrent", t)
    except Exception:
        qb_list = []

    if target not in tasks:
        await update.message.reply_text(f"無效編號，目前共 {idx} 個任務")
        return

    kind, item = tasks[target]

    if kind == "download":
        # 取消下載 task
        tid = item["id"]
        name = item["name"]
        state.cancel_task(tid)
        await update.message.reply_text(f"⏸ 已取消下載：{name}")

    elif kind == "torrent":
        h = item["hash"]
        name = item.get("name", "?")[:60]
        # 暫停
        d = urllib.parse.urlencode({"hashes": h}).encode()
        r2 = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/stop",
                                    data=d, headers={"Cookie": cookie}, method="POST")
        urllib.request.urlopen(r2)
        await update.message.reply_text(f"⏸ 已暫停種子：{name}")

        # 通知原始使用者
        owner_id = _torrent_owner.get(h)
        if owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"⏸ 你的種子下載已被管理員停止：\n`{name}`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                print(f"[admin] 無法通知使用者 {owner_id}：{e}")


async def dal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """刪除（取消）任務"""
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _authed_users:
        context.user_data["pending_cmd"] = "dal_handler"
        context.user_data["pending_args"] = context.args or []
        await update.message.reply_text("🔒 請輸入管理密碼：")
        return
    if not context.args:
        await update.message.reply_text("用法：/dal <任務編號>\n用 /list 查看任務編號")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("請輸入數字編號")
        return

    all_downloads = state.get_all_tasks()
    tasks = {}
    idx = 0
    for t in all_downloads:
        idx += 1
        tasks[idx] = ("download", t)

    cookie = ""
    qb_list = []
    try:
        cookie = await _login_qb()
        req = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/info",
                                     headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as r:
            qb_list = json.loads(r.read())
        for t in qb_list:
            idx += 1
            tasks[idx] = ("torrent", t)
    except Exception:
        qb_list = []

    if target not in tasks:
        await update.message.reply_text(f"無效編號，目前共 {idx} 個任務")
        return

    kind, item = tasks[target]
    if kind == "download":
        tid = item["id"]
        name = item["name"]
        state.cancel_task(tid)
        await update.message.reply_text(f"🗑 已刪除下載：{name}")
    elif kind == "torrent":
        h = item["hash"]
        name = item.get("name", "?")[:60]
        d = urllib.parse.urlencode({"hashes": h, "deleteFiles": "false"}).encode()
        r2 = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/delete",
                                     data=d, headers={"Cookie": cookie}, method="POST")
        urllib.request.urlopen(r2)
        await update.message.reply_text(f"🗑 已刪除種子（保留檔案）：{name}")
        owner_id = _torrent_owner.get(h)
        if owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"🗑 你的種子下載已被管理員刪除（保留檔案）：\n`{name}`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                print(f"[admin] 無法通知使用者 {owner_id}：{e}")


async def go_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """恢復已暫停的下載"""
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _authed_users:
        context.user_data["pending_cmd"] = "go_handler"
        context.user_data["pending_args"] = context.args or []
        await update.message.reply_text("🔒 請輸入管理密碼：")
        return

    cookie = ""
    try:
        cookie = await _login_qb()
    except Exception as e:
        await update.message.reply_text(f"qBittorrent 連線失敗：{e}")
        return

    req = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/info",
                                 headers={"Cookie": cookie})
    with urllib.request.urlopen(req) as r:
        qb_list = json.loads(r.read())

    paused = [t for t in qb_list if t.get("state") in ("pausedDL", "stoppedDL", "pausedUP", "stoppedUP")]

    if not context.args:
        if not paused:
            await update.message.reply_text("目前沒有暫停的任務")
            return
        lines = ["⏸ 已暫停的種子："]
        for i, t in enumerate(paused, 1):
            name = t.get("name", "?")[:50]
            state_s = t.get("state", "?")
            lines.append(f"{i}. [{state_s}] {name}")
        await update.message.reply_text("\n".join(lines))
        return

    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("請輸入數字編號")
        return

    if target < 1 or target > len(paused):
        await update.message.reply_text(f"無效編號，目前共 {len(paused)} 個暫停任務")
        return

    t = paused[target - 1]
    h = t["hash"]
    name = t.get("name", "?")[:60]
    d = urllib.parse.urlencode({"hashes": h}).encode()
    r2 = urllib.request.Request(f"{QB_HOST}/api/v2/torrents/start",
                                data=d, headers={"Cookie": cookie}, method="POST")
    urllib.request.urlopen(r2)
    await update.message.reply_text(f"▶️ 已恢復下載：{name}")

    owner_id = _torrent_owner.get(h)
    if owner_id:
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=f"▶️ 你的種子下載已恢復：\n`{name}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            print(f"[admin] 無法通知使用者 {owner_id}：{e}")


async def library_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _authed_users:
        context.user_data["pending_cmd"] = "library_handler"
        context.user_data["pending_args"] = []
        await update.message.reply_text("🔒 請輸入管理密碼：")
        return

    # 建立平台索引
    platforms = _build_platform_index()
    if not platforms:
        await update.message.reply_text("尚無下載紀錄")
        return

    buttons = []
    for plat in sorted(platforms.keys()):
        count = len(platforms[plat])
        buttons.append([InlineKeyboardButton(f"{plat} ({count})", callback_data=f"lib:{plat}:0")])

    await update.message.reply_text(
        "📂 歡迎管理員\n選擇平台查看已下載內容：",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def _build_platform_index() -> dict[str, list]:
    """從 cache 建立平台→項目列表"""
    from .cache import _cache
    platforms = {}
    for url, entry in _cache.items():
        if url == "__url_store__":
            continue
        if not isinstance(entry, dict):
            continue
        plat = _detect_platform(url)
        cap = entry.get("caption", "")[:60] if entry.get("caption") else ""
        t = entry.get("type", "?")
        platforms.setdefault(plat, []).append(f"  [{t}] {cap or url[:50]}")
    return platforms


def _detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    if "bilibili.com" in url or "b23.tv" in url:
        return "Bilibili"
    if "tiktok.com" in url:
        return "TikTok"
    if "twitter.com" in url or "x.com" in url:
        return "Twitter/X"
    if "douyin.com" in url or "iesdouyin" in url:
        return "抖音"
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return "小紅書"
    if "instagram.com" in url:
        return "Instagram"
    if "pixiv" in url:
        return "Pixiv"
    return "unknown"


async def library_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理平台選擇和翻頁"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("lib:"):
        return

    parts = data.split(":")
    if len(parts) < 3:
        return

    plat = parts[1]
    if plat == "__back__":
        # 返回平台列表
        platforms = _build_platform_index()
        buttons = []
        for p in sorted(platforms.keys()):
            count = len(platforms[p])
            buttons.append([InlineKeyboardButton(f"{p} ({count})", callback_data=f"lib:{p}:0")])
        await query.edit_message_text(
            "📂 歡迎管理員\n選擇平台查看已下載內容：",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    platforms = _build_platform_index()
    items = platforms.get(plat, [])
    total = len(items)
    per_page = 10
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    lines = [f"📂 {plat}（第 {page+1}/{total_pages} 頁，共 {total} 筆）\n"]
    for item in page_items:
        lines.append(item)

    # 頁碼按鈕
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ 上一頁", callback_data=f"lib:{plat}:{page-1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("下一頁 ➡️", callback_data=f"lib:{plat}:{page+1}"))
    nav_buttons = [row] if row else []

    # 返回平台列表
    back_row = [InlineKeyboardButton("🔙 返回平台列表", callback_data="lib:__back__:0")]

    keyboard = nav_buttons + [back_row]
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
