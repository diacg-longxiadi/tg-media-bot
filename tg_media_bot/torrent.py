import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse

from . import state
from .config import QB_HOST, QB_PASS, QB_USER
from .utils import _fmt_speed

TORRENT_SLOW_THRESHOLD = 50 * 1024   # 50 KB/s
TORRENT_SLOW_WINDOW    = 60          # 秒


# 公網 tracker 列表（無入站 IP 時用）
_PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.nitrix.me:443/announce",
    "http://tracker.bt4g.com:2095/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.moeking.me:6969/announce",
    "https://opentracker.i2p.rocks:443/announce",
    "http://tracker.renfei.net:8080/announce",
]

class QBittorrentClient:
    """非同步 qBittorrent Web API 封裝。所有 HTTP 在 executor 裡執行，不阻塞 event loop。"""

    def __init__(self, host: str, username: str, password: str):
        self.host     = host.rstrip("/")
        self.username = username
        self.password = password
        self._cookie  = ""

    def _req(self, method: str, path: str, **kwargs):
        import urllib.request
        import urllib.parse
        url = f"{self.host}/api/v2{path}"
        print(f"[qb] {method} {path}  (cookie={bool(self._cookie)}, has_data={'data' in kwargs})")
        headers = {"Cookie": self._cookie, "Referer": self.host}

        if method == "POST":
            data = kwargs.get("data")
            if isinstance(data, dict):
                if kwargs.get("multipart"):
                    boundary = "----FormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
                    body_parts = []
                    for k, v in data.items():
                        if isinstance(v, bytes):
                            body_parts.append(
                                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="file.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode()
                                + v + b"\r\n"
                            )
                        else:
                            body_parts.append(
                                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                            )
                    body_parts.append(f"--{boundary}--\r\n".encode())
                    body = b"".join(body_parts)
                    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
                else:
                    body = urllib.parse.urlencode(data).encode()
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = data or b""
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        else:
            req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[qb] → {r.status} {path}")
            return r.status, r.read().decode("utf-8", errors="ignore"), dict(r.headers)

    async def _call(self, method, path, **kwargs):
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._req(method, path, **kwargs)
        )

    async def login(self):
        status, body, headers = await self._call("POST", "/auth/login",
            data={"username": self.username, "password": self.password})
        if status == 204:
            pass  # 新版 qBittorrent 5.2+ 成功回 204 No Content
        elif body.strip() != "Ok.":
            raise RuntimeError(f"qBittorrent 登入失敗：{body}")
        # set-cookie 可能為大小寫，轉小寫查詢
        raw_headers = {k.lower(): v for k, v in headers.items()}
        cookie_hdr = raw_headers.get("set-cookie", "")
        self._cookie = cookie_hdr.split(";")[0] if cookie_hdr else ""
        print(f"[qb] 登入成功 (status={status}), cookie={self._cookie[:30]}...")

    async def wait_ready(self, retries: int = 30):
        for i in range(retries):
            try:
                await self.login()
                return
            except Exception as e:
                if i == retries - 1:
                    raise RuntimeError(f"qBittorrent 連線失敗：{e}")
                await asyncio.sleep(2)

    async def add_magnet(self, magnet: str, save_path: str) -> str:
        m = re.search(r"btih:([a-fA-F0-9]{40})", magnet, re.IGNORECASE)
        info_hash = m.group(1).lower() if m else ""
        # 補 tracker（尚未包含的才加）
        for tr in _PUBLIC_TRACKERS:
            if tr not in magnet:
                magnet += f"&tr={urllib.parse.quote(tr, safe='')}"
        print(f"[qb] add_magnet: {len(_PUBLIC_TRACKERS)} trackers appended, hash={info_hash[:12]}...")
        await self._call("POST", "/torrents/add", data={
            "urls":               magnet,
            "savepath":           save_path,
            "upload_limit":       1024,
            "ratio_limit":        "0",
            "seeding_time_limit": 0,
        })
        return info_hash

    async def add_torrent_file(self, torrent_bytes: bytes, save_path: str) -> str:
        await self._call("POST", "/torrents/add", multipart=True, data={
            "torrents":           torrent_bytes,
            "savepath":           save_path,
            "upload_limit":       1024,
            "ratio_limit":        "0",
            "seeding_time_limit": 0,
        })
        await asyncio.sleep(2)
        _, body, _ = await self._call("GET", "/torrents/info?sort=added_on&reverse=true&limit=1")
        torrents = json.loads(body)
        return torrents[0]["hash"] if torrents else ""

    async def get_torrent(self, info_hash: str) -> dict | None:
        _, body, _ = await self._call("GET", f"/torrents/info?hashes={info_hash}")
        items = json.loads(body)
        return items[0] if items else None

    async def delete_torrent(self, info_hash: str, delete_files: bool = False):
        await self._call("POST", "/torrents/delete", data={
            "hashes":      info_hash,
            "deleteFiles": "true" if delete_files else "false",
        })


