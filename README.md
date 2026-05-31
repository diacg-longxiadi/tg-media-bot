# tg-media-bot

Telegram media download bot. Send a video/media link, magnet link, or `.torrent` file to the bot, and it downloads the content and sends it back in Telegram.

The Docker Compose setup includes:

- `tg-media-bot`: the Python Telegram bot
- `telegram-bot-api`: local Telegram Bot API server for larger file uploads
- `qbittorrent`: qBittorrent Web API backend for magnet and torrent downloads

## Features

- Download and resend videos from YouTube, Bilibili, TikTok, X/Twitter, and other URLs supported by `yt-dlp`
- Download TikTok photo posts with `gallery-dl`
- Fallback support for direct media URLs and extracted `m3u8` streams
- Cache sent Telegram file IDs to avoid re-uploading the same media
- Accept magnet links and `.torrent` files, then download through qBittorrent
- Works in private chats and groups
  - Private chat: send links directly
  - Group chat: supported platform links are handled directly; mention the bot for generic URLs or magnets

## Requirements

- Docker and Docker Compose
- Telegram bot token from [BotFather](https://t.me/BotFather)
- Telegram API ID and API hash from [my.telegram.org](https://my.telegram.org)

## Quick Start

1. Create a `.env` file:

```env
BOT_TOKEN=123456789:your_bot_token_here
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash_here

# Optional qBittorrent credentials
QB_USER=admin
QB_PASS=adminadmin
```

2. Create local runtime files:

```powershell
New-Item -ItemType File -Force cookies.txt
New-Item -ItemType File -Force cache.json
Set-Content cache.json "{}"
```

3. Start the stack:

```powershell
docker compose up -d --build
```

4. Check logs:

```powershell
docker compose logs -f tg-media-bot
```

## Usage

Send one of these to the bot:

- YouTube, Bilibili, TikTok, X/Twitter, or other media URL
- Magnet link
- `.torrent` file

In a group, mention the bot when sending generic URLs or magnet links:

```text
@your_bot_username https://example.com/video.mp4
```

The bot also supports `/start`. If a cached deep link is opened, it resolves the stored URL and downloads it again.

## Configuration

Environment variables used by the bot:

| Variable | Default | Description |
| --- | --- | --- |
| `BOT_TOKEN` | required | Telegram bot token |
| `LOCAL_API_URL` | empty | Local Telegram Bot API URL. Docker Compose sets this to `http://telegram-bot-api:8081` |
| `COOKIES_FILE` | `/app/cookies.txt` | Optional cookies file for `yt-dlp` |
| `CACHE_FILE` | `/app/cache.json` | Cache file for Telegram file IDs and deep-link URL storage |
| `TORRENT_DIR` | `/downloads` | Directory where torrent downloads are stored |
| `QB_HOST` | `http://qbittorrent:8080` | qBittorrent Web API URL |
| `QB_USER` | `admin` | qBittorrent username |
| `QB_PASS` | `adminadmin` | qBittorrent password |

Docker Compose also requires:

| Variable | Description |
| --- | --- |
| `TELEGRAM_API_ID` | Telegram API ID for the local Bot API server |
| `TELEGRAM_API_HASH` | Telegram API hash for the local Bot API server |

## Cookies

Some platforms may require cookies for age-restricted, private, region-limited, or logged-in content. Put exported cookies into `cookies.txt`; the file is mounted into the container as `/app/cookies.txt`.

If cookies are not needed, an empty `cookies.txt` is fine.

## Docker Services

Useful commands:

```powershell
docker compose ps
docker compose logs -f
docker compose restart tg-media-bot
docker compose down
```

Torrent data is stored in the named Docker volume `torrent-downloads`. qBittorrent config is stored in `qb-config`.

## Local Development

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Run the bot directly:

```powershell
$env:BOT_TOKEN="123456789:your_bot_token_here"
python bot.py
```

Without `LOCAL_API_URL`, the bot uses the official Telegram Bot API, which has smaller upload limits than the local Bot API server.

## Notes

- `ffmpeg` is required for media merging and `m3u8` downloads. The Docker image installs it automatically.
- `nodejs` is installed in the Docker image for `yt-dlp` extractor support.
- `gallery-dl` is included in `requirements.txt` for TikTok photo/gallery fallback.
- Keep `BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, cookies, and qBittorrent credentials private.
