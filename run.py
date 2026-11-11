"""开发启动脚本：``python run.py``。

生产环境建议：

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
"""

from __future__ import annotations

import os
import socket

import uvicorn

from app.core.config import get_settings

HOST = "127.0.0.1"
PORT = int(os.getenv("DATAAGENT_PORT", "8000"))


def port_available(host: str, port: int) -> bool:
    """先试绑一下，把 WinError 10013 翻译成看得懂的话。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    settings = get_settings()
    if not port_available(HOST, PORT):
        raise SystemExit(
            f"端口 {PORT} 已被占用（或落在 Windows 保留端口段里）。\n"
            f"先看看是谁占着：netstat -ano | findstr :{PORT}\n"
            f"或者换个端口启动：set DATAAGENT_PORT=8010 && python run.py"
        )
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        log_level=settings.log_level.lower(),
    )
