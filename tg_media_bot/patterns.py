import re

BILIBILI_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/\S+|"
    r"https?://b23\.tv/\S+",
    re.IGNORECASE,
)
YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?youtube\.com/watch\?[^\s]*v=[\w-]+\S*|"
    r"https?://youtu\.be/[\w-]+\S*|"
    r"https?://(?:www\.)?youtube\.com/shorts/[\w-]+\S*",
    re.IGNORECASE,
)
TIKTOK_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+",
    re.IGNORECASE,
)
TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+/status/\d+\S*",
    re.IGNORECASE,
)
XIAOHONGSHU_RE = re.compile(
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-zA-Z0-9]+|"
    r"https?://xhslink\.com/\S+",
    re.IGNORECASE,
)
DOUYIN_RE = re.compile(
    r"https?://(?:www\.)?douyin\.com/video/\d+|"
    r"https?://v\.douyin\.com/\S+",
    re.IGNORECASE,
)
MAGNET_RE = re.compile(
    r"magnet:\?xt=urn:btih:[a-fA-F0-9]{40,}[^\s]*",
    re.IGNORECASE,
)
GENERIC_URL_RE  = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TIKTOK_PHOTO_RE = re.compile(r"tiktok\.com/.*/photo(?:/|\?)", re.IGNORECASE)

def _parse_tweet_id(url: str) -> str | None:
    m = re.search(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)", url)
    return m.group(1) if m else None

def extract_known_urls(text: str):
    results, seen = [], set()
    for pattern, platform in [
        (BILIBILI_RE, "bilibili"),
        (YOUTUBE_RE,  "youtube"),
        (TIKTOK_RE,   "tiktok"),
        (TWITTER_RE,  "twitter"),
        (XIAOHONGSHU_RE, "xiaohongshu"),
        (DOUYIN_RE,   "douyin"),
    ]:
        for m in pattern.finditer(text):
            if m.group() not in seen:
                results.append((m.group(), platform))
                seen.add(m.group())
    return results

def extract_generic_urls(text: str):
    known = {u for u, _ in extract_known_urls(text)}
    results = []
    for m in GENERIC_URL_RE.finditer(text):
        url = m.group().rstrip(".,;)")
        if url not in known and url not in results:
            results.append(url)
    return results

def extract_magnets(text: str) -> list[str]:
    return [m.group() for m in MAGNET_RE.finditer(text)]
