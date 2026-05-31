import hashlib
import json

from .config import CACHE_FILE

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(cache: dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[cache] 寫入失敗：{e}")

_cache: dict = _load_cache()

def cache_get(url: str):
    return _cache.get(url)

def cache_set(url: str, value: dict):
    _cache[url] = value
    _save_cache(_cache)

# ── Deep link URL store ────────────────────────────────────────────────────────

def _url_store() -> dict:
    if "__url_store__" not in _cache:
        _cache["__url_store__"] = {}
    return _cache["__url_store__"]

def store_url(url: str) -> str:
    key = hashlib.md5(url.encode()).hexdigest()[:8].upper()
    _url_store()[key] = url
    _save_cache(_cache)
    return key

def retrieve_url(key: str) -> str | None:
    return _url_store().get(key)
