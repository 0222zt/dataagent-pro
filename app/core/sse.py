"""Server-Sent Events 小工具。

SSE 只需要两个东西：``data: <json>\\n\\n`` 的格式，以及能持续 yield 的生成器。
这里不引入额外依赖。
"""

from __future__ import annotations

import json
from typing import Any


def sse_pack(data: dict[str, Any], event: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {payload}\n\n"


def sse_comment(text: str = "") -> str:
    """注释行，用来保持连接（或声明流已打开）。"""
    return f": {text}\n\n"
