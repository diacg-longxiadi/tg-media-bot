def _escape_md(text: str) -> str:
    """跳脫 Markdown v1 特殊字元。"""
    return text.replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")

def _fmt_speed(bps: int) -> str:
    if bps >= 1024 * 1024:
        return f"{bps/1024/1024:.1f} MB/s"
    if bps >= 1024:
        return f"{bps/1024:.1f} KB/s"
    return f"{bps} B/s"