async def dl_torrent(
    source: str,
    out_dir: str,
    status_msg,
) -> list[str]:
    """
    透過 qBittorrent Web API 下載種子。
    上傳限速 1 KB/s、ratio 0、做種時間 0，下載完立即移除任務保留檔案。
    全程 await asyncio.sleep 輪詢，不阻塞 event loop。
    """
    os.makedirs(out_dir, exist_ok=True)

    qb = QBittorrentClient(QB_HOST, QB_USER, QB_PASS)
    await qb.wait_ready()

    if source.startswith("magnet:"):
        info_hash = await qb.add_magnet(source, out_dir)
    else:
        with open(source, "rb") as f:
            torrent_bytes = f.read()
        info_hash = await qb.add_torrent_file(torrent_bytes, out_dir)

    if not info_hash:
        raise RuntimeError("無法取得種子 hash，請確認 qBittorrent 已正常運行")

    print(f"[torrent] hash={info_hash} out={out_dir}")

    slow_since:  float | None = None
    warned_slow: bool         = False
    last_edit:   float        = 0.0
    dot_count:   int          = 0
    META_STATES = {"metaDL", "checkingResumeData", "allocating"}

    while True:
        await asyncio.sleep(4)

        t = await qb.get_torrent(info_hash)
        if t is None:
            raise RuntimeError("找不到種子任務，可能已被刪除")

        state      = t.get("state", "")
        dlspeed    = t.get("dlspeed", 0)
        progress   = t.get("progress", 0.0)
        total      = t.get("size", 0)
        downloaded = int(total * progress)
        peers      = t.get("num_seeds", 0) + t.get("num_leechs", 0)
        eta        = t.get("eta", -1)

        if state in ("error", "missingFiles"):
            raise RuntimeError(f"qBittorrent 回報錯誤狀態：{state}")

        # 完成（進入做種或暫停狀態）→ 刪任務保留檔案
        if state in ("uploading", "stalledUP", "forcedUP", "pausedUP", "queuedUP", "checkingUP"):
            await qb.delete_torrent(info_hash, delete_files=False)
            break

        # Metadata 抓取中
        if state in META_STATES or total == 0:
            dot_count = (dot_count + 1) % 4
            now = time.time()
            if now - last_edit >= 4:
                try:
                    await status_msg.edit_text(
                        f"⏳ 正在獲取種子資訊{'.' * (dot_count + 1)}\n"
                        f"🔗 {peers} 個節點"
                    )
                except Exception:
                    pass
                last_edit = now
            continue

        # 實際下載中
        done_mb  = downloaded / 1024 / 1024
        total_mb = total      / 1024 / 1024
        pct      = progress * 100
        size_str = (f"{done_mb/1024:.2f}/{total_mb/1024:.2f} GB"
                    if total_mb >= 1024 else f"{done_mb:.1f}/{total_mb:.1f} MB")
        progress_str = f"{pct:.1f}% ({size_str})"
        speed_str    = _fmt_speed(dlspeed)
        eta_str      = f"{eta//60}m{eta%60:02d}s" if 0 < eta < 86400 else "–"

        # 死種偵測
        if dlspeed < TORRENT_SLOW_THRESHOLD:
            if slow_since is None:
                slow_since = time.time()
            elif not warned_slow and (time.time() - slow_since) > TORRENT_SLOW_WINDOW:
                warned_slow = True
                try:
                    await status_msg.edit_text(
                        f"⚠️ 速度超過 {TORRENT_SLOW_WINDOW}s 低於 {_fmt_speed(TORRENT_SLOW_THRESHOLD)}\n"
                        f"可能為死種，已降至最低優先繼續等待…\n\n"
                        f"📥 {progress_str}　🐢 {speed_str}　🔗 {peers} 節點"
                    )
                except Exception:
                    pass
                last_edit = time.time()
                continue
        else:
            slow_since  = None
            warned_slow = False

        now = time.time()
        if now - last_edit >= 5:
            icon = "🐢" if warned_slow else "⚡"
            try:
                await status_msg.edit_text(
                    f"📥 種子下載中…\n"
                    f"進度：{progress_str}\n"
                    f"速度：{icon} {speed_str}　ETA：{eta_str}\n"
                    f"🔗 {peers} 節點"
                )
            except Exception:
                pass
            last_edit = now

    files = []
    for root, _, fnames in os.walk(out_dir):
        for fn in fnames:
            if not fn.endswith(".!qB"):
                files.append(os.path.join(root, fn))
    return sorted(files)


# ── 種子佇列 worker ───────────────────────────────────────────────────────────

async def _torrent_worker():
    """低優先種子任務 worker：只在沒有其他下載任務時執行。"""
    while True:
        queue = state.get_torrent_queue()
        item = await queue.get()
        while state.active_tasks > 0:
            await asyncio.sleep(2)
        try:
            await item()
        except Exception as e:
            print(f"[torrent_worker] 任務失敗：{e}")
        finally:
            queue.task_done()


async def enqueue_torrent(coro_fn):
    await state.get_torrent_queue().put(coro_fn)


