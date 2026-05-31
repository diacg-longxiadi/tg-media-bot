import asyncio

active_tasks: int = 0
torrent_queue: asyncio.Queue | None = None

def init_torrent_queue() -> asyncio.Queue:
    global torrent_queue
    torrent_queue = asyncio.Queue()
    return torrent_queue

def get_torrent_queue() -> asyncio.Queue:
    if torrent_queue is None:
        return init_torrent_queue()
    return torrent_queue
