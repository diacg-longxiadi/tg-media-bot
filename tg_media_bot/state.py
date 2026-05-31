import asyncio
import itertools

active_tasks: int = 0
torrent_queue: asyncio.Queue | None = None

# ── 通用任務追蹤（下載/Torrent） ─────────────────────────────────────────────
_task_counter = itertools.count(1)
_running_tasks: dict[int, dict] = {}  # id -> {type, name, cancel, ...}


def init_torrent_queue() -> asyncio.Queue:
    global torrent_queue
    torrent_queue = asyncio.Queue()
    return torrent_queue


def get_torrent_queue() -> asyncio.Queue:
    if torrent_queue is None:
        return init_torrent_queue()
    return torrent_queue


def register_task(task_type: str, name: str, cancel_fn=None) -> int:
    """註冊一個進行中的任務，回傳 task_id"""
    task_id = next(_task_counter)
    _running_tasks[task_id] = {
        "id": task_id,
        "type": task_type,
        "name": name,
        "cancel": cancel_fn,
    }
    return task_id


def unregister_task(task_id: int):
    _running_tasks.pop(task_id, None)


def get_all_tasks() -> list[dict]:
    """取得所有任務（下載中 + qBittorrent 種子）"""
    return list(_running_tasks.values())


def cancel_task(task_id: int) -> str | None:
    """取消任務，回傳任務名稱或 None（找不到）"""
    task = _running_tasks.get(task_id)
    if not task:
        return None
    name = task["name"]
    cancel = task.get("cancel")
    if cancel:
        try:
            cancel()
        except Exception as e:
            print(f"[state] cancel failed: {e}")
    unregister_task(task_id)
    return name
