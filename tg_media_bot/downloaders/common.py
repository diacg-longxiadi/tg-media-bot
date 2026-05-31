import asyncio

async def run_blocking(fn):
    return await asyncio.get_event_loop().run_in_executor(None, fn)
