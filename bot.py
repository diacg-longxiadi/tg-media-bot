import asyncio
import os
import shutil

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from tg_media_bot import state
from tg_media_bot.cache import _cache
from tg_media_bot.config import BOT_TOKEN, CACHE_FILE, COOKIES_FILE, LOCAL_API_URL, QB_HOST, QB_USER, TORRENT_DIR
from tg_media_bot.handlers import handle_torrent_file, message_handler, start_handler
from tg_media_bot.admin import dal_handler, go_handler, list_handler, stop_handler, library_handler, library_callback
from tg_media_bot.torrent import _torrent_worker


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set")

    print(f"Cookie: {'loaded ' + COOKIES_FILE if os.path.isfile(COOKIES_FILE) else 'not found ' + COOKIES_FILE}")
    print(f"Cache: {CACHE_FILE} ({len(_cache)} entries)")
    print(f"Torrent dir: {TORRENT_DIR}")
    print(f"qBittorrent: {QB_HOST} (user={QB_USER})")

    if shutil.which("gallery-dl"):
        print("gallery-dl: installed")
    else:
        print("gallery-dl: not installed; TikTok gallery fallback will not work")

    os.makedirs(TORRENT_DIR, exist_ok=True)

    builder = ApplicationBuilder().token(BOT_TOKEN)
    if LOCAL_API_URL:
        print(f"Local Bot API: {LOCAL_API_URL}")
        builder = (
            builder
            .local_mode(True)
            .base_url(f"{LOCAL_API_URL}/bot")
            .base_file_url(f"{LOCAL_API_URL}/file/bot")
        )
    else:
        print("LOCAL_API_URL is not set; using official Telegram Bot API")

    app = builder.build()
    state.init_torrent_queue()

    async def _post_init(application):
        asyncio.create_task(_torrent_worker())

    app.post_init = _post_init
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("dal", dal_handler))
    app.add_handler(CommandHandler("go", go_handler))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CommandHandler("library", library_handler))
    app.add_handler(CallbackQueryHandler(library_callback, pattern="^lib:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, message_handler))

    print("Bot started; listening...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
